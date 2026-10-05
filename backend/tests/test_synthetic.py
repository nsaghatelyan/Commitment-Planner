from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from app.collectors.aws.commitments import amortized_hourly
from app.synthetic import PROFILES, generate
from app.usage.schema import USAGE_SCHEMA


@pytest.fixture(scope="module")
def tenants():
    return {
        p: generate(p, end=date(2026, 10, 1), days=14 if p != "startup" else None) for p in PROFILES
    }


def _q(tenant, sql):
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.register("u", tenant.usage)
    return con.execute(sql).fetchall()


def test_profiles_shape(tenants):
    small, medium, large, startup = (tenants[p] for p in ("small", "medium", "large", "startup"))
    assert {c.provider for c in small.connections} == {"aws"}
    assert [(c.provider, c.agreement_type) for c in medium.connections] == [("azure", "ea")]
    assert {(c.provider, c.agreement_type) for c in large.connections} == {
        ("aws", None),
        ("azure", "mca"),
    }
    assert len([a for c in large.connections if c.provider == "aws" for a in c.accounts]) > 1
    assert {(c.provider, c.agreement_type) for c in startup.connections} == {
        ("aws", None),
        ("azure", "payg"),
    }
    assert startup.days == 21 and startup.commitments == []
    for t in tenants.values():
        assert t.usage.schema == USAGE_SCHEMA


def test_large_profile_features(tenants):
    large = tenants["large"]
    assert _q(large, "SELECT count(*) > 0 FROM u WHERE pricing_category = 'Spot'") == [(True,)]
    assert _q(large, "SELECT count(*) > 0 FROM u WHERE operating_system = 'Windows'") == [(True,)]
    assert _q(
        large,
        "SELECT count(*) > 0 FROM u WHERE database_engine = 'SQL Server SE' "
        "AND deployment_option = 'Multi-AZ' AND commitment_status = 'Used'",
    ) == [(True,)]
    kinds = {c.kind for c in large.commitments}
    assert {"aws_ri", "aws_sp_ec2", "aws_sp_compute", "azure_ri", "azure_sp_compute"} <= kinds
    # Multi-AZ doubles normalized units.
    (units,) = _q(
        large,
        "SELECT max(normalized_units) FROM u WHERE instance_type = 'db.r5.xlarge' "
        "AND deployment_option = 'Multi-AZ'",
    )[0]
    assert units == pytest.approx(16)


@pytest.fixture(scope="module")
def large_155():
    """Long enough to include the day-120 migration and the day-150 purchase."""
    return generate("large", end=date(2026, 10, 1), days=155)


def test_stranded_ri_after_migration(large_155):
    m5 = next(c for c in large_155.commitments if c.instance_type == "m5.xlarge")
    util = {
        u.date: u.utilization_pct
        for u in large_155.utilization
        if u.provider_commitment_id == m5.provider_commitment_id
    }
    days = sorted(util)
    assert util[days[100]] == 100  # before day 120 the m5 fleet uses it fully
    assert util[days[-1]] == 0  # after migrating to m7i it is stranded


def test_commitment_hour_invariant(tenants):
    """For each commitment-hour, Used + Unused effective cost = amortized hourly cost."""
    for t in tenants.values():
        sums = _q(
            t,
            """
            SELECT commitment_id, charge_period_start, sum(effective_cost)
            FROM u WHERE commitment_status IN ('Used', 'Unused')
            GROUP BY 1, 2""",
        )
        expected = {c.provider_commitment_id: float(amortized_hourly(c)) for c in t.commitments}
        start = datetime(t.start.year, t.start.month, t.start.day, tzinfo=UTC)
        end = start + timedelta(days=t.days)
        active_hours = sum(
            max(0, (min(end, c.end_at) - max(start, c.start_at)).total_seconds() // 3600)
            for c in t.commitments
        )
        assert len(sums) == active_hours
        for cid, _, total in sums:
            assert total == pytest.approx(expected[cid], rel=1e-6, abs=1e-6)


def test_row_conventions(tenants):
    for t in tenants.values():
        assert _q(
            t, "SELECT count(*) FROM u WHERE commitment_status = 'Used' AND billed_cost <> 0"
        ) == [(0,)]
        assert _q(
            t,
            "SELECT count(*) FROM u WHERE charge_category = 'Purchase' "
            "AND (billed_cost <= 0 OR effective_cost <> 0)",
        ) == [(0,)]
        assert _q(
            t,
            "SELECT count(*) FROM u WHERE commitment_status = 'Unused' "
            "AND (billed_cost <> 0 OR effective_cost <= 0)",
        ) == [(0,)]
    # A partly covered resource-hour is split into a covered row and an on-demand row.
    split = _q(
        tenants["large"],
        """
        SELECT count(*) FROM (
            SELECT resource_id, charge_period_start FROM u WHERE resource_id IS NOT NULL
            GROUP BY 1, 2
            HAVING count(*) FILTER (WHERE commitment_status = 'Used') > 0
               AND count(*) FILTER (WHERE pricing_category = 'On-Demand') > 0)""",
    )
    assert split[0][0] > 0


def test_upfront_purchase_inside_window(large_155):
    rows = _q(large_155, "SELECT max(billed_cost) FROM u WHERE charge_category = 'Purchase'")
    # The 1-year all-upfront $0.8/h Compute SP bought on day 150.
    assert rows[0][0] == pytest.approx(0.8 * 12 * 730, rel=1e-6)


def test_deterministic():
    a = generate("small", days=3)
    b = generate("small", days=3)
    assert a.tenant_id == b.tenant_id
    assert a.usage.equals(b.usage)
