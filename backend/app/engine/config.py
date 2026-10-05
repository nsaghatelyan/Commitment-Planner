from dataclasses import dataclass, field

RISK_PROFILES = ("conservative", "balanced", "aggressive")


@dataclass(frozen=True)
class EngineConfig:
    risk_profile: str = "balanced"
    # Minimum expected utilization of a recommended commitment, per risk profile.
    target_utilization: dict[str, float] = field(
        default_factory=lambda: {"conservative": 0.98, "balanced": 0.95, "aggressive": 0.90}
    )
    # Sizing looks at up to this many days (after any step change).
    lookback_days: int = 90
    # Step changes and stability are judged on up to this much history.
    history_days: int = 180
    # Commitments ending within this many days are treated as ending (renewals recommended).
    expiring_days: int = 90
    urgent_days: int = 15
    # A workload must have run this long (after any step change) before we commit to it.
    min_settled_days: int = 21
    # Below this much tenant history we warn and only allow conservative 1y No Upfront.
    min_history_days: int = 30
    # Hold back recommendations saving less than this per month: a 1-3 year lock-in isn't worth
    # a few dollars. Small accounts get a lower bar, this share of their monthly
    # commitment-eligible spend, but never below min_monthly_savings_floor.
    min_monthly_savings: float = 5.0
    min_savings_share: float = 0.02
    min_monthly_savings_floor: float = 1.0
    # A step change must move daily mean usage by at least this fraction.
    step_min_change: float = 0.25

    def target(self, profile: str | None = None) -> float:
        return self.target_utilization[profile or self.risk_profile]
