"""Run the recommendation engine for a tenant and store the results."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow.fs as pafs
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.engine import ENGINE_VERSION
from app.engine.config import RISK_PROFILES, EngineConfig
from app.engine.data import ExpiringCommitment, UsageData, data_bounds, load_usage
from app.engine.engine import CommitmentInfo, EngineResult, run_engine
from app.engine.prices import PriceBook
from app.engine.summary import build_summary, spend_metrics
from app.models import AnalysisRun, Commitment, Price, Recommendation, Tenant
from app.models.tables import CommitmentUtilization
from app.timeutil import utc_now
from app.usage.query import connect
from app.usage.storage import UsageStore

log = logging.getLogger(__name__)


@dataclass
class Analysis:
    result: EngineResult
    summary: dict[str, Any]
    data: UsageData
    native: list[dict[str, Any]]


def usage_as_of(store: UsageStore, tenant_id: str) -> date | None:
    try:
        return data_bounds(connect(store, tenant_id))[1]
    except Exception:  # noqa: BLE001 - no usage files for this tenant yet
        return None


def usage_regions(store: UsageStore, tenant_id: str) -> list[str]:
    try:
        rows = connect(store, tenant_id).execute(
            "SELECT DISTINCT region FROM usage WHERE region IS NOT NULL"
        )
        return [r[0] for r in rows.fetchall()]
    except Exception:  # noqa: BLE001
        return []


def load_native(store: UsageStore, tenant_id: str) -> list[dict[str, Any]]:
    """Latest saved native recommendations per provider (written by collection or seeding)."""
    base = store.root.rsplit("/", 1)[0]
    out: list[dict[str, Any]] = []
    for provider in ("aws", "azure"):
        path = f"{base}/native_recommendations/tenant={tenant_id}/provider={provider}"
        infos = store.fs.get_file_info(pafs.FileSelector(path, allow_not_found=True))
        files = sorted(i.path for i in infos if i.path.endswith(".json"))
        if files:
            with store.fs.open_input_stream(files[-1]) as f:
                out += json.loads(f.read())
    return out


def analyze(
    store: UsageStore,
    tenant_id: str,
    commitments: list[CommitmentInfo],
    price_rows: list[dict[str, Any]],
    config: EngineConfig,
    as_of: date | None = None,
    native: list[dict[str, Any]] | None = None,
) -> Analysis:
    """The engine end to end, without the database (inputs passed in)."""
    as_of = as_of or usage_as_of(store, tenant_id) or utc_now().date()
    as_of_dt = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)
    horizon = as_of_dt + timedelta(days=config.expiring_days)
    expiring = [
        ExpiringCommitment(c.provider_commitment_id, c.cover_type)
        for c in commitments
        if as_of_dt < c.end_at <= horizon
    ]
    data = load_usage(store, tenant_id, expiring, config.history_days, as_of)
    result = run_engine(data, PriceBook(price_rows), commitments, config)
    native = native if native is not None else load_native(store, tenant_id)
    metrics = (
        spend_metrics(store, tenant_id, as_of)
        if data.first_day
        else {"providers": {}, "commitment_savings": {}}
    )
    summary = build_summary(
        result,
        commitments,
        metrics,
        native,
        as_of=as_of,
        history_days=data.history_days,
        risk_profile="conservative"
        if data.history_days < config.min_history_days
        else config.risk_profile,
        expiring_days=config.expiring_days,
        urgent_days=config.urgent_days,
    )
    return Analysis(result, summary, data, native)


def commitments_for(session: Session, tenant_id: uuid.UUID) -> list[CommitmentInfo]:
    rows = session.scalars(select(Commitment).where(Commitment.tenant_id == tenant_id)).all()
    util: dict[uuid.UUID, list[tuple[date, float]]] = {}
    if rows:
        since = utc_now().date() - timedelta(days=400)
        for u in session.scalars(
            select(CommitmentUtilization)
            .where(CommitmentUtilization.commitment_id.in_([c.id for c in rows]))
            .where(CommitmentUtilization.date >= since)
            .order_by(CommitmentUtilization.date)
        ):
            util.setdefault(u.commitment_id, []).append((u.date, float(u.utilization_pct)))

    def f(v: Decimal | None) -> float | None:
        return float(v) if v is not None else None

    return [
        CommitmentInfo(
            provider_commitment_id=c.provider_commitment_id,
            provider=c.provider,
            kind=c.kind,
            start_at=c.start_at,
            end_at=c.end_at,
            term_months=c.term_months,
            region=c.region,
            service=c.service,
            instance_type=c.instance_type,
            instance_family=c.instance_family,
            quantity=c.quantity,
            hourly_commitment=f(c.hourly_commitment),
            amortized_hourly=f(c.amortized_hourly_cost),
            payment_option=c.payment_option,
            attributes=c.attributes or {},
            utilization=util.get(c.id, []),
        )
        for c in rows
    ]


def price_rows_for(session: Session, regions: list[str]) -> list[dict[str, Any]]:
    stmt = select(Price)
    if regions:
        stmt = stmt.where(Price.region.in_(regions))
    return [
        {
            "provider": p.provider,
            "sku_key": p.sku_key,
            "region": p.region,
            "pricing_model": p.pricing_model,
            "term_months": p.term_months,
            "payment_option": p.payment_option,
            "price_per_unit": p.price_per_unit,
            "effective_from": p.effective_from,
            "attributes": p.attributes,
        }
        for p in session.scalars(stmt)
    ]


def _dec(v: float | None, places: str = "0.000001") -> Decimal | None:
    return Decimal(str(v)).quantize(Decimal(places)) if v is not None else None


def run_analysis(
    session: Session,
    tenant_id: Any,
    *,
    analysis_run_id: Any = None,
    risk_profile: str | None = None,
    store: UsageStore | None = None,
    settings: Settings | None = None,
    as_of: date | None = None,
) -> AnalysisRun:
    settings = settings or get_settings()
    store = store or UsageStore(settings.usage_storage_root)
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError(f"tenant {tenant_id} not found")
    profile = risk_profile or tenant.risk_profile or "balanced"
    if profile not in RISK_PROFILES:
        raise ValueError(f"risk_profile must be one of {RISK_PROFILES}")
    config = EngineConfig(risk_profile=profile)
    run = session.get(AnalysisRun, analysis_run_id) if analysis_run_id else None
    if run is None:
        run = AnalysisRun(tenant_id=tenant.id, summary={})
        session.add(run)
    run.status = "running"
    run.risk_profile = profile
    run.lookback_days = config.lookback_days
    run.engine_version = ENGINE_VERSION
    run.parameters = {
        "risk_profile": profile,
        "target_utilization": config.target(profile),
        "lookback_days": config.lookback_days,
        "expiring_days": config.expiring_days,
    }
    run.started_at = utc_now()
    session.commit()
    try:
        tid = str(tenant.id)
        analysis = analyze(
            store,
            tid,
            commitments_for(session, tenant.id),
            price_rows_for(session, usage_regions(store, tid)),
            config,
            as_of,
        )
        for r in analysis.result.recommendations:
            session.add(
                Recommendation(
                    tenant_id=tenant.id,
                    analysis_run_id=run.id,
                    source=r.source,
                    action=r.action,
                    plan_rank=r.plan_rank,
                    provider=r.provider,
                    kind=r.kind,
                    scope=r.scope,
                    service=r.service,
                    region=r.region,
                    instance_family=r.instance_family,
                    instance_type=r.instance_type,
                    term_months=r.term_months,
                    payment_option=r.payment_option,
                    hourly_commitment=_dec(r.hourly_commitment),
                    quantity=_dec(r.quantity, "0.0001"),
                    upfront_cost=_dec(r.upfront_cost),
                    monthly_cost_after=_dec(r.monthly_cost_after),
                    monthly_savings=_dec(r.monthly_savings),
                    savings_pct=_dec(r.savings_pct, "0.0001"),
                    expected_utilization_pct=_dec(r.expected_utilization_pct, "0.0001"),
                    breakeven_month=_dec(r.breakeven_month, "0.01"),
                    risk=r.risk,
                    urgent=r.urgent,
                    rationale=r.rationale,
                    status="open",
                    details=json.loads(json.dumps(r.details, default=str)),
                )
            )
        for n in analysis.native:
            session.add(
                Recommendation(
                    tenant_id=tenant.id,
                    analysis_run_id=run.id,
                    source="native",
                    action="purchase",
                    provider=n["provider"],
                    kind=n["kind"],
                    scope=n.get("scope"),
                    region=n.get("region"),
                    instance_family=n.get("instance_family"),
                    instance_type=n.get("instance_type"),
                    term_months=n["term_months"],
                    payment_option=n.get("payment_option"),
                    hourly_commitment=_dec(_num(n.get("hourly_commitment"))),
                    quantity=_dec(_num(n.get("quantity")), "0.0001"),
                    upfront_cost=_dec(_num(n.get("upfront_cost"))),
                    monthly_savings=_dec(_num(n.get("estimated_monthly_savings")) or 0.0),
                    rationale=(
                        f"{n['provider'].upper()}'s own recommendation "
                        f"({n.get('lookback_days')}-day lookback)."
                    ),
                    status="open",
                    details={"raw": json.loads(json.dumps(n.get("raw") or {}, default=str))},
                )
            )
        run.summary = json.loads(json.dumps(analysis.summary, default=str))
        run.status = "succeeded"
    except Exception as exc:
        log.exception("analysis failed for tenant %s", tenant_id)
        session.rollback()
        run = session.merge(run)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
    run.finished_at = utc_now()
    session.commit()
    return run


def _num(v: Any) -> float | None:
    return float(v) if v not in (None, "") else None
