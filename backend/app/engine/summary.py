"""Per-client summary of an analysis, and the comparison with the providers' own
recommendations."""

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from app.collectors import types as k
from app.engine.engine import CommitmentInfo, EngineRecommendation, EngineResult
from app.usage.query import connect
from app.usage.storage import UsageStore

MONTH_FACTOR = 30.4 / 30  # last-30-days totals -> per month
# Services whose usage commitments can cover (coverage and savings-rate denominators).
ELIGIBLE_SERVICES = [
    "Amazon Elastic Compute Cloud - Compute",
    "Amazon Elastic Compute Cloud",
    "Amazon Relational Database Service",
    "Amazon ElastiCache",
    "Amazon OpenSearch Service",
    "Amazon Redshift",
    "Amazon MemoryDB",
    "Amazon DynamoDB",
    "Amazon Elastic Container Service",
    "AWS Lambda",
    "Virtual Machines",
    "SQL Database",
    "SQL Managed Instance",
    "Azure Cosmos DB",
    "Azure App Service",
    "Azure Database for PostgreSQL",
    "Azure Database for MySQL",
    "Azure Cache for Redis",
    "Functions",
    "Azure Container Apps",
    "Azure Dedicated Host",
]
SP_KINDS = {
    k.AWS_SP_COMPUTE,
    k.AWS_SP_EC2,
    k.AWS_SP_SAGEMAKER,
    k.AWS_SP_DATABASE,
    k.AZURE_SP_COMPUTE,
}


def spend_metrics(store: UsageStore, tenant_id: str, as_of: date) -> dict[str, Any]:
    """Last-30-days spend, coverage and commitment utilization per provider, plus the
    savings each commitment delivered."""
    con = connect(store, tenant_id)
    since = as_of - timedelta(days=30)
    params = [since, as_of, ELIGIBLE_SERVICES]
    rows = con.execute(
        """
        SELECT provider,
          sum(billed_cost) FILTER (WHERE pricing_category = 'On-Demand'
                                   AND charge_category = 'Usage') AS on_demand_spend,
          sum(billed_cost) FILTER (WHERE pricing_category = 'Spot') AS spot_spend,
          sum(billed_cost) AS billed,
          sum(effective_cost) AS effective,
          sum(on_demand_equiv_cost) FILTER (WHERE list_contains(?3, service_name)
              AND charge_category = 'Usage' AND pricing_category <> 'Spot'
              AND coalesce(commitment_status, 'Used') = 'Used') AS eligible_od,
          sum(on_demand_equiv_cost) FILTER (WHERE commitment_status = 'Used') AS covered_od,
          sum(effective_cost) FILTER (WHERE (list_contains(?3, service_name)
              AND charge_category = 'Usage' AND pricing_category = 'On-Demand')
              OR commitment_status IN ('Used', 'Unused')) AS eligible_effective,
          sum(effective_cost) FILTER (WHERE commitment_status = 'Used') AS used_effective,
          sum(effective_cost) FILTER (WHERE commitment_status = 'Unused') AS unused_effective
        FROM usage
        WHERE charge_period_start >= ?1 AND charge_period_start < ?2
        GROUP BY provider ORDER BY provider
        """,
        params,
    ).fetchall()
    names = [
        "on_demand_spend",
        "spot_spend",
        "billed",
        "effective",
        "eligible_od",
        "covered_od",
        "eligible_effective",
        "used_effective",
        "unused_effective",
    ]
    providers = {
        r[0]: {n: round((v or 0.0) * MONTH_FACTOR, 2) for n, v in zip(names, r[1:], strict=True)}
        for r in rows
    }
    per_commitment = con.execute(
        """
        SELECT commitment_id,
          sum(coalesce(on_demand_equiv_cost, 0) - effective_cost)
            FILTER (WHERE commitment_status = 'Used')
          - coalesce(sum(effective_cost) FILTER (WHERE commitment_status = 'Unused'), 0)
        FROM usage
        WHERE charge_period_start >= ?1 AND charge_period_start < ?2
          AND commitment_status IN ('Used', 'Unused')
        GROUP BY 1
        """,
        [since, as_of],
    ).fetchall()
    by_service = con.execute(
        """
        SELECT provider, service_name,
          sum(effective_cost) FILTER (WHERE pricing_category = 'On-Demand'
                                      AND charge_category = 'Usage') AS on_demand,
          sum(effective_cost) FILTER (WHERE commitment_status IN ('Used', 'Unused')) AS committed,
          sum(effective_cost) FILTER (WHERE pricing_category = 'Spot') AS spot,
          sum(on_demand_equiv_cost) FILTER (WHERE charge_category = 'Usage'
              AND coalesce(commitment_status, 'Used') = 'Used') AS on_demand_equiv
        FROM usage
        WHERE charge_period_start >= ?1 AND charge_period_start < ?2
          AND list_contains(?3, service_name)
        GROUP BY ALL ORDER BY on_demand_equiv DESC NULLS LAST
        """,
        params,
    ).fetchall()
    return {
        "providers": providers,
        "commitment_savings": {cid: (v or 0.0) * MONTH_FACTOR for cid, v in per_commitment},
        "by_service": [
            {
                "provider": prov,
                "service": svc,
                "on_demand": round((od or 0) * MONTH_FACTOR, 2),
                "committed": round((c or 0) * MONTH_FACTOR, 2),
                "spot": round((sp or 0) * MONTH_FACTOR, 2),
                "on_demand_equiv": round((eq or 0) * MONTH_FACTOR, 2),
            }
            for prov, svc, od, c, sp, eq in by_service
        ],
    }


