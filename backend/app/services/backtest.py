"""Backtest: size the plan on data up to `holdout_days` before the end, then replay it over
the held-out days of actual usage and compare realized utilization and savings with the
projection."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.engine.config import EngineConfig
from app.engine.data import ExpiringCommitment, load_usage
from app.engine.engine import CommitmentInfo, Engine
from app.engine.prices import PriceBook
from app.services.analysis import analyze
from app.usage.storage import UsageStore


def backtest(
    store: UsageStore,
    tenant_id: str,
    commitments: list[CommitmentInfo],
    price_rows: list[dict[str, Any]],
    config: EngineConfig,
    as_of: date,
    holdout_days: int = 30,
) -> dict[str, Any]:
    train_as_of = as_of - timedelta(days=holdout_days)
    # Utilization history must not leak the holdout period either.
    train_commitments = [
        CommitmentInfo(
            **{**c.__dict__, "utilization": [u for u in c.utilization if u[0] < train_as_of]}
        )
        for c in commitments
    ]
    train = analyze(store, tenant_id, train_commitments, price_rows, config, train_as_of, [])
    if train.data.history_days < config.min_history_days:
        return {
            "available": False,
            "reason": f"needs at least {config.min_history_days} days of history before "
            f"the {holdout_days}-day holdout",
        }
    t0 = datetime(train_as_of.year, train_as_of.month, train_as_of.day, tzinfo=UTC)
    expiring = [
        ExpiringCommitment(c.provider_commitment_id, c.cover_type)
        for c in commitments
        if t0 < c.end_at <= t0 + timedelta(days=config.expiring_days)
    ]
    test = load_usage(store, tenant_id, expiring, holdout_days, as_of)
    rows = Engine(test, PriceBook(price_rows), commitments, config).replay(
        train.result.recommendations
    )
    projected = sum(r["projected_monthly_savings"] for r in rows)
    realized = sum(r["realized_monthly_savings"] for r in rows)
    cost = sum(r["commitment_monthly_cost"] for r in rows) or 1.0

    def weighted(key: str) -> float:
        return round(sum(r[key] * r["commitment_monthly_cost"] for r in rows) / cost, 2)

    return {
        "available": True,
        "train_until": train_as_of.isoformat(),
        "holdout_days": holdout_days,
        "risk_profile": config.risk_profile,
        "recommendations": rows,
        "projected_monthly_savings": round(projected, 2),
        "realized_monthly_savings": round(realized, 2),
        "savings_accuracy_pct": round(100 * realized / projected, 1) if projected else None,
        "projected_utilization_pct": weighted("projected_utilization_pct"),
        "realized_utilization_pct": weighted("realized_utilization_pct"),
    }
