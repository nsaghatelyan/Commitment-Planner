import duckdb
import pytest

from app.usage.normalize import detect_format, instance_family, normalize
from app.usage.schema import USAGE_SCHEMA
from tests.conftest import fixture_parquet


def _q(table, sql):
    con = duckdb.connect()
    con.register("t", table)
    return con.execute(sql).fetchall()


def test_aws_focus_export():
    raw = fixture_parquet("aws/focus_1_0_export.parquet")
    assert detect_format(raw) == "focus"
    t = normalize(raw, "aws")
    assert t.schema == USAGE_SCHEMA
    rows = {
        (r["resource_name"], r["charge_category"], r["commitment_status"]): r for r in t.to_pylist()
    }
    covered = rows[("web-02", "Usage", "Used")]
    assert covered["billed_cost"] == 0
    assert covered["effective_cost"] == pytest.approx(0.1286)
    assert covered["pricing_category"] == "Commitment-Based"
    assert covered["commitment_type"] == "Savings Plan"
    assert covered["instance_type"] == "m5.xlarge"
    assert covered["instance_family"] == "m5"
    assert covered["sub_account_name"] == "prod"
    assert covered["usage_unit"] == "Hrs"
    assert dict(covered["tags"]) == {"env": "prod"}
    unused = rows[(None, "Usage", "Unused")]
    assert unused["effective_cost"] == pytest.approx(0.0714)
    assert unused["on_demand_equiv_cost"] is None
    # Used + Unused effective cost = the SP's hourly commitment (0.2)
    assert covered["effective_cost"] + unused["effective_cost"] == pytest.approx(0.2)
    purchase = rows[(None, "Purchase", None)]
    assert purchase["billed_cost"] == pytest.approx(0.2)
    assert purchase["effective_cost"] == 0
    assert rows[("spark-01", "Usage", None)]["pricing_category"] == "Spot"


def test_cur2_export_applies_row_conventions():
    raw = fixture_parquet("aws/cur_2_0_export.parquet")
    assert detect_format(raw) == "cur2"
    t = normalize(raw, "aws")
    assert t.schema == USAGE_SCHEMA
    # SavingsPlanNegation dropped; each recurring fee -> Purchase + Unused rows.
    got = _q(
        t,
        "SELECT charge_category, pricing_category, commitment_type, commitment_status, "
        "round(billed_cost, 4), round(effective_cost, 4) FROM t ORDER BY 1, 2, 3, 4, 5",
    )
    assert got == [
        ("Purchase", "Commitment-Based", "Reservation", None, 0.24, 0.0),
        ("Purchase", "Commitment-Based", "Savings Plan", None, 0.2, 0.0),
        ("Tax", "On-Demand", None, None, 0.05, 0.05),
        ("Usage", "Commitment-Based", "Reservation", "Unused", 0.0, 0.12),
        ("Usage", "Commitment-Based", "Reservation", "Used", 0.0, 0.12),
        ("Usage", "Commitment-Based", "Savings Plan", "Unused", 0.0, 0.0714),
        ("Usage", "Commitment-Based", "Savings Plan", "Used", 0.0, 0.1286),
        ("Usage", "On-Demand", None, None, 0.192, 0.192),
    ]
    # Billed total matches the invoice: usage + fees + tax (negation and covered cancel out).
    assert _q(t, "SELECT round(sum(billed_cost), 4) FROM t") == [(0.682,)]
    # Per commitment-hour: Used + Unused effective = amortized hourly cost.
    sp = _q(
        t,
        "SELECT round(sum(effective_cost), 4) FROM t "
        "WHERE commitment_type = 'Savings Plan' AND commitment_status IS NOT NULL",
    )
    assert sp == [(0.2,)]
    ri = _q(
        t,
        "SELECT round(sum(effective_cost), 4) FROM t "
        "WHERE commitment_type = 'Reservation' AND commitment_status IS NOT NULL",
    )
    assert ri == [(0.24,)]
    od = _q(
        t,
        "SELECT on_demand_equiv_cost, operating_system, sub_account_name FROM t "
        "WHERE pricing_category = 'On-Demand' AND charge_category = 'Usage'",
    )
    assert od == [(0.192, "Linux", "prod")]


def test_azure_focus_export():
    raw = fixture_parquet("azure/focus_export.parquet")
    t = normalize(raw, "azure")
    assert t.schema == USAGE_SCHEMA
    rows = t.to_pylist()
    assert {r["provider"] for r in rows} == {"azure"}
    usage = [r for r in rows if r["resource_name"] == "vm-api-01"]
    assert {r["instance_type"] for r in usage} == {"Standard_D4s_v3"}
    assert {r["instance_family"] for r in usage} == {"Ds_v3"}
    assert dict(usage[0]["tags"]) == {"app": "api", "env": "prod"}
    covered = next(r for r in rows if r["commitment_status"] == "Used")
    assert covered["billed_cost"] == 0 and covered["commitment_type"] == "Reservation"
    assert covered["charge_period_start"].isoformat() == "2026-09-01T00:00:00+00:00"
    purchase = next(r for r in rows if r["charge_category"] == "Purchase")
    assert purchase["billed_cost"] == 1200.0 and purchase["effective_cost"] == 0


def test_unknown_format_is_rejected():
    import pyarrow as pa

    with pytest.raises(ValueError, match="Unrecognized"):
        normalize(pa.table({"foo": [1]}), "aws")


@pytest.mark.parametrize(
    ("itype", "family"),
    [
        ("m5.large", "m5"),
        ("db.r6g.xlarge", "r6g"),
        ("cache.r6g.large", "r6g"),
        ("Standard_D4s_v5", "Ds_v5"),
        ("Standard_E8-4s_v3", "Es_v3"),
        ("Standard_B2s", "Bs"),
        (None, None),
    ],
)
def test_instance_family(itype, family):
    assert instance_family(itype) == family