def _pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 2) if b else None


KIND_LABELS = {
    k.AWS_SP_COMPUTE: "Compute Savings Plan",
    k.AWS_SP_EC2: "EC2 Instance Savings Plan",
    k.AWS_SP_SAGEMAKER: "SageMaker Savings Plan",
    k.AWS_SP_DATABASE: "Database Savings Plan",
    k.AWS_RI: "reserved",
    k.AZURE_SP_COMPUTE: "Azure Savings Plan",
    k.AZURE_RI: "Azure reservation",
}
ACTION_LABELS = {"purchase": "Buy", "renew": "Renew", "exchange": "Exchange into"}
SERVICE_LABELS = {"ec2": "EC2", "rds": "RDS", "elasticache": "ElastiCache"}


def _describe(r: EngineRecommendation) -> str:
    action = ACTION_LABELS.get(r.action, r.action)
    if r.hourly_commitment is not None and r.kind in SP_KINDS:
        what = f"${r.hourly_commitment:,.3f}/hour {KIND_LABELS.get(r.kind, r.kind)}"
    else:
        what = f"{r.quantity:g} × {r.instance_type or r.instance_family} ({KIND_LABELS.get(r.kind, r.kind)})"
    service = SERVICE_LABELS.get(r.service or "", r.service)
    where = ", ".join(x for x in (service, r.region) if x and r.service != "compute")
    return f"{action} {what}{' — ' + where if where else ''}"


