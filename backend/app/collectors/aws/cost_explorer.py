"""Cost Explorer bootstrap: daily usage while the client's Data Export fills.

Cost Explorer allows only two group-by dimensions, so commitment-eligible services are queried
per service and per purchase type, grouped by USAGE_TYPE and OPERATION (which carry region,
instance type and OS). All other services are kept at SERVICE x LINKED_ACCOUNT level so total
spend still adds up. Each request costs $0.01 and goes through the counting cache.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa

from app.collectors.aws.usage_types import (
    database_engine,
    deployment_option,
    operating_system,
    parse_usage_type,
)
from app.collectors.cache import CountingCaller
from app.usage.normalize import instance_family
from app.usage.schema import (
    ChargeCategory,
    CommitmentStatus,
    CommitmentType,
    PricingCategory,
    usage_table,
)

# Cost Explorer SERVICE names whose usage commitments can cover.
COMMITMENT_SERVICES = {
    "Amazon Elastic Compute Cloud - Compute": "Compute",
    "Amazon Relational Database Service": "Databases",
    "Amazon ElastiCache": "Databases",
    "Amazon OpenSearch Service": "Analytics",
    "Amazon Redshift": "Analytics",
    "Amazon MemoryDB": "Databases",
    "Amazon DynamoDB": "Databases",
    "AWS Lambda": "Compute",
    "Amazon SageMaker": "AI and Machine Learning",
    "Amazon Elastic Container Service": "Compute",
}

USAGE_RECORD_TYPES = ["Usage", "DiscountedUsage", "SavingsPlanCoveredUsage"]
METRICS = ["AmortizedCost", "UnblendedCost", "UsageQuantity", "NormalizedUsageAmount"]


def classify_purchase_type(value: str) -> str:
    """Return 'spot' | 'sp' | 'ri' | 'od' for a PURCHASE_TYPE dimension value."""
    v = value.lower()
    if "spot" in v:
        return "spot"
    if "savings plan" in v:
        return "sp"
    if "reserved" in v or "reservation" in v:
        return "ri"
    return "od"


def _period(start: date, end: date) -> dict[str, str]:
    return {"Start": start.isoformat(), "End": end.isoformat()}


def _dim(key: str, values: list[str]) -> dict[str, Any]:
    return {"Dimensions": {"Key": key, "Values": values}}


def _and(*exprs: dict[str, Any]) -> dict[str, Any]:
    exprs = tuple(e for e in exprs if e)
    return exprs[0] if len(exprs) == 1 else {"And": list(exprs)}


class CostExplorerBootstrap:
    def __init__(self, ce_client: Any, call: CountingCaller, payer_account_id: str | None) -> None:
        self.ce = ce_client
        self.call = call
        self.payer = payer_account_id

    def dimension_values(self, key: str, start: date, end: date) -> list[str]:
        values: list[str] = []
        token = None
        while True:
            kwargs: dict[str, Any] = {"TimePeriod": _period(start, end), "Dimension": key}
            if token:
                kwargs["NextPageToken"] = token
            resp = self.call(
                "ce:GetDimensionValues",
                self.ce.get_dimension_values,
                cache_key=kwargs,
                **kwargs,
            )
            values += [d["Value"] for d in resp.get("DimensionValues", [])]
            token = resp.get("NextPageToken")
            if not token:
                return values

    def cost_and_usage(self, start: date, end: date, group_by: list[str], filter_: dict | None):
        """Yield (day, {group key: value}, metrics) across pages."""
        token = None
        while True:
            kwargs: dict[str, Any] = {
                "TimePeriod": _period(start, end),
                "Granularity": "DAILY",
                "Metrics": METRICS,
                "GroupBy": [{"Type": "DIMENSION", "Key": k} for k in group_by],
            }
            if filter_:
                kwargs["Filter"] = filter_
            if token:
                kwargs["NextPageToken"] = token
            resp = self.call(
                "ce:GetCostAndUsage", self.ce.get_cost_and_usage, cache_key=kwargs, **kwargs
            )
            yield from iter_groups(resp, group_by)
            token = resp.get("NextPageToken")
            if not token:
                return

    def parse_response(self, resp: dict[str, Any]) -> list[dict[str, Any]]:
        """Any daily GetCostAndUsage response (e.g. daily by SERVICE) -> usage rows."""
        rows = (self.parse_group(day, keys, metrics) for day, keys, metrics in iter_groups(resp))
        return [r for r in rows if r is not None]

    def collect(self, start: date, end: date) -> pa.Table:
        services = self.dimension_values("SERVICE", start, end)
        purchase_types = self.dimension_values("PURCHASE_TYPE", start, end)
        by_class: dict[str, list[str]] = {}
        for value in purchase_types:
            by_class.setdefault(classify_purchase_type(value), []).append(value)

        rows: list[dict[str, Any]] = []
        eligible = [s for s in services if s in COMMITMENT_SERVICES]
        record_filter = _dim("RECORD_TYPE", USAGE_RECORD_TYPES)
        detail_groups = ["USAGE_TYPE", "OPERATION"]
        for service in eligible:
            for klass, values in by_class.items():
                filter_ = _and(
                    _dim("SERVICE", [service]), _dim("PURCHASE_TYPE", values), record_filter
                )
                for day, keys, metrics in self.cost_and_usage(start, end, detail_groups, filter_):
                    row = self.parse_group(day, keys, metrics)
                    if row:
                        rows.append(self._apply_detail(row, service, klass))

        others = [s for s in services if s not in COMMITMENT_SERVICES]
        if others:
            other_groups = ["SERVICE", "LINKED_ACCOUNT"]
            for day, keys, metrics in self.cost_and_usage(
                start, end, other_groups, _dim("SERVICE", others)
            ):
                row = self.parse_group(day, keys, metrics)
                if row:
                    rows.append(row)
        return usage_table(rows)

    def parse_group(
        self, day: date, keys: dict[str, str], metrics: dict[str, Any]
    ) -> dict[str, Any] | None:
        """One GetCostAndUsage group -> one usage row, for any combination of group-by keys."""
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        row: dict[str, Any] = {
            "charge_period_start": start,
            "charge_period_end": start + timedelta(days=1),
            "provider": "aws",
            "billing_account_id": self.payer,
            "charge_category": ChargeCategory.USAGE.value,
            "pricing_category": PricingCategory.ON_DEMAND.value,
            "usage_quantity": _amount(metrics, "UsageQuantity"),
            "usage_unit": metrics.get("UsageQuantity", {}).get("Unit"),
            "normalized_units": _amount(metrics, "NormalizedUsageAmount"),
            "billed_cost": _amount(metrics, "UnblendedCost"),
            "effective_cost": _amount(metrics, "AmortizedCost"),
        }
        if not any(row[k] for k in ("usage_quantity", "billed_cost", "effective_cost")):
            return None
        if "SERVICE" in keys:
            row["service_name"] = keys["SERVICE"]
            row["service_category"] = COMMITMENT_SERVICES.get(keys["SERVICE"])
        if "LINKED_ACCOUNT" in keys:
            row["sub_account_id"] = keys["LINKED_ACCOUNT"]
        if "REGION" in keys:
            row["region"] = keys["REGION"]
        if "INSTANCE_TYPE" in keys:
            row["instance_type"] = keys["INSTANCE_TYPE"]
        if "USAGE_TYPE" in keys:
            parsed = parse_usage_type(keys["USAGE_TYPE"])
            row["region"] = row.get("region") or parsed["region"]
            row["instance_type"] = row.get("instance_type") or parsed["instance_type"]
            row["tenancy"] = parsed["tenancy"]
            row["deployment_option"] = deployment_option(parsed["usage_kind"])
            if parsed["usage_kind"] and "Spot" in parsed["usage_kind"]:
                row["pricing_category"] = PricingCategory.SPOT.value
        if "OPERATION" in keys:
            row["operating_system"] = operating_system(keys["OPERATION"])
            row["database_engine"] = database_engine(keys["OPERATION"])
        if "USAGE_TYPE" in keys and "OPERATION" in keys:
            row["sku_id"] = f"{keys['USAGE_TYPE']}|{keys['OPERATION']}"
        row["instance_family"] = instance_family(row.get("instance_type"))
        if row["pricing_category"] == PricingCategory.ON_DEMAND.value:
            row["on_demand_equiv_cost"] = row["billed_cost"]
            row["list_cost"] = row["billed_cost"]
        return row

    def _apply_detail(self, row: dict[str, Any], service: str, klass: str) -> dict[str, Any]:
        row["service_name"] = service
        row["service_category"] = COMMITMENT_SERVICES[service]
        if klass == "spot":
            row["pricing_category"] = PricingCategory.SPOT.value
            row["on_demand_equiv_cost"] = row["list_cost"] = None
        elif klass in ("sp", "ri"):
            # SP-covered usage shows the on-demand rate as unblended cost; RI-covered usage
            # shows 0, so its on-demand equivalent is left for pricing to fill.
            od_equiv = row["billed_cost"] if klass == "sp" else None
            row.update(
                {
                    "pricing_category": PricingCategory.COMMITMENT.value,
                    "commitment_type": (
                        CommitmentType.SAVINGS_PLAN if klass == "sp" else CommitmentType.RESERVATION
                    ).value,
                    "commitment_status": CommitmentStatus.USED.value,
                    "billed_cost": 0.0,
                    "list_cost": od_equiv,
                    "on_demand_equiv_cost": od_equiv,
                }
            )
        return row


def iter_groups(resp: dict[str, Any], group_by: list[str] | None = None):
    names = [g["Key"] for g in resp.get("GroupDefinitions", [])] or group_by or []
    for result in resp.get("ResultsByTime", []):
        day = date.fromisoformat(result["TimePeriod"]["Start"])
        for group in result.get("Groups", []):
            yield day, dict(zip(names, group["Keys"], strict=True)), group["Metrics"]


def _amount(metrics: dict[str, Any], name: str) -> float | None:
    value = metrics.get(name, {}).get("Amount")
    return float(value) if value is not None else None
