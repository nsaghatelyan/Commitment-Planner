"""Price lookups for the engine: on-demand rates and commitment discounts from the price table."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

UPFRONT_SHARE = {"no_upfront": 0.0, "partial_upfront": 0.5, "all_upfront": 1.0, "monthly": 0.0}


@dataclass
class Rate:
    price: float  # amortized $/unit-hour
    upfront_share: float  # fraction of the commitment paid upfront


@dataclass
class SkuPrices:
    on_demand: float | None = None
    # (pricing_model, term_months, payment_option) -> Rate
    commitments: dict[tuple[str, int, str | None], Rate] = field(default_factory=dict)

    def discount(self, model: str, term: int, payment: str | None) -> float | None:
        rate = self.commitments.get((model, term, payment))
        if rate is None or not self.on_demand:
            return None
        return 1 - rate.price / self.on_demand


class PriceBook:
    """(provider, sku_key, region) -> SkuPrices, keeping the latest effective_from."""

    def __init__(self, rows: Iterable[dict[str, Any]]) -> None:
        latest: dict[tuple, Any] = {}
        self.skus: dict[tuple[str, str, str], SkuPrices] = defaultdict(SkuPrices)
        for row in sorted(rows, key=lambda r: r["effective_from"]):
            ident = (row["provider"], row["sku_key"], row["region"], row["pricing_model"],
                     row["term_months"], row["payment_option"])  # fmt: skip
            latest[ident] = row
        for (provider, key, region, model, term, payment), row in latest.items():
            sku = self.skus[(provider, key, region)]
            price = float(row["price_per_unit"])
            if model == "on_demand":
                sku.on_demand = price
                continue
            attrs = row.get("attributes") or {}
            share = UPFRONT_SHARE.get(payment or "", 0.0)
            if "upfront" in attrs and term:
                total = price * term * 730
                share = float(Decimal(attrs["upfront"])) / total if total else share
            sku.commitments[(model, term, payment)] = Rate(price, share)

    def get(self, provider: str, sku_key: str | None, region: str | None) -> SkuPrices | None:
        if not sku_key or not region:
            return None
        return self.skus.get((provider, sku_key, region))

    def options(self, provider: str, sku_key: str, region: str, model: str):
        """(term, payment) pairs priced for this sku and model."""
        sku = self.get(provider, sku_key, region)
        if not sku:
            return []
        return sorted((t, p) for (m, t, p) in sku.commitments if m == model)