def build_summary(
    result: EngineResult,
    commitments: list[CommitmentInfo],
    metrics: dict[str, Any],
    native: list[dict[str, Any]],
    *,
    as_of: date,
    history_days: int,
    risk_profile: str,
    expiring_days: int,
    urgent_days: int,
) -> dict[str, Any]:
    providers = metrics["providers"]
    total = defaultdict(float)
    for p in providers.values():
        for key, v in p.items():
            total[key] += v
    plan = [r for r in result.recommendations if r.plan_rank is not None]
    flags = [r for r in result.recommendations if r.action == "flag"]
    new_savings = sum(r.monthly_savings for r in plan)

    expiring = []
    lost = 0.0
    for c in commitments:
        days_left = (c.end_at.date() - as_of).days
        if 0 < days_left <= expiring_days:
            at_risk = metrics["commitment_savings"].get(c.provider_commitment_id, 0.0)
            lost += at_risk
            expiring.append(
                {
                    "id": c.provider_commitment_id,
                    "kind": c.kind,
                    "instance_type": c.instance_type,
                    "quantity": c.quantity,
                    "hourly_commitment": c.hourly_commitment,
                    "end": c.end_at.date().isoformat(),
                    "days_left": days_left,
                    "urgent": days_left <= urgent_days,
                    "monthly_savings_at_risk": round(at_risk, 2),
                }
            )
    expiring.sort(key=lambda e: e["days_left"])

    eligible_od = total["eligible_od"]
    current_effective = total["eligible_effective"]
    new_effective = current_effective + lost - new_savings
    summary = {
        "as_of": as_of.isoformat(),
        "history_days": history_days,
        "risk_profile": risk_profile,
        "warnings": result.warnings,
        "providers": providers,
        "on_demand_spend_monthly": round(total["on_demand_spend"], 2),
        "on_demand_equiv_monthly": round(eligible_od, 2),
        "spend_by_service": metrics.get("by_service", []),
        "spot_spend_monthly": round(total["spot_spend"], 2),
        "coverage_pct": _pct(total["covered_od"], eligible_od),
        "existing_utilization_pct": _pct(
            total["used_effective"], total["used_effective"] + total["unused_effective"]
        ),
        "expiring_commitments": expiring,
        "underutilized_commitments": [
            {
                "id": r.details.get("commitment_id"),
                "kind": r.kind,
                "instance_type": r.instance_type,
                "status": r.details.get("status"),
                "utilization_pct": r.expected_utilization_pct,
                "unused_monthly": r.details.get("unused_monthly"),
            }
            for r in flags
        ]
        + [
            {
                "id": r.details.get("exchange_from"),
                "kind": r.kind,
                "instance_type": r.details.get("from_instance_type"),
                "status": "stranded (exchange recommended)",
                "utilization_pct": r.details.get("recent_utilization_pct"),
                "unused_monthly": r.details.get("unused_monthly"),
            }
            for r in plan
            if r.action == "exchange"
        ],
        "purchase_plan": [
            {
                "rank": r.plan_rank,
                "action": r.action,
                "description": _describe(r),
                "term_months": r.term_months,
                "payment_option": r.payment_option,
                "upfront_cost": r.upfront_cost,
                "monthly_savings": r.monthly_savings,
                "risk": r.risk,
                "urgent": r.urgent,
            }
            for r in plan
        ],
        "upfront_total": round(sum(r.upfront_cost or 0 for r in plan), 2),
        "projected_monthly_savings": round(new_savings, 2),
        "projected_annual_savings": round(12 * new_savings, 2),
        "expiring_savings_monthly": round(lost, 2),
        "effective_savings_rate_current_pct": _pct(eligible_od - current_effective, eligible_od),
        "effective_savings_rate_new_pct": _pct(eligible_od - new_effective, eligible_od),
        "savings_floor_monthly": result.summary.get("savings_floor_monthly"),
        "below_threshold": [
            {
                "pool": p.pool.label,
                "description": _describe(p.held_back),
                "term_months": p.held_back.term_months,
                "payment_option": p.held_back.payment_option,
                "upfront_cost": p.held_back.upfront_cost,
                "monthly_savings": p.held_back.monthly_savings,
                "risk": p.held_back.risk,
            }
            for p in result.pools
            if p.held_back is not None
        ],
        "skipped_pools": [
            {"pool": p.pool.label, "reason": p.skipped}
            for p in result.pools
            if p.skipped and p.skipped != "no uncovered usage"
        ],
    }
    summary["native_comparison"] = compare_native(result, native)
    return summary


def _native_savings(recs: list[dict[str, Any]]) -> float:
    return sum(float(n.get("estimated_monthly_savings") or 0) for n in recs)


