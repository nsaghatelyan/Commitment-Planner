from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

HOURS_PER_MONTH = Decimal(730)


@dataclass
class PriceRecord:
    provider: str
    service: str
    sku_key: str
    region: str
    pricing_model: str  # on_demand | sp | ri
    unit: str
    price_per_unit: Decimal  # per unit (per hour for compute); RI/SP upfront is amortized in
    effective_from: date
    term_months: int | None = None
    payment_option: str | None = None
    currency: str = "USD"
    attributes: dict[str, Any] = field(default_factory=dict)
