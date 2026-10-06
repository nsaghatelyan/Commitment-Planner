"""Load a tenant's usage into hourly series per usage group, for the engine.

A usage group is one combination of provider / service / region / instance type / OS /
engine / deployment (plus which commitment covered it, when that commitment is ending).
Only usage the engine may commit to is loaded: on-demand usage (Spot excluded) and usage
covered by commitments that end within the expiry window (they are treated as ending).
Daily rows (API bootstrap) are spread evenly over their 24 hours.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import duckdb
import numpy as np
import pyarrow as pa

from app.collectors.aws.usage_types import aws_size_factor
from app.pricing.keys import usage_sku_key
from app.usage.query import connect
from app.usage.storage import UsageStore

GROUP_COLUMNS = (
    "provider",
    "service_name",
    "region",
    "instance_type",
    "instance_family",
    "operating_system",
    "tenancy",
    "database_engine",
    "deployment_option",
    "usage_unit",
    "usage_type",
    "operation",
)


@dataclass
class ExpiringCommitment:
    provider_commitment_id: str
    ctype: str  # "sp" | "ri"


@dataclass
class UsageGroup:
    attrs: dict[str, str | None]
    # "od" for on-demand usage, else the id of the (ending) commitment that covered it
    cover: str
    cover_type: str | None  # "sp" | "ri" | None
    od: np.ndarray  # on-demand-equivalent $ per hour
    units: np.ndarray  # normalized units per hour
    qty: np.ndarray  # usage quantity per hour (instances, vCores, 100 RU/s, ...)
    sku_key: str | None = None
    units_per_instance: float | None = None

    def __getattr__(self, name: str):
        attrs = self.__dict__.get("attrs")
        if attrs is not None and name in attrs:
            return attrs[name]
        raise AttributeError(name)


@dataclass
class UsageData:
    start: datetime  # first hour of the series
    hours: int
    as_of: date  # day after the last day with data
    first_day: date | None  # first day the tenant has any usage
    groups: list[UsageGroup] = field(default_factory=list)

    @property
    def history_days(self) -> int:
        return (self.as_of - self.first_day).days if self.first_day else 0


def data_bounds(con: duckdb.DuckDBPyConnection) -> tuple[date | None, date | None]:
    row = con.execute(
        "SELECT min(charge_period_start), max(charge_period_end) FROM usage "
        "WHERE charge_category = 'Usage'"
    ).fetchone()
    if not row or row[0] is None:
        return None, None
    first, last = row
    last_day = last.date() if last.hour == 0 and last.minute == 0 else last.date() + timedelta(1)
    return first.date(), last_day


def load_usage(
    store: UsageStore,
    tenant_id: str,
    expiring: list[ExpiringCommitment],
    history_days: int,
    as_of: date | None = None,
) -> UsageData:
    con = connect(store, tenant_id)
    try:
        first_day, last_day = data_bounds(con)
    except duckdb.IOException:  # no files for this tenant
        first_day, last_day = None, None
    as_of = as_of or last_day or date.today()  # noqa: DTZ011 - only when there is no data
    start = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC) - timedelta(days=history_days)
    hours = history_days * 24
    data = UsageData(start=start, hours=hours, as_of=as_of, first_day=first_day)
    if first_day is None:
        return data

    con.register(
        "expiring",
        pa.table(
            {
                "id": [e.provider_commitment_id for e in expiring] or [""],
                "ctype": [e.ctype for e in expiring] or [""],
            }
        ),
    )
    cols = ", ".join(GROUP_COLUMNS)
    rows = con.execute(
        f"""
        WITH src AS (
            SELECT u.*, e.id AS cover_id, e.ctype AS cover_type,
                   greatest(1, round((epoch(charge_period_end) - epoch(charge_period_start))
                                     / 3600))::INT AS dur
            FROM usage u
            LEFT JOIN expiring e
              ON u.commitment_id IS NOT NULL AND e.id <> ''
             AND (u.commitment_id = e.id OR u.commitment_id LIKE '%' || e.id)
            WHERE u.charge_category = 'Usage'
              AND u.charge_period_start >= ? AND u.charge_period_start < ?
              AND (u.pricing_category = 'On-Demand'
                   OR (u.pricing_category = 'Commitment-Based'
                       AND u.commitment_status = 'Used' AND e.id IS NOT NULL))
              AND u.service_name IS NOT NULL
        ),
        spread AS (
            SELECT *, unnest(range(dur)) AS off FROM src
        )
        SELECT {cols}, coalesce(cover_id, 'od') AS cover, cover_type,
               CAST((epoch(charge_period_start) - epoch(?::TIMESTAMPTZ)) / 3600 AS INT) + off AS h,
               sum(coalesce(on_demand_equiv_cost, list_cost, 0) / dur) AS od,
               sum(coalesce(normalized_units, 0) / dur) AS units,
               sum(coalesce(usage_quantity, 0) / dur) AS qty
        FROM spread
        GROUP BY ALL
        """,
        [start, start + timedelta(hours=hours), start],
    ).to_arrow_table()

    keys = list(GROUP_COLUMNS) + ["cover", "cover_type"]
    index: dict[tuple, UsageGroup] = {}
    columns = {name: rows.column(name).to_pylist() for name in rows.column_names}
    for i in range(rows.num_rows):
        h = columns["h"][i]
        if h is None or not 0 <= h < hours:
            continue
        key = tuple(columns[k][i] for k in keys)
        group = index.get(key)
        if group is None:
            attrs = {k: columns[k][i] for k in GROUP_COLUMNS}
            group = UsageGroup(
                attrs=attrs,
                cover=columns["cover"][i],
                cover_type=columns["cover_type"][i],
                od=np.zeros(hours),
                units=np.zeros(hours),
                qty=np.zeros(hours),
            )
            index[key] = group
        group.od[h] += columns["od"][i] or 0.0
        group.units[h] += columns["units"][i] or 0.0
        group.qty[h] += columns["qty"][i] or 0.0

    for group in index.values():
        a = group.attrs
        group.sku_key = usage_sku_key(
            a["provider"], a["service_name"], a["instance_type"], a["operating_system"],
            a["tenancy"], a["database_engine"], a["deployment_option"], a["usage_unit"],
            a["usage_type"], a["operation"],
        )  # fmt: skip
        if not group.units.any() and group.qty.any() and a["provider"] == "aws":
            factor = aws_size_factor(a["instance_type"]) if a["instance_type"] else 1
            group.units = group.qty * factor
        qty = group.qty.sum()
        group.units_per_instance = float(group.units.sum() / qty) if qty else None
        data.groups.append(group)
    return data