def compare_native(result: EngineResult, native: list[dict[str, Any]]) -> dict[str, Any]:
    """Side-by-side totals per commitment kind, and why the engine differs."""
    plan = [r for r in result.recommendations if r.plan_rank is not None]
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def row(provider: str, kind: str) -> dict[str, Any]:
        return rows.setdefault(
            (provider, kind),
            {
                "provider": provider,
                "kind": kind,
                "native_count": 0,
                "native_options": 0,
                "native_term_months": None,
                "native_payment_option": None,
                "native_hourly_commitment": 0.0,
                "native_quantity": 0.0,
                "native_monthly_savings": 0.0,
                "engine_count": 0,
                "engine_hourly_commitment": 0.0,
                "engine_quantity": 0.0,
                "engine_monthly_savings": 0.0,
            },
        )

    lookbacks = {n.get("lookback_days") for n in native}
    engine_options: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for e in plan:
        r = row(e.provider, e.kind)
        r["engine_count"] += 1
        r["engine_hourly_commitment"] += e.hourly_commitment or 0
        r["engine_quantity"] += e.quantity or 0
        r["engine_monthly_savings"] += e.monthly_savings
        engine_options[(e.provider, e.kind)][(e.term_months, e.payment_option)] += 1

    # Providers return the same opportunity once per term/payment option asked for; those are
    # alternatives, so only one option's recommendations are totalled: the one the engine
    # mostly chose for this kind, else the one with the largest native savings.
    by_option: dict[tuple[str, str], dict[tuple, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for n in native:
        option = (n.get("term_months"), n.get("payment_option"), n.get("lookback_days"))
        by_option[(n["provider"], n["kind"])][option].append(n)
    for key, options in by_option.items():
        preferred = [o for o, _ in engine_options[key].most_common()]
        chosen = next(
            (o for p in preferred for o in options if o[:2] == p),
            max(options, key=lambda o, opts=options: _native_savings(opts[o])),
        )
        r = row(*key)
        r["native_options"] = len(options)
        r["native_term_months"], r["native_payment_option"] = chosen[0], chosen[1]
        for n in options[chosen]:
            r["native_count"] += 1
            r["native_hourly_commitment"] += float(n.get("hourly_commitment") or 0)
            r["native_quantity"] += float(n.get("quantity") or 0)
            r["native_monthly_savings"] += float(n.get("estimated_monthly_savings") or 0)
    for r in rows.values():
        for key in list(r):
            if isinstance(r[key], float):
                r[key] = round(r[key], 3)

    reasons: list[str] = []
    if native:
        days = ", ".join(str(d) for d in sorted(x for x in lookbacks if x))
        reasons.append(
            f"The providers' recommendations average the last {days} days; the engine "
            "simulates each hour of up to 90 days and sizes only on data after step changes."
        )
    skipped = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for p in result.pools:
        if not p.skipped or p.skipped in ("no uncovered usage", "no usage"):
            continue
        reason = p.skipped.split(":")[0].split(" (")[0]
        skipped[reason] += 1
        if len(examples[reason]) < 3:
            examples[reason].append(p.pool.label)
    for reason, n in skipped.most_common():
        reasons.append(
            f"Not committed ({reason}): {n} pool(s), e.g. {'; '.join(examples[reason])}."
        )
    if any(r.action == "exchange" for r in result.recommendations):
        reasons.append(
            "Stranded reservations are exchanged into the new usage before buying "
            "more; native recommendations would buy on top of them."
        )
    if any(r.action == "renew" for r in plan):
        reasons.append(
            "Commitments ending within 90 days are treated as ending, so their "
            "usage is included and renewals are recommended."
        )
    for (provider, kind), r in rows.items():
        if kind not in SP_KINDS or r["native_hourly_commitment"] <= r["engine_hourly_commitment"]:
            continue
        name = f"{provider.upper()} savings plan"
        if not r["engine_hourly_commitment"]:
            why = next(
                (
                    p.skipped
                    for p in result.pools
                    if p.pool.kind == kind and p.pool.provider == provider and p.skipped
                ),
                "no steady floor",
            )
            reasons.append(
                f"{name}: none recommended ({why}); the native tool suggests "
                f"${r['native_hourly_commitment']:,.3f}/hour."
            )
        else:
            reasons.append(
                f"{name}: the engine commits ${r['engine_hourly_commitment']:,.3f}/hour vs "
                f"${r['native_hourly_commitment']:,.3f}/hour native, because reservations are "
                "applied first and only the steady floor of what remains is committed."
            )
    return {
        "by_kind": sorted(rows.values(), key=lambda r: (r["provider"], r["kind"])),
        "explanations": reasons,
    }
