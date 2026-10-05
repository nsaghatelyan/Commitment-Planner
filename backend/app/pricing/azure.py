"""Azure Retail Prices API (public, no auth): on-demand, reservation and savings plan prices."""

from collections.abc import Iterable, Iterator
from datetime import date
from decimal import Decimal
from typing import Any

from app.collectors.azure.http import AzureHttp
from app.collectors.cache import CountingCaller
from app.pricing.keys import azure_key
from app.pricing.types import HOURS_PER_MONTH, PriceRecord

RETAIL_URL = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"  # includes savingsPlan prices on consumption items
TERM_MONTHS = {"1 Year": 12, "3 Years": 36, "5 Years": 60}


def retail_items(
    http: AzureHttp, call: CountingCaller, odata_filter: str
) -> Iterator[dict[str, Any]]:
    url: str | None = RETAIL_URL
    params: dict[str, Any] | None = {"api-version": API_VERSION, "$filter": odata_filter}
    while url:
        page = call(
            "azure:RetailPrices",
            http.request,
            cache_key={"url": url, "params": params},
            method="GET",
            url=url,
            params=params,
        )
        yield from page.get("Items", [])
        url, params = page.get("NextPageLink"), None


def _hours_per_unit(unit: str) -> Decimal:
    return Decimal(1) if "hour" in unit.lower() else Decimal(0)


def parse_item(item: dict[str, Any], today: date) -> list[PriceRecord]:
    """One retail price item -> on_demand / ri / sp price rows (Spot and DevTest skipped)."""
    sku_name = item.get("skuName", "")
    if (
        item.get("type") not in ("Consumption", "Reservation")
        or "Spot" in sku_name
        or "Low Priority" in sku_name
        or not item.get("armSkuName")
        or float(item.get("tierMinimumUnits", 0) or 0) > 0
    ):
        return []
    key = azure_key(item["serviceName"], item["armSkuName"], item.get("productName"))
    common = {
        "provider": "azure",
        "service": item["serviceName"],
        "sku_key": key,
        "region": item.get("armRegionName", ""),
        "currency": item.get("currencyCode", "USD"),
        "effective_from": today,
        "attributes": {
            "meterId": item.get("meterId"),
            "productName": item.get("productName"),
            "skuName": sku_name,
            "list_effective_start": item.get("effectiveStartDate"),
        },
    }
    unit = item.get("unitOfMeasure", "1 Hour")
    out = []
    if item["type"] == "Reservation":
        months = TERM_MONTHS.get(item.get("reservationTerm", ""))
        if months:
            # Reservation unitPrice is the total for the term; store it per hour.
            hourly = Decimal(str(item["unitPrice"])) / (months * HOURS_PER_MONTH)
            out.append(
                PriceRecord(
                    **common,
                    pricing_model="ri",
                    unit="1 Hour",
                    price_per_unit=hourly.quantize(Decimal("0.00000001")),
                    term_months=months,
                )
            )
        return out
    out.append(
        PriceRecord(
            **common,
            pricing_model="on_demand",
            unit=unit,
            price_per_unit=Decimal(str(item["retailPrice"])),
        )
    )
    for sp in item.get("savingsPlan") or []:
        months = TERM_MONTHS.get(sp.get("term", ""))
        if months:
            out.append(
                PriceRecord(
                    **common,
                    pricing_model="sp",
                    unit=unit,
                    price_per_unit=Decimal(str(sp["retailPrice"])),
                    term_months=months,
                )
            )
    return out


def retail_prices(
    http: AzureHttp,
    call: CountingCaller,
    regions: Iterable[str],
    services: Iterable[str] = ("Virtual Machines",),
    today: date | None = None,
) -> Iterator[PriceRecord]:
    today = today or date.min
    for service in services:
        for region in regions:
            odata = f"serviceName eq '{service}' and armRegionName eq '{region}'"
            for item in retail_items(http, call, odata):
                yield from parse_item(item, today)


class MeterCatalog:
    """meterId -> service, region, SKU, product and list price, from the Retail Prices API."""

    BATCH = 10

    def __init__(self, http: AzureHttp, call: CountingCaller) -> None:
        self.http = http
        self.call = call
        self.meters: dict[str, dict[str, Any]] = {}

    def lookup(self, meter_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        missing = sorted({m.lower() for m in meter_ids if m} - set(self.meters))
        for i in range(0, len(missing), self.BATCH):
            batch = missing[i : i + self.BATCH]
            odata = " or ".join(f"meterId eq '{m}'" for m in batch)
            for item in retail_items(self.http, self.call, odata):
                meter = item.get("meterId", "").lower()
                # Prefer the pay-as-you-go consumption row for a meter.
                if meter and (meter not in self.meters or item.get("type") == "Consumption"):
                    self.meters[meter] = item
            for m in batch:
                self.meters.setdefault(m, {})
        return self.meters
