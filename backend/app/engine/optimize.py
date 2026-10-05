"""Size a commitment by simulating it hour by hour over the usage series.

Everything is in "units" of the pool: normalized units for reservations, on-demand-equivalent
dollars for savings plans (od_per_unit = 1). A commitment of K units costs
K * od_per_unit * (1 - discount) every hour and covers min(usage, K) units.
"""

from dataclasses import dataclass

import numpy as np

HOURS_PER_MONTH = 730


@dataclass
class Sizing:
    capacity: float  # K, in pool units
    utilization: float  # expected, 0..1 (never above 1)
    coverage: float  # share of usage covered, 0..1
    covered_units_per_hour: float
    percentile: float  # share of hours with usage below K


def simulate(series: np.ndarray, capacity: float) -> tuple[float, float]:
    """(utilization, covered units per hour) for a commitment of `capacity` units."""
    if capacity <= 0 or len(series) == 0:
        return 0.0, 0.0
    covered = np.minimum(series, capacity)
    return float(covered.sum() / (capacity * len(series))), float(covered.mean())


def net_savings_per_hour(series, capacity, discount, od_per_unit=1.0) -> float:
    """Average hourly saving vs on-demand: covered*od - capacity*od*(1-discount)."""
    _, covered = simulate(series, capacity)
    return od_per_unit * (covered - capacity * (1 - discount))


def _candidates(series: np.ndarray, step: float) -> np.ndarray:
    qs = np.unique(np.percentile(series, np.arange(0, 101)))
    if step > 0:
        qs = np.unique(np.floor(qs / step) * step)
    return qs[qs > 0]


def choose_capacity(
    series: np.ndarray,
    discount: float,
    profile: str,
    target: float,
    step: float = 0.0,
) -> Sizing | None:
    """Pick K for a risk profile, keeping expected utilization >= target.

    conservative: about P10 of the series; balanced: the net-savings optimum; aggressive:
    the largest K that still saves money and meets the (lower) aggressive target.
    `step`: K is rounded down to a multiple of it (whole instances for reservations)."""
    if len(series) == 0 or series.max() <= 0:
        return None
    candidates = _candidates(series, step)
    ok = []
    for k in candidates:
        util, covered = simulate(series, k)
        if util + 1e-9 >= target:
            ok.append((k, util, covered, covered - k * (1 - discount)))
    if not ok:
        return None
    if profile == "conservative":
        p10 = np.percentile(series, 10)
        if step > 0:
            p10 = np.floor(p10 / step) * step
        below = [c for c in ok if c[0] <= p10 + 1e-9]
        pick = max(below, key=lambda c: c[0]) if below else min(ok, key=lambda c: c[0])
    elif profile == "aggressive":
        saving = [c for c in ok if c[3] > 0]
        pick = max(saving, key=lambda c: c[0]) if saving else None
    else:
        pick = max(ok, key=lambda c: c[3])
    if pick is None or pick[3] <= 0:
        return None
    k, util, covered, _ = pick
    return Sizing(
        capacity=float(k),
        utilization=min(util, 1.0),
        coverage=float(covered / series.mean()) if series.mean() > 0 else 0.0,
        covered_units_per_hour=covered,
        percentile=float((series < k).mean()),
    )


@dataclass
class TermOption:
    term_months: int
    payment_option: str | None
    discount: float
    hourly_cost: float  # amortized $/hour of the commitment
    upfront: float
    monthly_cost_after: float  # what the covered usage costs per month with the commitment
    monthly_savings: float
    savings_pct: float  # of the on-demand cost of the covered usage... and unused capacity
    breakeven_month: float


def term_option(
    capacity: float,
    covered_units_per_hour: float,
    od_per_unit: float,
    discount: float,
    upfront_share: float,
    term: int,
    payment: str | None,
) -> TermOption:
    hourly_cost = capacity * od_per_unit * (1 - discount)
    od_covered = covered_units_per_hour * od_per_unit
    monthly_cost = hourly_cost * HOURS_PER_MONTH
    monthly_savings = (od_covered - hourly_cost) * HOURS_PER_MONTH
    upfront = hourly_cost * term * HOURS_PER_MONTH * upfront_share
    recurring_monthly = monthly_cost * (1 - upfront_share)
    benefit = od_covered * HOURS_PER_MONTH - recurring_monthly
    breakeven = upfront / benefit if upfront and benefit > 0 else 0.0
    return TermOption(
        term_months=term,
        payment_option=payment,
        discount=discount,
        hourly_cost=hourly_cost,
        upfront=upfront,
        monthly_cost_after=monthly_cost,
        monthly_savings=monthly_savings,
        savings_pct=monthly_savings / (od_covered * HOURS_PER_MONTH) if od_covered else 0.0,
        breakeven_month=breakeven,
    )
