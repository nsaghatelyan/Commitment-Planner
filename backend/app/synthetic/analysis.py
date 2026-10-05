"""Run the engine straight on a synthetic tenant (no database): for tests and demos."""

from dataclasses import asdict

import pyarrow.compute as pc

from app.collectors.aws.commitments import amortized_hourly
from app.engine.config import EngineConfig
from app.engine.engine import CommitmentInfo
from app.services.analysis import Analysis, analyze
from app.synthetic.generator import SyntheticTenant
from app.usage.storage import UsageStore


def commitment_infos(tenant: SyntheticTenant) -> list[CommitmentInfo]:
    util: dict[str, list] = {}
    for u in tenant.utilization:
        util.setdefault(u.provider_commitment_id, []).append((u.date, float(u.utilization_pct)))
    return [
        CommitmentInfo(
            provider_commitment_id=c.provider_commitment_id,
            provider=c.kind.split("_", 1)[0],
            kind=c.kind,
            start_at=c.start_at,
            end_at=c.end_at,
            term_months=c.term_months,
            region=c.region,
            service=c.service,
            instance_type=c.instance_type,
            instance_family=c.instance_family,
            quantity=c.quantity,
            hourly_commitment=float(c.hourly_commitment) if c.hourly_commitment else None,
            amortized_hourly=float(amortized_hourly(c) or 0),
            payment_option=c.payment_option,
            attributes=c.attributes,
            utilization=sorted(util.get(c.provider_commitment_id, [])),
        )
        for c in tenant.commitments
    ]


def price_rows(tenant: SyntheticTenant) -> list[dict]:
    return [asdict(p) for p in tenant.prices]


def write_usage(tenant: SyntheticTenant, store: UsageStore) -> None:
    for provider in ("aws", "azure"):
        part = tenant.usage.filter(pc.equal(tenant.usage.column("provider"), provider))
        store.write(tenant.tenant_id, provider, part)


def analyze_synthetic(
    tenant: SyntheticTenant, store: UsageStore, risk_profile: str = "balanced"
) -> Analysis:
    if not store.dates(tenant.tenant_id, "aws") and not store.dates(tenant.tenant_id, "azure"):
        write_usage(tenant, store)
    native = [
        {**asdict(n), "hourly_commitment": n.hourly_commitment, "quantity": n.quantity}
        for n in tenant.native_recommendations
    ]
    return analyze(
        store,
        tenant.tenant_id,
        commitment_infos(tenant),
        price_rows(tenant),
        EngineConfig(risk_profile=risk_profile),
        tenant.end,
        native,
    )
