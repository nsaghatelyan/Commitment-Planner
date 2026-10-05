import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AnalysisRun, Recommendation, Tenant

router = APIRouter(prefix="/tenants/{tenant_id}/analysis-runs", tags=["analysis"])
SessionDep = Annotated[Session, Depends(get_session)]


class AnalysisRequest(BaseModel):
    risk_profile: Literal["conservative", "balanced", "aggressive"] | None = None
    # Run in the request instead of queueing it for a worker (small tenants, tests).
    wait: bool = False


def _run_out(run: AnalysisRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "status": run.status,
        "risk_profile": run.risk_profile,
        "engine_version": run.engine_version,
        "parameters": run.parameters,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error": run.error,
        "summary": run.summary,
    }


def _rec_out(r: Recommendation) -> dict[str, Any]:
    def num(v):
        return float(v) if v is not None else None

    return {
        "id": str(r.id),
        "source": r.source,
        "action": r.action,
        "plan_rank": r.plan_rank,
        "provider": r.provider,
        "kind": r.kind,
        "scope": r.scope,
        "service": r.service,
        "region": r.region,
        "instance_family": r.instance_family,
        "instance_type": r.instance_type,
        "term_months": r.term_months,
        "payment_option": r.payment_option,
        "hourly_commitment": num(r.hourly_commitment),
        "quantity": num(r.quantity),
        "upfront_cost": num(r.upfront_cost),
        "monthly_cost_after": num(r.monthly_cost_after),
        "monthly_savings": num(r.monthly_savings),
        "savings_pct": num(r.savings_pct),
        "expected_utilization_pct": num(r.expected_utilization_pct),
        "breakeven_month": num(r.breakeven_month),
        "risk": r.risk,
        "urgent": r.urgent,
        "rationale": r.rationale,
        "status": r.status,
        "details": r.details,
    }


def _tenant(session: Session, tenant_id: uuid.UUID) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "tenant not found")
    return tenant


@router.post("", status_code=202)
async def start_analysis(
    tenant_id: uuid.UUID, body: AnalysisRequest, session: SessionDep
) -> dict[str, Any]:
    tenant = _tenant(session, tenant_id)
    if body.wait:
        from app.services.analysis import run_analysis

        run = run_analysis(session, tenant.id, risk_profile=body.risk_profile)
        return _run_out(run)
    run = AnalysisRun(
        tenant_id=tenant.id, status="pending", risk_profile=body.risk_profile, summary={}
    )
    session.add(run)
    session.commit()
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.config import get_settings

    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await redis.enqueue_job("run_analysis", str(tenant.id), str(run.id), body.risk_profile)
    finally:
        await redis.aclose()
    return _run_out(run)


@router.get("/latest")
def latest_analysis(tenant_id: uuid.UUID, session: SessionDep):
    _tenant(session, tenant_id)
    run = session.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.tenant_id == tenant_id, AnalysisRun.status == "succeeded")
        .order_by(AnalysisRun.finished_at.desc())
        .limit(1)
    ).first()
    if run is None:
        raise HTTPException(404, "no completed analysis")
    return _run_out(run)


@router.get("/{run_id}")
def get_analysis(tenant_id: uuid.UUID, run_id: uuid.UUID, session: SessionDep):
    run = session.get(AnalysisRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "analysis run not found")
    return _run_out(run)


@router.get("/{run_id}/recommendations")
def list_recommendations(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDep,
    source: Literal["engine", "native"] | None = None,
) -> list[dict[str, Any]]:
    run = session.get(AnalysisRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "analysis run not found")
    stmt = select(Recommendation).where(Recommendation.analysis_run_id == run_id)
    if source:
        stmt = stmt.where(Recommendation.source == source)
    recs = session.scalars(stmt).all()
    recs = sorted(
        recs,
        key=lambda r: (
            r.source != "engine",
            r.plan_rank is None,
            r.plan_rank or 0,
            -float(r.monthly_savings),
        ),
    )
    return [_rec_out(r) for r in recs]
