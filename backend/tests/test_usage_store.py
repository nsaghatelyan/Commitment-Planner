from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.compute as pc

from app.usage import usage_table
from app.usage.query import connect


def _rows(day: date, n: int, cost: float = 1.0):
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [
        {
            "charge_period_start": start + timedelta(hours=h),
            "provider": "aws",
            "service_name": "EC2",
            "billed_cost": cost,
            "effective_cost": cost,
            "tags": {"env": "prod"},
        }
        for h in range(n)
    ]


def test_write_uses_contract_layout(store):
    store.write("t1", "aws", usage_table(_rows(date(2026, 9, 1), 3)))
    expected = Path(store.root) / "tenant=t1/provider=aws/date=2026-09-01/part-0.parquet"
    assert expected.exists()
    assert store.root.endswith("/usage_hourly")


def test_write_replaces_whole_day_partitions(store):
    store.write("t1", "aws", usage_table(_rows(date(2026, 9, 1), 24) + _rows(date(2026, 9, 2), 24)))
    counts = store.write("t1", "aws", usage_table(_rows(date(2026, 9, 2), 5, cost=2.0)))
    assert counts == {date(2026, 9, 2): 5}
    table = store.read("t1", "aws")
    assert table.num_rows == 29
    assert store.dates("t1", "aws") == {date(2026, 9, 1), date(2026, 9, 2)}
    assert store.dates("t1", "azure") == set()


def test_duckdb_reads_with_hive_partitioning(store):
    store.write("t1", "aws", usage_table(_rows(date(2026, 9, 1), 24)))
    store.write(
        "t1", "azure", usage_table([{**r, "provider": "azure"} for r in _rows(date(2026, 9, 1), 2)])
    )
    con = connect(store, "t1")
    rows = con.execute(
        "SELECT provider, count(*), sum(billed_cost), min(CAST(date AS VARCHAR)), "
        "any_value(tags['env']) FROM usage GROUP BY provider ORDER BY provider"
    ).fetchall()
    assert rows == [
        ("aws", 24, 24.0, "2026-09-01", "prod"),
        ("azure", 2, 2.0, "2026-09-01", "prod"),
    ]


def test_read_missing_tenant_is_empty(store):
    assert store.read("nobody").num_rows == 0
    assert pc.sum(store.read("nobody").column("billed_cost")).as_py() is None
