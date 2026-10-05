import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import AnalysisRun, CloudConnection, Recommendation, Tenant
from app.services.analysis import usage_as_of
from app.usage.query import connect
from app.usage.storage import UsageStore

router = APIRouter(tags=["tenants"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/tenants")
def list_tenants(session: SessionDep) -> list[dict[str, Any]]:
    out = []
    for t in session.scalars(select(Tenant).order_by(Tenant.name)):
        conns = session.scalars(
            select(CloudConnection).where(CloudConnection.tenant_id == t.id)
        ).all()
        run = session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.tenant_id == t.id, AnalysisRun.status == "succeeded")
            .order_by(AnalysisRun.finished_at.desc())
            .limit(1)
        ).first()
        s = run.summary if run else {}
        out.append(
            {
                "id": str(t.id),
                "name": t.name,
                "slug": t.slug,
                "risk_profile": t.risk_profile,
                "connections": [
                    {
                        "provider": c.provider,
                        "name": c.name,
                        "agreement_type": c.azure_agreement_type,
                    }
                    for c in conns
                ],
                "latest_run": None
                if run is None
                else {
                    "id": str(run.id),
                    "finished_at": run.finished_at,
                    "risk_profile": run.risk_profile,
                    "history_days": s.get("history_days"),
                    "on_demand_spend_monthly": s.get("on_demand_spend_monthly"),
                    "projected_monthly_savings": s.get("projected_monthly_savings"),
                    "effective_savings_rate_current_pct": s.get(
                        "effective_savings_rate_current_pct"
                    ),
                    "effective_savings_rate_new_pct": s.get("effective_savings_rate_new_pct"),
                    "warnings": s.get("warnings", []),
                },
            }
        )
    return out


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    for t in list_tenants(session):
        if t["id"] == str(tenant_id):
            return t
    raise HTTPException(404, "tenant not found")


@router.get("/tenants/{tenant_id}/usage/daily")
def daily_usage(tenant_id: uuid.UUID, session: SessionDep, days: int = 90) -> dict[str, Any]:
    """Daily cost of commitment-eligible usage split by how it was paid for (last `days`)."""
    if session.get(Tenant, tenant_id) is None:
        raise HTTPException(404, "tenant not found")
    from app.engine.summary import ELIGIBLE_SERVICES

    store = UsageStore(get_settings().usage_storage_root)
    as_of = usage_as_of(store, str(tenant_id))
    if as_of is None:
        return {"as_of": None, "days": []}
    rows = (
        connect(store, str(tenant_id))
        .execute(
            """
        SELECT CAST(charge_period_start AS DATE) AS day,
          sum(effective_cost) FILTER (WHERE pricing_category = 'On-Demand'
                                      AND charge_category = 'Usage') AS on_demand,
          sum(effective_cost) FILTER (WHERE commitment_status = 'Used') AS committed,
          sum(effective_cost) FILTER (WHERE commitment_status = 'Unused') AS unused,
          sum(effective_cost) FILTER (WHERE pricing_category = 'Spot') AS spot,
          sum(on_demand_equiv_cost) FILTER (WHERE commitment_status = 'Used') AS covered_od
        FROM usage
        WHERE charge_period_start >= ?1 AND charge_period_start < ?2
          AND (list_contains(?3, service_name) OR commitment_status = 'Unused')
        GROUP BY 1 ORDER BY 1
        """,
            [as_of - timedelta(days=days), as_of, ELIGIBLE_SERVICES],
        )
        .fetchall()
    )
    return {
        "as_of": as_of.isoformat(),
        "days": [
            {
                "date": d.isoformat(),
                "on_demand": round(o or 0, 2),
                "committed": round(c or 0, 2),
                "unused": round(u or 0, 2),
                "spot": round(s or 0, 2),
                "covered_on_demand_equiv": round(cod or 0, 2),
            }
            for d, o, c, u, s, cod in rows
        ],
    }


@router.get("/tenants/{tenant_id}/commitments")
def list_commitments(tenant_id: uuid.UUID, session: SessionDep, days: int = 90):
    """Existing commitments with daily utilization for the last `days`."""
    from app.models import Commitment
    from app.models.tables import CommitmentUtilization

    if session.get(Tenant, tenant_id) is None:
        raise HTTPException(404, "tenant not found")
    rows = session.scalars(
        select(Commitment).where(Commitment.tenant_id == tenant_id).order_by(Commitment.end_at)
    ).all()
    out = []
    for c in rows:
        util = session.scalars(
            select(CommitmentUtilization)
            .where(CommitmentUtilization.commitment_id == c.id)
            .order_by(CommitmentUtilization.date.desc())
            .limit(days)
        ).all()[::-1]

        def num(v):
            return float(v) if v is not None else None

        recent = [float(u.utilization_pct) for u in util[-14:]]
        out.append(
            {
                "id": str(c.id),
                "provider_commitment_id": c.provider_commitment_id,
                "provider": c.provider,
                "kind": c.kind,
                "service": c.service,
                "region": c.region,
                "instance_type": c.instance_type,
                "instance_family": c.instance_family,
                "quantity": c.quantity,
                "hourly_commitment": num(c.hourly_commitment),
                "amortized_hourly_cost": num(c.amortized_hourly_cost),
                "term_months": c.term_months,
                "payment_option": c.payment_option,
                "start_at": c.start_at,
                "end_at": c.end_at,
                "attributes": c.attributes,
                "recent_utilization_pct": round(sum(recent) / len(recent), 2) if recent else None,
                "utilization": [
                    {
                        "date": u.date.isoformat(),
                        "utilization_pct": float(u.utilization_pct),
                        "unused_cost": num(u.unused_cost),
                    }
                    for u in util
                ],
            }
        )
    return out


class StatusUpdate(BaseModel):
    status: Literal["open", "accepted", "dismissed"]


@router.patch("/recommendations/{rec_id}")
def update_recommendation(rec_id: uuid.UUID, body: StatusUpdate, session: SessionDep):
    rec = session.get(Recommendation, rec_id)
    if rec is None:
        raise HTTPException(404, "recommendation not found")
    rec.status = body.status
    session.commit()
    return {"id": str(rec.id), "status": rec.status}
