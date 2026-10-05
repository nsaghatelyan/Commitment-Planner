"""Cost Management Query API bootstrap: daily usage while the client's FOCUS export fills.

The Query API allows two groupings per request, so:
  - commitment-eligible services are queried per pricing model, grouped by MeterId plus
    SubscriptionId (on-demand, spot) or ChargeType (reservation / savings plan, to tell used
    from unused), then enriched from the Retail Prices meter catalog (region, SKU, OS);
  - other services are queried daily by ServiceName x SubscriptionId;
  - commitment purchases come from one ActualCost query.
Everything else uses AmortizedCost. Requests are chunked by calendar month.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa

from app.collectors.azure.http import AzureHttp
from app.collectors.cache import CountingCaller
from app.pricing.azure import MeterCatalog
from app.pricing.keys import azure_os
from app.usage.normalize import instance_family
from app.usage.schema import (
    ChargeCategory,
    CommitmentStatus,
    CommitmentType,
    PricingCategory,
    usage_table,
)

API_VERSION = "2023-11-01"
COMMITMENT_SERVICES = [
    "Virtual Machines",
    "Virtual Machines Licenses",
    "SQL Database",
    "SQL Managed Instance",
    "Azure Cosmos DB",
    "Azure App Service",
    "Azure Database for MySQL",
    "Azure Database for PostgreSQL",
    "Azure Dedicated Host",
    "Azure Container Apps",
    "Functions",
    "Azure Databricks",
    "Azure Synapse Analytics",
    "Redis Cache",
    "Azure Kubernetes Service",
]
COST_COLUMNS = ("Cost", "PreTaxCost", "CostUSD", "PreTaxCostUSD")


def _dim(name: str, values: list[str]) -> dict[str, Any]:
    return {"dimensions": {"name": name, "operator": "In", "values": values}}


def _and(*exprs: dict[str, Any]) -> dict[str, Any]:
    return exprs[0] if len(exprs) == 1 else {"and": list(exprs)}


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """[start, end) split at month boundaries; yields inclusive (first, last) days."""
    cur = start
    while cur < end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        last = min(nxt, end) - timedelta(days=1)
        yield cur, last
        cur = last + timedelta(days=1)


def parse_query_rows(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """Query API response (columns + rows) -> list of dicts keyed by column name."""
    props = resp.get("properties", resp)
    names = [c["name"] for c in props.get("columns", [])]
    return [dict(zip(names, row, strict=False)) for row in props.get("rows", [])]


def _cost(row: dict[str, Any]) -> float | None:
    for name in COST_COLUMNS:
        if row.get(name) is not None:
            return float(row[name])
    return None


def _day(value: Any) -> datetime:
    s = str(value)
    if s.isdigit() and len(s) == 8:  # 20250131
        d = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    else:
        d = datetime.fromisoformat(s).date()
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


class QueryBootstrap:
    def __init__(
        self,
        http: AzureHttp,
        call: CountingCaller,
        scope: str,
        meters: MeterCatalog | None,
        billing_account_id: str | None = None,
    ) -> None:
        self.http = http
        self.call = call
        self.scope = scope.rstrip("/")
        self.meters = meters
        self.billing_account_id = billing_account_id

    def query(
        self,
        cost_type: str,
        first: date,
        last: date,
        grouping: list[str],
        filter_: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "type": cost_type,
            "timeframe": "Custom",
            "timePeriod": {
                "from": f"{first.isoformat()}T00:00:00Z",
                "to": f"{last.isoformat()}T23:59:59Z",
            },
            "dataset": {
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {"name": "Cost", "function": "Sum"},
                    "totalUsage": {"name": "UsageQuantity", "function": "Sum"},
                },
                "grouping": [{"type": "Dimension", "name": g} for g in grouping],
            },
        }
        if filter_:
            body["dataset"]["filter"] = filter_
        url: str | None = (
            f"{self.scope}/providers/Microsoft.CostManagement/query?api-version={API_VERSION}"
        )
        rows: list[dict[str, Any]] = []
        while url:
            resp = self.call(
                "azure:CostManagementQuery",
                self.http.request,
                cache_key={"url": url, "body": body},
                method="POST",
                url=url,
                json=body,
            )
            rows += parse_query_rows(resp)
            url = resp.get("properties", {}).get("nextLink")
        return rows

    def collect(self, start: date, end: date) -> Iterator[pa.Table]:
        """Yields one table per month (every row for those days)."""
        eligible = _dim("ServiceName", COMMITMENT_SERVICES)
        for first, last in month_chunks(start, end):
            raw: list[tuple[str, dict[str, Any]]] = []
            for model, grouping in (
                ("OnDemand", ["MeterId", "SubscriptionId"]),
                ("Spot", ["MeterId", "SubscriptionId"]),
                ("Reservation", ["MeterId", "ChargeType"]),
                ("SavingsPlan", ["MeterId", "ChargeType"]),
            ):
                f = _and(eligible, _dim("PricingModel", [model]))
                for row in self.query("AmortizedCost", first, last, grouping, f):
                    row.setdefault("PricingModel", model)
                    raw.append(("AmortizedCost", row))
            others = {"not": eligible}
            for row in self.query(
                "AmortizedCost", first, last, ["ServiceName", "SubscriptionId"], others
            ):
                raw.append(("AmortizedCost", row))
            purchases = _dim("ChargeType", ["Purchase"])
            for row in self.query(
                "ActualCost", first, last, ["ServiceName", "PricingModel"], purchases
            ):
                row.setdefault("ChargeType", "Purchase")
                raw.append(("ActualCost", row))

            if self.meters is not None:
                self.meters.lookup(r["MeterId"] for _, r in raw if r.get("MeterId"))
            rows = [self.to_usage(cost_type, r) for cost_type, r in raw]
            table = usage_table(r for r in rows if r is not None)
            if table.num_rows:
                yield table

    def to_usage_pair(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """A merge_cost_types() row: billed from ActualCost, effective from AmortizedCost.

        Billed cost with no amortized cost and no usage is a commitment purchase.
        """
        billed = row.get("BilledCost") or 0.0
        if not _cost(row) and not row.get("UsageQuantity") and billed:
            row = {**row, "ChargeType": row.get("ChargeType") or "Purchase", "Cost": billed}
        out = self.to_usage("AmortizedCost", row)
        if out is not None and out.get("charge_category") == ChargeCategory.USAGE.value:
            out["billed_cost"] = billed
        return out

    def to_usage(self, cost_type: str, row: dict[str, Any]) -> dict[str, Any] | None:
        """One Query API row (any grouping, ActualCost or AmortizedCost) -> one usage row."""
        cost = _cost(row)
        quantity = row.get("UsageQuantity")
        if not cost and not quantity:
            return None
        start = _day(row.get("UsageDate") or row.get("BillingMonth"))
        model = row.get("PricingModel") or "OnDemand"
        charge_type = row.get("ChargeType") or "Usage"
        meter = (self.meters.meters if self.meters else {}).get(
            str(row.get("MeterId", "")).lower(), {}
        )
        out: dict[str, Any] = {
            "charge_period_start": start,
            "charge_period_end": start + timedelta(days=1),
            "provider": "azure",
            "billing_account_id": self.billing_account_id,
            "sub_account_id": row.get("SubscriptionId"),
            "sub_account_name": row.get("SubscriptionName"),
            "region": row.get("ResourceLocation") or meter.get("armRegionName"),
            "service_name": row.get("ServiceName") or meter.get("serviceName"),
            "service_category": row.get("ServiceFamily") or meter.get("serviceFamily"),
            "sku_id": row.get("MeterId"),
            "resource_id": row.get("ResourceId"),
            "instance_type": meter.get("armSkuName") or None,
            "operating_system": azure_os(meter.get("productName"))
            if meter.get("serviceName") == "Virtual Machines"
            else None,
            "usage_quantity": float(quantity) if quantity is not None else None,
            "usage_unit": row.get("UnitOfMeasure") or meter.get("unitOfMeasure"),
            "charge_category": ChargeCategory.USAGE.value,
            "pricing_category": {
                "Spot": PricingCategory.SPOT,
                "Reservation": PricingCategory.COMMITMENT,
                "SavingsPlan": PricingCategory.COMMITMENT,
            }.get(model, PricingCategory.ON_DEMAND).value,
        }
        out["instance_family"] = instance_family(out["instance_type"])
        commitment = model in ("Reservation", "SavingsPlan")
        if commitment:
            out["commitment_type"] = (
                CommitmentType.RESERVATION
                if model == "Reservation"
                else CommitmentType.SAVINGS_PLAN
            ).value
        if charge_type == "Purchase":
            out.update(
                charge_category=ChargeCategory.PURCHASE.value,
                billed_cost=cost,
                effective_cost=0.0,
                usage_quantity=None,
            )
        elif charge_type in ("UnusedReservation", "UnusedSavingsPlan"):
            out.update(
                commitment_status=CommitmentStatus.UNUSED.value,
                billed_cost=0.0,
                effective_cost=cost,
                usage_quantity=None,
            )
        elif commitment:
            list_price = meter.get("retailPrice")
            od = float(list_price) * float(quantity) if list_price and quantity else None
            out.update(
                commitment_status=CommitmentStatus.USED.value,
                billed_cost=0.0,
                effective_cost=cost,
                list_cost=od,
                on_demand_equiv_cost=od,
            )
        elif cost_type == "ActualCost":
            # Actual cost alone can't give amortized cost; merge_cost_types() pairs it up.
            out.update(billed_cost=cost, effective_cost=None)
        else:
            # Pay-as-you-go: actual and amortized cost are the same.
            on_demand = model == "OnDemand"
            out.update(
                billed_cost=cost,
                effective_cost=cost,
                list_cost=cost if on_demand else None,
                on_demand_equiv_cost=cost if on_demand else None,
            )
        if charge_type in ("Refund", "RoundingAdjustment"):
            out["charge_category"] = (
                ChargeCategory.CREDIT if charge_type == "Refund" else ChargeCategory.ADJUSTMENT
            ).value
        return out


def merge_cost_types(
    actual: list[dict[str, Any]], amortized: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pair ActualCost and AmortizedCost query rows with the same date and grouping keys.

    Returns raw rows with both `BilledCost` (actual) and `Cost` (amortized) set, for
    QueryBootstrap.to_usage_pair.
    """

    def key(row: dict[str, Any]) -> tuple:
        return tuple(
            sorted((k, v) for k, v in row.items() if k not in COST_COLUMNS + ("UsageQuantity",))
        )

    merged: dict[tuple, dict[str, Any]] = {}
    for row in amortized:
        merged[key(row)] = {**row, "BilledCost": 0.0, "Cost": _cost(row)}
    for row in actual:
        k = key(row)
        entry = merged.setdefault(k, {**row, "Cost": 0.0, "BilledCost": 0.0})
        entry["BilledCost"] = (entry.get("BilledCost") or 0.0) + (_cost(row) or 0.0)
        if entry.get("UsageQuantity") is None:
            entry["UsageQuantity"] = row.get("UsageQuantity")
    return list(merged.values())
