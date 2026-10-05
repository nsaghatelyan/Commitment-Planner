"""Stability analysis of an hourly usage series."""

import math
from dataclasses import dataclass, field

import numpy as np

PERCENTILES = (5, 10, 20, 30, 40, 50)


@dataclass
class Stability:
    days: int  # days in the analysed series
    first_active_day: int | None  # index of the first day with usage
    step_day: int | None = None  # index of the last significant step change
    step_change: float = 0.0  # relative change of daily mean at the step
    sizing_start_day: int = 0  # sizing uses days [sizing_start_day, days)
    sizing_reason: str | None = None
    percentiles: dict[int, float] = field(default_factory=dict)  # on the sizing window
    mean: float = 0.0
    trend_rel: float = 0.0  # linear-fit change over the sizing window, relative to mean
    floor_cv: float = 0.0  # coefficient of variation of the daily minimum
    weekend_ratio: float | None = None  # weekend mean / weekday mean
    business_hours_ratio: float | None = None  # weekday 08-20 mean / all other hours mean
    night_ratio: float | None = None  # 00-06 mean / other hours mean
    recent_mean: float = 0.0  # last 7 days
    p10_lookback: float = 0.0  # P10 over the whole lookback, ignoring steps and ramps
    peak_daily_mean: float = 0.0

    @property
    def settled_days(self) -> int:
        return self.days - self.sizing_start_day

    @property
    def decommissioned(self) -> bool:
        return self.peak_daily_mean > 0 and self.recent_mean < 0.05 * self.peak_daily_mean

    @property
    def active_days(self) -> int:
        return 0 if self.first_active_day is None else self.days - self.first_active_day


def find_step(daily: np.ndarray, min_segment: int = 7, min_change: float = 0.25):
    """Most significant abrupt change in the daily mean: (day index, relative change) or None.

    A split counts when the means differ by at least `min_change` of the larger one, by more
    than 4 standard errors, and most of the change happens within a few days around the split
    (a gradual ramp is a trend, not a step)."""
    n = len(daily)
    best = None
    for s in range(min_segment, n - min_segment + 1):
        before, after = daily[:s], daily[s:]
        mb, ma = before.mean(), after.mean()
        base = max(mb, ma)
        if base <= 0 or abs(ma - mb) < min_change * base:
            continue
        se = np.sqrt(before.var() / len(before) + after.var() / len(after)) + 1e-9
        score = abs(ma - mb) / se
        if score > 4 and (best is None or score > best[2]):
            best = (s, (ma - mb) / base, score)
    if not best:
        return None
    s = best[0]
    local = daily[s : s + 3].mean() - daily[max(0, s - 3) : s].mean()
    total = daily[s:].mean() - daily[:s].mean()
    if abs(local) < 0.5 * abs(total):
        return None
    return s, best[1]


def analyze(
    series: np.ndarray,
    hour0_weekday: int,
    lookback_days: int,
    min_change: float = 0.25,
) -> Stability:
    """`series`: hourly values for whole days ending now; `hour0_weekday`: weekday of hour 0."""
    days = len(series) // 24
    x = series[: days * 24].reshape(days, 24)
    daily_mean = x.mean(axis=1)
    peak = float(daily_mean.max()) if days else 0.0
    active = np.nonzero(daily_mean > 0.01 * peak)[0] if peak > 0 else np.array([], dtype=int)
    st = Stability(days=days, first_active_day=int(active[0]) if len(active) else None)
    st.peak_daily_mean = peak
    st.recent_mean = float(daily_mean[-7:].mean()) if days else 0.0
    if st.first_active_day is None:
        return st
    st.p10_lookback = float(np.percentile(x[max(0, days - lookback_days) :].ravel(), 10))

    # Only look for steps from the first active day: the start of a workload is not a step.
    st.sizing_start_day = st.first_active_day
    if st.first_active_day > 0:
        st.sizing_reason = "started"
    step = find_step(daily_mean[st.first_active_day :], min_change=min_change)
    if step:
        st.step_day = st.first_active_day + step[0]
        st.step_change = float(step[1])
        st.sizing_start_day = st.step_day
        st.sizing_reason = "step"
    st.sizing_start_day = max(st.sizing_start_day, days - lookback_days)

    window = x[st.sizing_start_day :]
    w_daily = daily_mean[st.sizing_start_day :]
    st.mean = float(window.mean())
    if len(w_daily) >= 2 and st.mean > 0:
        slope = np.polyfit(np.arange(len(w_daily)), w_daily, 1)[0]
        st.trend_rel = float(slope * len(w_daily) / st.mean)
    # Ramping up: commit only to the recent floor (last 30 days of the window).
    if st.trend_rel > 0.2 and len(w_daily) > 30:
        st.sizing_start_day = days - 30
        st.sizing_reason = "ramp"
        window = x[st.sizing_start_day :]
        w_daily = daily_mean[st.sizing_start_day :]
    flat = window.ravel()
    st.percentiles = {p: float(np.percentile(flat, p)) for p in PERCENTILES}
    floors = window.min(axis=1)
    st.floor_cv = float(floors.std() / floors.mean()) if floors.mean() > 0 else 0.0

    weekdays = (hour0_weekday + np.arange(days)) % 7
    sel_days = np.arange(st.sizing_start_day, days)
    wk = weekdays[sel_days]
    weekday_mean = window[wk < 5].mean() if (wk < 5).any() else 0.0
    weekend_mean = window[wk >= 5].mean() if (wk >= 5).any() else None
    if weekend_mean is not None and weekday_mean > 0:
        st.weekend_ratio = float(weekend_mean / weekday_mean)
    hours = np.arange(24)
    bh = window[wk < 5][:, (hours >= 8) & (hours < 20)]
    other_mask = np.ones_like(window, dtype=bool)
    other_mask[np.ix_(wk < 5, (hours >= 8) & (hours < 20))] = False
    other = window[other_mask]
    if bh.size and other.size:
        st.business_hours_ratio = _ratio(bh.mean(), other.mean())
    st.night_ratio = _ratio(window[:, hours < 6].mean(), window[:, hours >= 6].mean())
    return st


def _ratio(a: float, b: float) -> float | None:
    if b > 0:
        return float(a / b)
    return math.inf if a > 0 else None
