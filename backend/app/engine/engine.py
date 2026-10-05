"""The recommendation engine.

1. Baseline: hourly on-demand-equivalent usage per commitment pool that existing commitments
   don't cover (usage under commitments ending within the expiry window counts as uncovered,
   but only towards pools of the same commitment type, so renewals keep their type). Spot
   is excluded.
2. Stability: percentiles, trend, weekly/daily patterns and step changes per pool; after a
   step change only later data is used, ramps use the recent floor, new workloads wait.
3. Layering, the way providers apply commitments: reservations first, then EC2 Instance SPs,
   then Compute / Azure savings plans, each sized on what the previous layers leave uncovered.
4. Sizing: simulate every hour for each candidate commitment and pick by risk profile, never
   below the profile's target utilization.
5. Terms: 1y / 3y and payment options from the price table; 1y No Upfront unless the usage is
   long and flat and 3y pays back quickly.
6. Every recommendation carries a plain-language rationale and the series to chart it.
"""

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from app.collectors import types as k
from app.engine import ENGINE_VERSION
from app.engine.config import EngineConfig
from app.engine.data import UsageData, UsageGroup
from app.engine.optimize import (
    HOURS_PER_MONTH,
    Sizing,
    TermOption,
    choose_capacity,
    simulate,
    term_option,
)
from app.engine.pools import (
    LAYER_COMPUTE_SP,
    LAYER_INSTANCE_SP,
    LAYER_RI,
    Pool,
    ri_pool,
    sp_pools,
)
from app.engine.prices import PriceBook
from app.engine.stats import Stability, analyze
from app.pricing.keys import AWS_SERVICES

NO_UPFRONT = {"aws": "no_upfront", "azure": None}


@dataclass
class CommitmentInfo:
    """An existing commitment, as the engine needs it."""

    provider_commitment_id: str
    provider: str
    kind: str
    start_at: datetime
    end_at: datetime
    term_months: int
    region: str | None = None
    service: str | None = None
    instance_type: str | None = None
    instance_family: str | None = None
    quantity: float | None = None
    hourly_commitment: float | None = None
    amortized_hourly: float | None = None
    payment_option: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    # (date, utilization %) daily, oldest first
    utilization: list[tuple[date, float]] = field(default_factory=list)

    @property
    def is_reservation(self) -> bool:
        return self.kind in (k.AWS_RI, k.AZURE_RI)

    @property
    def cover_type(self) -> str:
        return "ri" if self.is_reservation else self.kind

    @property
    def exchangeable(self) -> bool:
        if self.kind == k.AZURE_RI:
            return True
        return self.kind == k.AWS_RI and self.attributes.get("offering_class") == "convertible"


@dataclass
class EngineRecommendation:
    source: str
    action: str  # purchase | renew | exchange | flag
    provider: str
    kind: str
    term_months: int
    payment_option: str | None
    monthly_savings: float
    plan_rank: int | None = None
    scope: str | None = None
    service: str | None = None
    region: str | None = None
    instance_family: str | None = None
    instance_type: str | None = None
    hourly_commitment: float | None = None
    quantity: float | None = None
    upfront_cost: float | None = None
    monthly_cost_after: float | None = None
    savings_pct: float | None = None
    expected_utilization_pct: float | None = None
    breakeven_month: float | None = None
    risk: str | None = None
    urgent: bool = False
    rationale: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PoolReport:
    pool: Pool
    stability: Stability
    skipped: str | None = None
    sizes: dict[str, float] = field(default_factory=dict)  # profile -> capacity (pool units)
    recommendation: EngineRecommendation | None = None


@dataclass
class EngineResult:
    recommendations: list[EngineRecommendation]
    summary: dict[str, Any]
    pools: list[PoolReport]
    warnings: list[str]

    def find(self, **match) -> list[EngineRecommendation]:
        return [
            r for r in self.recommendations if all(getattr(r, a) == v for a, v in match.items())
        ]


# --------------------------------------------------------------------------- helpers


def _round_down(value: float, step: float) -> float:
    return math.floor(value / step + 1e-9) * step if step > 0 else value


def _pool_service(c: "CommitmentInfo") -> str | None:
    """The pool service name a commitment's service corresponds to."""
    if c.provider == "aws":
        return AWS_SERVICES.get(c.service or "", c.service)
    if (c.service or "").replace(" ", "").lower() == "virtualmachines":
        return "Virtual Machines"
    return c.service


