from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# commitment.kind values
AWS_SP_COMPUTE = "aws_sp_compute"
AWS_SP_EC2 = "aws_sp_ec2"
AWS_SP_SAGEMAKER = "aws_sp_sagemaker"
AWS_SP_DATABASE = "aws_sp_database"
AWS_RI = "aws_ri"
AZURE_SP_COMPUTE = "azure_sp_compute"
AZURE_RI = "azure_ri"


@dataclass
class AccountInfo:
    external_account_id: str
    name: str | None = None
    is_payer: bool = False


@dataclass
class CommitmentRecord:
    kind: str
    provider_commitment_id: str
    owner_account_id: str | None
    start_at: datetime
    end_at: datetime
    term_months: int
    scope: str | None = None
    service: str | None = None
    region: str | None = None
    instance_family: str | None = None
    instance_type: str | None = None
    quantity: int | None = None
    hourly_commitment: Decimal | None = None
    payment_option: str | None = None
    upfront_cost: Decimal | None = None
    recurring_hourly_cost: Decimal | None = None
    state: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class UtilizationRecord:
    provider_commitment_id: str
    date: date
    utilization_pct: Decimal
    unused_cost: Decimal | None = None
    used_amount: Decimal | None = None
    net_savings: Decimal | None = None


@dataclass
class NativeRecommendation:
    """A purchase recommendation produced by the cloud provider itself, kept as engine input."""

    provider: str
    kind: str
    term_months: int
    payment_option: str | None
    lookback_days: int
    region: str | None = None
    instance_family: str | None = None
    instance_type: str | None = None
    hourly_commitment: Decimal | None = None
    quantity: Decimal | None = None
    estimated_monthly_savings: Decimal | None = None
    upfront_cost: Decimal | None = None
    scope: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallStats:
    """Counts API calls actually sent (cache hits excluded); Cost Explorer calls are billed."""

    calls: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0

    def record(self, api: str) -> None:
        self.calls[api] = self.calls.get(api, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.calls.values())
