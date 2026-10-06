"""Normalized usage schema shared by both clouds. Column names follow FOCUS where it has one."""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

import pyarrow as pa


class PricingCategory(StrEnum):
    ON_DEMAND = "On-Demand"
    COMMITMENT = "Commitment-Based"
    SPOT = "Spot"


class ChargeCategory(StrEnum):
    USAGE = "Usage"
    PURCHASE = "Purchase"
    TAX = "Tax"
    CREDIT = "Credit"
    ADJUSTMENT = "Adjustment"


class CommitmentType(StrEnum):
    SAVINGS_PLAN = "Savings Plan"
    RESERVATION = "Reservation"


class CommitmentStatus(StrEnum):
    USED = "Used"
    UNUSED = "Unused"


# Row conventions (shared with the synthetic dataset):
# - Covered usage: billed_cost=0, effective_cost=amortized commitment rate,
#   pricing_category=Commitment-Based, commitment_status=Used.
# - Unused commitment: commitment_status=Unused, the unused amortized cost in effective_cost.
# - Purchase fees: charge_category=Purchase with billed_cost (effective_cost=0).
# - For each commitment-hour, Used + Unused effective_cost = the commitment's amortized hourly cost.


TIMESTAMP = pa.timestamp("us", tz="UTC")

USAGE_SCHEMA = pa.schema(
    [
        # Start of the charge period: the hour for export data, the day for API bootstrap data.
        pa.field("charge_period_start", TIMESTAMP, nullable=False),
        pa.field("charge_period_end", TIMESTAMP),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("billing_account_id", pa.string()),
        pa.field("sub_account_id", pa.string()),
        pa.field("sub_account_name", pa.string()),
        pa.field("region", pa.string()),
        pa.field("service_category", pa.string()),
        pa.field("service_name", pa.string()),
        pa.field("sku_id", pa.string()),
        # AWS usage type and operation ("USE2-InstanceUsage:db.m5.large",
        # "CreateDBInstance:0002"): the exact join key into AWS prices for non-EC2 services.
        pa.field("usage_type", pa.string()),
        pa.field("operation", pa.string()),
        pa.field("resource_id", pa.string()),
        pa.field("resource_name", pa.string()),
        pa.field("instance_family", pa.string()),
        pa.field("instance_type", pa.string()),
        pa.field("operating_system", pa.string()),
        pa.field("tenancy", pa.string()),
        pa.field("database_engine", pa.string()),
        # RDS: Single-AZ | Multi-AZ (Multi-AZ doubles normalized units).
        pa.field("deployment_option", pa.string()),
        pa.field("charge_category", pa.string()),
        pa.field("pricing_category", pa.string()),
        pa.field("commitment_id", pa.string()),
        pa.field("commitment_type", pa.string()),
        pa.field("commitment_status", pa.string()),
        pa.field("usage_quantity", pa.float64()),
        pa.field("usage_unit", pa.string()),
        pa.field("normalized_units", pa.float64()),
        pa.field("list_cost", pa.float64()),
        pa.field("billed_cost", pa.float64()),
        # Amortized: commitment purchases spread over the usage they covered.
        pa.field("effective_cost", pa.float64()),
        pa.field("on_demand_equiv_cost", pa.float64()),
        pa.field("tags", pa.map_(pa.string(), pa.string())),
    ]
)

COLUMNS = USAGE_SCHEMA.names


def empty_usage_table() -> pa.Table:
    return USAGE_SCHEMA.empty_table()


def usage_table(rows: Iterable[Mapping[str, Any]]) -> pa.Table:
    """Build a schema-conformant table from dict rows; missing keys become null."""
    columns: dict[str, list[Any]] = {name: [] for name in COLUMNS}
    for row in rows:
        for name in COLUMNS:
            value = row.get(name)
            if name == "tags" and isinstance(value, Mapping):
                value = list(value.items())
            columns[name].append(value)
    return pa.Table.from_pydict(columns, schema=USAGE_SCHEMA)


def conform(table: pa.Table) -> pa.Table:
    """Reorder/cast a table to USAGE_SCHEMA, adding any missing columns as nulls."""
    arrays = []
    for field in USAGE_SCHEMA:
        if field.name in table.column_names:
            arrays.append(table.column(field.name).cast(field.type))
        else:
            arrays.append(pa.nulls(table.num_rows, field.type))
    return pa.Table.from_arrays(arrays, schema=USAGE_SCHEMA)