def _risk(sizing: Sizing, st: Stability, term: int) -> str:
    level = 0 if sizing.percentile <= 0.12 else 1 if sizing.percentile <= 0.35 else 2
    if term >= 36:
        level += 1
    if st.trend_rel < -0.1 or st.floor_cv > 0.3:
        level += 1
    return ("low", "med", "high")[min(level, 2)]


def _fmt_money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def _day(data: UsageData, index: int) -> date:
    return (data.start + timedelta(days=index)).date()


class Engine:
    def __init__(
        self,
        data: UsageData,
        prices: PriceBook,
        commitments: list[CommitmentInfo],
        config: EngineConfig | None = None,
    ) -> None:
        self.data = data
        self.prices = prices
        self.commitments = commitments
        self.config = config or EngineConfig()
        self.short_history = data.history_days < self.config.min_history_days
        self.profile = "conservative" if self.short_history else self.config.risk_profile
        self.min_settled = 14 if self.short_history else self.config.min_settled_days
        self.as_of = datetime(data.as_of.year, data.as_of.month, data.as_of.day, tzinfo=UTC)
        self.hour0_weekday = data.start.weekday()
        self.lookback_hours = self.config.lookback_days * 24
        self.by_id = {c.provider_commitment_id: c for c in commitments}
        # Remaining on-demand $ per group after the layers applied so far.
        self.residual = {id(g): g.od.copy() for g in data.groups}
        self.reports: list[PoolReport] = []
        self.recommendations: list[EngineRecommendation] = []
        self.warnings: list[str] = []
        self.exchange_units: dict[Pool, tuple[float, CommitmentInfo, float]] = {}

    # ---- top level
    def run(self) -> EngineResult:
        if self.short_history:
            self.warnings.append(
                f"Only {self.data.history_days} days of usage history; at least "
                f"{self.config.min_history_days} are needed for reliable sizing. Only "
                "conservative 1-year No Upfront commitments on flat usage are shown."
            )
        ri_pools = self._group(lambda g: [ri_pool(g)] if g.cover_type in (None, "ri") else [])
        self._plan_exchanges(ri_pools)
        for pool, groups in sorted(ri_pools.items(), key=lambda kv: kv[0].label):
            self._size_pool(pool, groups)
        instance = self._group(
            lambda g: (
                [p for p in sp_pools(g) if p.layer == LAYER_INSTANCE_SP]
                if g.cover_type in (None, "ri", k.AWS_SP_EC2)
                else []
            )
        )
        for pool, groups in sorted(instance.items(), key=lambda kv: kv[0].label):
            self._size_pool(pool, groups)
        compute = self._group(lambda g: [p for p in sp_pools(g) if p.layer == LAYER_COMPUTE_SP])
        for pool, groups in sorted(compute.items(), key=lambda kv: kv[0].label):
            self._size_pool(pool, groups)
        self._flag_existing()
        self._rank()
        return EngineResult(self.recommendations, {}, self.reports, self.warnings)

    def _group(self, assign) -> dict[Pool, list[UsageGroup]]:
        pools: dict[Pool, list[UsageGroup]] = defaultdict(list)
        for g in self.data.groups:
            for pool in assign(g):
                if pool is not None:
                    pools[pool].append(g)
        return pools

    # ---- series and prices
    def _series(self, pool: Pool, groups: list[UsageGroup]) -> np.ndarray:
        total = np.zeros(self.data.hours)
        for g in groups:
            if pool.measure == "od":
                total += self.residual[id(g)]
            else:
                # Reservation pools: scale units by how much of the group is still uncovered.
                share = np.divide(
                    self.residual[id(g)], g.od, out=np.zeros_like(g.od), where=g.od > 0
                )
                total += pool.series(g) * share
        return total

    def _pool_prices(self, pool: Pool, groups: list[UsageGroup]):
        """Spend-weighted discount per (term, payment), od $ per unit and covered spend share."""
        spend: dict[str, float] = defaultdict(float)
        for g in groups:
            spend[g.sku_key] += float(self.residual[id(g)].sum())
        total = sum(spend.values())
        if total <= 0:
            return None
        options: dict[tuple[int, str | None], float] = defaultdict(float)
        upfront: dict[tuple[int, str | None], float] = defaultdict(float)
        priced = 0.0
        regions = {g.region for g in groups}
        for key, s in spend.items():
            for region in regions:
                sku = self.prices.get(pool.provider, key, region)
                if sku is None or not sku.on_demand:
                    continue
                found = False
                for (model, term, payment), rate in sku.commitments.items():
                    if model != pool.price_model:
                        continue
                    options[(term, payment)] += s * (1 - rate.price / sku.on_demand)
                    upfront[(term, payment)] += s * rate.upfront_share
                    found = True
                if found:
                    priced += s
                    break
        if priced < 0.5 * total:
            return None
        discounts = {tp: v / priced for tp, v in options.items()}
        shares = {tp: upfront[tp] / priced for tp in options}
        return discounts, shares, priced / total

    # ---- one pool
    def _size_pool(self, pool: Pool, groups: list[UsageGroup]) -> None:
        series = self._series(pool, groups)
        st = analyze(
            series, self.hour0_weekday, self.config.lookback_days, self.config.step_min_change
        )
        report = PoolReport(pool, st)
        self.reports.append(report)
        if series.sum() <= 0:
            report.skipped = "no uncovered usage"
            return
        reason = self._gate(st)
        if reason:
            report.skipped = reason
            return
        priced = self._pool_prices(pool, groups)
        if priced is None:
            report.skipped = "no commitment prices for this usage"
            return
        discounts, shares, _ = priced
        base_tp = (12, NO_UPFRONT[pool.provider])
        if base_tp not in discounts:
            base_tp = min(discounts, key=lambda tp: (tp[0], tp[1] or ""))
        sizing_series = series[st.sizing_start_day * 24 :]
        od_series = self._od_series(pool, groups)[st.sizing_start_day * 24 :]
        od_per_unit = float(od_series.sum() / sizing_series.sum()) if sizing_series.sum() else 0
        if od_per_unit <= 0:
            report.skipped = "no on-demand cost"
            return
        step = self._step(pool, groups, sizing_series)
        for profile in ("conservative", "balanced", "aggressive"):
            s = self._choose(sizing_series, discounts[base_tp], profile, step, st)
            report.sizes[profile] = s.capacity if s else 0.0
        sizing = self._choose(sizing_series, discounts[base_tp], self.profile, step, st)
        if sizing is None:
            report.skipped = self._no_floor_reason(st)
            return

        # Exchanges of stranded reservations cover part of this pool first.
        exchanged_units = 0.0
        if pool in self.exchange_units:
            exchanged_units = self._apply_exchange(
                pool, sizing, od_per_unit, discounts, base_tp, sizing_series, step, groups
            )
        capacity_to_buy = max(0.0, sizing.capacity - exchanged_units)
        self._consume(pool, groups, sizing.capacity)

        if pool.measure == "od":
            # Savings plans: the commitment is in $/hour after discount.
            buy = _round_down(capacity_to_buy * (1 - discounts[base_tp]), 0.001)
            capacity_to_buy = buy / (1 - discounts[base_tp]) if buy else 0.0
        if capacity_to_buy <= 0:
            report.skipped = "covered by exchanging a stranded reservation"
            return
        purchase_util, purchase_covered = self._marginal(
            sizing_series, exchanged_units, capacity_to_buy
        )
        opts = {
            tp: term_option(capacity_to_buy, purchase_covered, od_per_unit, d, shares[tp], *tp)
            for tp, d in discounts.items()
        }
        chosen_tp = self._choose_term(
            st, opts, base_tp, od_per_unit, purchase_covered, capacity_to_buy, discounts
        )
        opt = opts[chosen_tp]
        if opt.monthly_savings < self.config.min_monthly_savings:
            report.skipped = f"saves less than ${self.config.min_monthly_savings:.0f}/month"
            return
        rec = self._recommendation(
            pool,
            groups,
            st,
            sizing,
            opt,
            opts,
            capacity_to_buy,
            purchase_util,
            od_per_unit,
            sizing_series,
            exchanged_units,
        )
        report.recommendation = rec
        self.recommendations.append(rec)

    def _choose(self, series, discount, profile, step, st: Stability) -> Sizing | None:
        """Sizing for a profile; a ramping workload commits only to its recent floor (P10 of
        the last 30 days) unless the profile is aggressive."""
        sizing = choose_capacity(series, discount, profile, self.config.target(profile), step)
        if sizing is None or st.sizing_reason != "ramp" or profile == "aggressive":
            return sizing
        floor = _round_down(st.percentiles[10], step) if step else st.percentiles[10]
        if sizing.capacity <= floor or floor <= 0:
            return sizing if floor > 0 else None
        util, covered = simulate(series, floor)
        return Sizing(
            floor, min(util, 1.0), covered / series.mean(), covered, float((series < floor).mean())
        )

    def _gate(self, st: Stability) -> str | None:
        if st.first_active_day is None:
            return "no usage"
        if st.decommissioned:
            return "usage stopped (decommissioned or migrated away)"
        first_data_day = (self.data.first_day - self.data.start.date()).days
        if self.short_history and st.first_active_day > first_data_day + 1:
            return "too new for the available history: not present for the whole period"
        if st.active_days < self.min_settled:
            return f"too new: running for {st.active_days} days, waiting for it to settle"
        if st.days - st.sizing_start_day < self.min_settled and st.sizing_reason == "step":
            return (
                f"changed {st.days - st.sizing_start_day} days ago "
                f"({st.step_change:+.0%}); waiting for it to settle"
            )
        return None

    def _no_floor_reason(self, st: Stability) -> str:
        if st.business_hours_ratio and st.business_hours_ratio > 3:
            return "no steady floor (business-hours usage)"
        if st.night_ratio and st.night_ratio > 3:
            return "no steady floor (nightly batch)"
        if st.weekend_ratio is not None and st.weekend_ratio < 0.3:
            return "no steady floor (weekday-only usage)"
        return "no steady floor at the target utilization"

    def _od_series(self, pool: Pool, groups: list[UsageGroup]) -> np.ndarray:
        total = np.zeros(self.data.hours)
        for g in groups:
            total += self.residual[id(g)]
        return total

    def _step(self, pool: Pool, groups: list[UsageGroup], series: np.ndarray) -> float:
        """Granularity of a purchase: whole instances of the most used type, or $0.001/h."""
        if pool.measure == "od":
            return 0.0
        if pool.measure == "qty":
            return 1.0
        main = max(groups, key=lambda g: float(pool.series(g)[-len(series) :].sum()))
        return main.units_per_instance or 1.0

    def _consume(self, pool: Pool, groups: list[UsageGroup], capacity: float) -> None:
        """Remove what the new commitment covers from the residual on-demand usage."""
        series = self._series(pool, groups)
        frac = np.divide(
            np.minimum(series, capacity), series, out=np.zeros_like(series), where=series > 0
        )
        for g in groups:
            self.residual[id(g)] = self.residual[id(g)] * (1 - frac)

    @staticmethod
    def _marginal(series, base, extra) -> tuple[float, float]:
        """Utilization and covered units/hour of `extra` capacity stacked on `base`."""
        if extra <= 0:
            return 0.0, 0.0
        covered = np.clip(series - base, 0, extra)
        return float(covered.sum() / (extra * len(series))), float(covered.mean())

    def _choose_term(self, st, opts, base_tp, od_per_unit, covered, capacity, discounts):
        """1y No Upfront unless usage is long and flat and 3 years pays back quickly."""
        if self.short_history or self.profile == "conservative":
            return base_tp
        three = [tp for tp in opts if tp[0] == 36 and tp[1] == base_tp[1]]
        if not three or st.settled_days < 90 or abs(st.trend_rel) > 0.1 or st.floor_cv > 0.05:
            return base_tp
        # Pay-back of a 3-year commitment paid fully upfront, in months.
        d3 = discounts[three[0]]
        payback = term_option(capacity, covered, od_per_unit, d3, 1.0, 36, "all_upfront")
        return three[0] if payback.breakeven_month <= 15 else base_tp

    # ---- recommendation building
    def _recommendation(
        self,
        pool,
        groups,
        st,
        sizing,
        opt: TermOption,
        opts,
        capacity_to_buy,
        util,
        od_per_unit,
        series,
        exchanged_units,
    ) -> EngineRecommendation:
        main = max(
            groups,
            key=lambda g: (
                float(self._series(pool, [g])[-len(series) :].sum())
                if pool.measure != "od"
                else float(g.od[-len(series) :].sum())
            ),
        )
        expiring = sorted(
            {
                self.by_id[g.cover].provider_commitment_id: self.by_id[g.cover]
                for g in groups
                if g.cover in self.by_id
            }.values(),
            key=lambda c: c.end_at,
        )
        urgent = any(
            c.end_at <= self.as_of + timedelta(days=self.config.urgent_days) for c in expiring
        )
        rec = EngineRecommendation(
            source="engine",
            action="renew" if expiring else "purchase",
            provider=pool.provider,
            kind=pool.kind,
            term_months=opt.term_months,
            payment_option=opt.payment_option,
            monthly_savings=round(opt.monthly_savings, 2),
            scope="organization"
            if pool.kind == k.AWS_SP_COMPUTE
            else ("Shared" if pool.provider == "azure" else "Region"),
            service=pool.service,
            region=pool.region,
            instance_family=pool.family,
            upfront_cost=round(opt.upfront, 2),
            monthly_cost_after=round(opt.monthly_cost_after, 2),
            savings_pct=round(100 * opt.savings_pct, 2),
            expected_utilization_pct=round(100 * min(util, 1.0), 2),
            breakeven_month=round(opt.breakeven_month, 2),
            risk=_risk(sizing, st, opt.term_months),
            urgent=urgent,
        )
        if pool.measure == "od":
            rec.hourly_commitment = round(opt.hourly_cost, 3)
        else:
            per_instance = 1.0 if pool.measure == "qty" else (main.units_per_instance or 1.0)
            rec.quantity = round(capacity_to_buy / per_instance, 2)
            rec.instance_type = pool.instance_type or main.instance_type
            if pool.measure == "qty" and pool.service in ("SQL Database", "Azure Cosmos DB"):
                rec.instance_type = pool.instance_type
        expiring_units = self._expiring_units(expiring, main)
        rec.rationale = self._rationale(
            pool, st, sizing, rec, expiring, expiring_units, exchanged_units, main, series
        )
        rec.details = {
            "pool": asdict(pool),
            "pool_label": pool.label,
            "unit": pool.unit_label,
            "capacity_units": round(sizing.capacity, 4),
            "purchase_units": round(capacity_to_buy, 4),
            "exchanged_units": round(exchanged_units, 4),
            "od_per_unit_hour": round(od_per_unit, 6),
            "percentiles": {f"p{p}": round(v, 4) for p, v in st.percentiles.items()},
            "stability": {
                "sizing_from": _day(self.data, st.sizing_start_day).isoformat(),
                "sizing_reason": st.sizing_reason,
                "step_day": _day(self.data, st.step_day).isoformat() if st.step_day else None,
                "step_change": round(st.step_change, 3),
                "trend": round(st.trend_rel, 3),
                "floor_cv": round(st.floor_cv, 3),
                "weekend_ratio": st.weekend_ratio and round(st.weekend_ratio, 3),
                "business_hours_ratio": st.business_hours_ratio
                and round(st.business_hours_ratio, 3),
                "night_ratio": st.night_ratio and round(st.night_ratio, 3),
            },
            "sizes_by_profile": {p: round(v, 4) for p, v in self._report_for(pool).sizes.items()},
            "options": [
                {
                    "term_months": o.term_months,
                    "payment_option": o.payment_option,
                    "discount": round(o.discount, 4),
                    "upfront": round(o.upfront, 2),
                    "monthly_cost_after": round(o.monthly_cost_after, 2),
                    "monthly_savings": round(o.monthly_savings, 2),
                    "breakeven_month": round(o.breakeven_month, 2),
                }
                for _, o in sorted(opts.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
            ],
            "expiring_commitments": [
                {
                    "id": c.provider_commitment_id,
                    "end": c.end_at.date().isoformat(),
                    "quantity": c.quantity,
                    "hourly_commitment": c.hourly_commitment,
                }
                for c in expiring
            ],
            "chart": self._chart(series, sizing.capacity),
        }
        if expiring and rec.quantity is not None and expiring_units:
            rec.details["renew_quantity"] = round(min(expiring_units, rec.quantity), 2)
            rec.details["add_quantity"] = round(max(rec.quantity - expiring_units, 0), 2)
        return rec

    def _report_for(self, pool: Pool) -> PoolReport:
        return next(r for r in reversed(self.reports) if r.pool == pool)

    def _expiring_units(self, expiring: list[CommitmentInfo], main: UsageGroup) -> float:
        """Expiring reservations, in instances of the recommended type."""
        total = 0.0
        for c in expiring:
            if c.is_reservation and c.quantity:
                if main.units_per_instance and c.instance_type and main.instance_type:
                    from app.collectors.aws.usage_types import aws_size_factor

                    if c.provider == "aws" and "." in c.instance_type:
                        ratio = aws_size_factor(c.instance_type) / aws_size_factor(
                            main.instance_type
                        )
                        total += c.quantity * ratio
                        continue
                total += c.quantity
        return total

    def _chart(self, series: np.ndarray, capacity: float) -> dict[str, Any]:
        """Hourly baseline (last 30 days) and daily min/mean/max of the sizing window."""
        days = len(series) // 24
        daily = series[: days * 24].reshape(days, 24)
        start = self.as_of - timedelta(days=days)
        return {
            "commitment_line": round(capacity, 4),
            "hourly_start": (self.as_of - timedelta(days=min(days, 30))).isoformat(),
            "hourly": [round(float(v), 3) for v in series[-min(days, 30) * 24 :]],
            "daily_start": start.date().isoformat(),
            "daily_min": [round(float(v), 3) for v in daily.min(axis=1)],
            "daily_mean": [round(float(v), 3) for v in daily.mean(axis=1)],
            "daily_max": [round(float(v), 3) for v in daily.max(axis=1)],
        }

    def _rationale(
        self, pool, st, sizing, rec, expiring, expiring_units, exchanged_units, main, series
    ) -> str:
        days = len(series) // 24
        unit = pool.unit_label
        if pool.measure == "od":
            what = {
                k.AWS_SP_COMPUTE: "on-demand EC2, Fargate and Lambda compute",
                k.AWS_SP_EC2: f"on-demand {pool.family} usage in {pool.region}",
                k.AZURE_SP_COMPUTE: "on-demand Azure compute (VMs, App Service)",
            }[pool.kind]
            floor = (
                f"{what} cost at least ${st.percentiles[5]:,.2f}/hour 95% of the time "
                f"(median ${st.percentiles[50]:,.2f}/hour) over the last {days} days"
            )
            commit = (
                f"A {rec.term_months // 12}-year savings plan of ${rec.hourly_commitment:,.3f}/hour"
            )
        else:
            label = pool.instance_type or f"{pool.family}"
            svc = {"ec2": "EC2", "rds": "RDS", "elasticache": "ElastiCache"}.get(
                pool.service, pool.service
            )
            low = float(series.min())
            floor = (
                f"Your {svc} {label} usage in {pool.region}"
                f"{' (' + pool.detail + ')' if pool.detail else ''} never dropped below "
                f"{low:,.1f} {unit}/hour in {days} days (P10 {st.percentiles[10]:,.1f}, "
                f"median {st.percentiles[50]:,.1f})"
            )
            commit = (
                f"Reserving {rec.quantity:g} × {rec.instance_type} for "
                f"{rec.term_months // 12} year{'s' if rec.term_months > 12 else ''}"
            )
        parts = [floor + "."]
        if st.sizing_reason == "step" and st.step_day is not None:
            parts.append(
                f"Usage {'rose' if st.step_change > 0 else 'fell'} {abs(st.step_change):.0%} "
                f"on {_day(self.data, st.step_day).isoformat()}, so only data since then is used."
            )
        elif st.sizing_reason == "ramp":
            parts.append(
                "Usage is ramping up, so this commits only to the floor of the last 30 days."
            )
        elif st.sizing_reason == "started":
            parts.append(
                f"This workload started on {_day(self.data, st.first_active_day).isoformat()}."
            )
        if st.trend_rel > 0.1 and st.sizing_reason != "ramp":
            parts.append(f"It grew about {st.trend_rel:.0%} over the period.")
        if st.business_hours_ratio and st.business_hours_ratio > 1.5:
            parts.append("Weekday daytime peaks are left on demand.")
        parts.append(
            f"{commit} keeps expected utilization at {rec.expected_utilization_pct:.1f}% and "
            f"saves {_fmt_money(rec.monthly_savings)}/month ({rec.savings_pct:.0f}% of the "
            f"covered on-demand cost)."
        )
        if rec.upfront_cost:
            parts.append(
                f"Upfront {_fmt_money(rec.upfront_cost)}, paid back in "
                f"{rec.breakeven_month:.1f} months."
            )
        if expiring:
            ends = ", ".join(c.end_at.date().isoformat() for c in expiring)
            if pool.measure == "od":
                current = sum(c.hourly_commitment or 0 for c in expiring)
                parts.append(f"This replaces a savings plan of ${current:,.3f}/hour ending {ends}.")
            elif expiring_units and rec.quantity is not None:
                renew = min(expiring_units, rec.quantity)
                add = max(rec.quantity - expiring_units, 0)
                parts.append(
                    f"Renew {renew:g} reserved ending {ends}"
                    + (f" and add {add:g}." if add >= 0.5 else ".")
                )
            if rec.urgent:
                parts.append(
                    f"Urgent: the current commitment ends within {self.config.urgent_days} days."
                )
        if exchanged_units:
            parts.append(
                f"{exchanged_units:,.1f} {unit}/hour of this usage is covered by "
                "exchanging a stranded reservation (see the exchange "
                "recommendation); only the remainder is a new purchase."
            )
        return " ".join(parts)

    # ---- existing commitments: stranded / underutilized, exchanges
    def _commitment_health(self, c: CommitmentInfo):
        """(recent utilization %, earlier utilization %, drop date) from daily utilization."""
        if not c.utilization:
            return None
        recent = [u for d, u in c.utilization[-14:]]
        drop = None
        for d, u in reversed(c.utilization):
            if u < 50:
                drop = d
            else:
                break
        before = [u for d, u in c.utilization if drop is None or d < drop][-30:]
        return (float(np.mean(recent)), float(np.mean(before)) if before else None, drop)

    def _plan_exchanges(self, ri_pools: dict[Pool, list[UsageGroup]]) -> None:
        for c in self.commitments:
            if not c.exchangeable or c.end_at <= self.as_of:
                continue
            health = self._commitment_health(c)
            if not health:
                continue
            recent, before, drop = health
            if recent >= 25 or before is None or before < 80 or drop is None:
                continue
            target = self._exchange_target(c, drop, ri_pools)
            if target:
                self.exchange_units[target] = (0.0, c, recent)

    def _exchange_target(self, c: CommitmentInfo, drop: date, ri_pools) -> Pool | None:
        """A reservation pool in the same service and region whose usage started or stepped
        up around the time this commitment's usage dropped."""
        best, best_mean = None, 0.0
        for pool, groups in ri_pools.items():
            if pool.provider != c.provider or pool.region != c.region:
                continue
            if pool.service != _pool_service(c):
                continue
            if pool.family and pool.family == c.instance_family:
                continue
            series = self._series(pool, groups)
            st = analyze(
                series, self.hour0_weekday, self.config.lookback_days, self.config.step_min_change
            )
            start_idx = st.step_day if st.step_change > 0 else st.first_active_day
            if start_idx is None:
                continue
            start = _day(self.data, start_idx)
            if abs((start - drop).days) <= 7 and st.mean > best_mean:
                best, best_mean = pool, st.mean
        return best

    def _apply_exchange(
        self, pool, sizing, od_per_unit, discounts, base_tp, series, step, groups
    ) -> float:
        """Size the exchange of a stranded reservation into this pool; returns units covered."""
        _, c, recent = self.exchange_units[pool]
        value_per_hour = c.amortized_hourly or 0.0
        rate_per_unit = od_per_unit * (1 - discounts[base_tp])
        units = _round_down(min(value_per_hour / rate_per_unit, sizing.capacity), step or 0.001)
        if units <= 0:
            return 0.0
        util, covered = simulate(series, units)
        main = max(groups, key=lambda g: float(pool.series(g)[-len(series) :].sum()))
        per_instance = 1.0 if pool.measure == "qty" else (main.units_per_instance or 1.0)
        how = (
            "Azure lets you exchange reservations for another size or series"
            if c.provider == "azure"
            else "Convertible RIs can be exchanged for another family of equal or greater value"
        )
        rec = EngineRecommendation(
            source="engine",
            action="exchange",
            provider=c.provider,
            kind=c.kind,
            term_months=c.term_months,
            payment_option=c.payment_option,
            monthly_savings=round(covered * od_per_unit * HOURS_PER_MONTH, 2),
            service=pool.service,
            region=pool.region,
            instance_family=pool.family,
            instance_type=main.instance_type,
            quantity=round(units / per_instance, 2),
            upfront_cost=0.0,
            monthly_cost_after=0.0,
            savings_pct=100.0,
            expected_utilization_pct=round(100 * util, 2),
            breakeven_month=0.0,
            risk="low",
            rationale=(
                f"Your {c.instance_type} reservation ({c.quantity:g} reserved, ends "
                f"{c.end_at.date().isoformat()}) has been {recent:.0f}% utilized for the last "
                f"14 days since usage moved to {main.instance_type}. {how}: exchanging it into "
                f"{units / per_instance:g} × {main.instance_type} puts its remaining value "
                f"(about {_fmt_money(value_per_hour * HOURS_PER_MONTH)}/month) back to work "
                f"at {100 * util:.0f}% expected utilization. Do not buy more "
                f"{c.instance_family or c.instance_type}."
            ),
            details={
                "exchange_from": c.provider_commitment_id,
                "from_instance_type": c.instance_type,
                "recent_utilization_pct": round(recent, 2),
                "unused_monthly": round((1 - recent / 100) * value_per_hour * HOURS_PER_MONTH, 2),
                "units": round(units, 4),
                "pool_label": pool.label,
            },
        )
        self.recommendations.append(rec)
        self.exchange_units[pool] = (units, c, recent)
        return units

    def _flag_existing(self) -> None:
        exchanged = {c.provider_commitment_id for _, c, _ in self.exchange_units.values()}
        for c in self.commitments:
            if c.provider_commitment_id in exchanged or c.end_at <= self.as_of:
                continue
            health = self._commitment_health(c)
            if not health:
                continue
            recent, before, _ = health
            if recent >= 80:
                continue
            stranded = recent < 25 and (before or 0) >= 80
            if stranded and c.is_reservation and not c.exchangeable:
                advice = (
                    "Standard RIs can't be exchanged; consider selling the remaining term "
                    "on the RI Marketplace, and don't buy more of this type."
                )
            elif stranded:
                advice = (
                    "No current workload fits it; exchange it when one does, and don't "
                    "buy more of this type."
                )
            else:
                advice = "Avoid buying more commitments that overlap with it."
            unused = (1 - recent / 100) * (c.amortized_hourly or 0) * HOURS_PER_MONTH
            self.recommendations.append(
                EngineRecommendation(
                    source="engine",
                    action="flag",
                    provider=c.provider,
                    kind=c.kind,
                    term_months=c.term_months,
                    payment_option=c.payment_option,
                    monthly_savings=0.0,
                    service=c.service,
                    region=c.region,
                    instance_family=c.instance_family,
                    instance_type=c.instance_type,
                    quantity=c.quantity,
                    hourly_commitment=c.hourly_commitment,
                    expected_utilization_pct=round(recent, 2),
                    risk="high" if stranded else "med",
                    rationale=(
                        f"{'Stranded' if stranded else 'Underutilized'}: this "
                        f"{c.instance_type or c.kind} commitment was {recent:.0f}% utilized "
                        f"over the last 14 days, wasting about {_fmt_money(unused)}/month. "
                        + advice
                    ),
                    details={
                        "commitment_id": c.provider_commitment_id,
                        "status": "stranded" if stranded else "underutilized",
                        "unused_monthly": round(unused, 2),
                    },
                )
            )

    def _rank(self) -> None:
        """Purchase plan order: layer order (RIs, instance SPs, compute SPs), urgent first,
        then by savings. Exchanges come before purchases in the same layer."""
        layer = {
            k.AWS_RI: 0,
            k.AZURE_RI: 0,
            k.AWS_SP_EC2: 1,
            k.AWS_SP_COMPUTE: 2,
            k.AZURE_SP_COMPUTE: 2,
        }
        action = {"exchange": 0, "renew": 1, "purchase": 1}
        plan = [r for r in self.recommendations if r.action != "flag"]
        plan.sort(
            key=lambda r: (not r.urgent, layer.get(r.kind, 3), action[r.action], -r.monthly_savings)
        )
        for i, r in enumerate(plan, 1):
            r.plan_rank = i
        flags = [r for r in self.recommendations if r.action == "flag"]
        self.recommendations = plan + flags


def run_engine(data, prices, commitments, config=None) -> EngineResult:
    return Engine(data, prices, commitments, config).run()


__all__ = [
    "ENGINE_VERSION",
    "LAYER_COMPUTE_SP",
    "LAYER_INSTANCE_SP",
    "LAYER_RI",
    "CommitmentInfo",
    "Engine",
    "EngineRecommendation",
    "EngineResult",
    "run_engine",
]
