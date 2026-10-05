"""Engine unit tests and the Phase 3 acceptance criteria, run on the synthetic profiles."""

from datetime import date, timedelta

import numpy as np
import pytest

from app.engine.optimize import choose_capacity, simulate, term_option
from app.engine.stats import analyze, find_step
from app.synthetic import generate
from app.synthetic.analysis import analyze_synthetic
from app.synthetic.catalog import discount
from app.usage.storage import UsageStore

TARGETS = {"conservative": 98.0, "balanced": 95.0, "aggressive": 90.0}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """One synthetic tenant + balanced analysis per profile, shared by the tests below."""
    out = {}
    for profile in ("small", "medium", "large", "startup"):
        tenant = generate(profile)
        store = UsageStore(str(tmp_path_factory.mktemp(profile)))
        out[profile] = (tenant, analyze_synthetic(tenant, store), store)
    return out


def recs(analysis, **match):
    return analysis.result.find(**match)


def pool_report(analysis, kind, family=None, service=None, region=None):
    return next(
        p
        for p in analysis.result.pools
        if p.pool.kind == kind
        and (family is None or p.pool.family == family)
        and (service is None or p.pool.service == service)
        and (region is None or p.pool.region == region)
    )


# ---------------------------------------------------------------- units


def test_sizing_profiles_and_target():
    rng = np.random.default_rng(1)
    hours = np.arange(24 * 60)
    series = 40 + 20 * (np.sin(hours / 24 * 2 * np.pi) > 0) + rng.normal(0, 2, len(hours))
    sizes = {p: choose_capacity(series, 0.38, p, TARGETS[p] / 100, 1.0) for p in TARGETS}
    assert sizes["conservative"].capacity <= sizes["balanced"].capacity
    assert sizes["balanced"].capacity <= sizes["aggressive"].capacity
    for p, s in sizes.items():
        assert TARGETS[p] / 100 <= s.utilization <= 1.0
        assert s.capacity == int(s.capacity)  # whole units
    # a flat series is committed in full at 100% utilization
    flat = choose_capacity(np.full(500, 8.0), 0.4, "balanced", 0.95, 1.0)
    assert flat.capacity == 8 and flat.utilization == 1.0
    # nothing to commit to when usage is zero most of the time
    business = np.tile(np.r_[np.zeros(14), np.full(10, 5.0)], 30)
    assert choose_capacity(business, 0.4, "balanced", 0.95, 1.0) is None


def test_simulation_and_term_math():
    series = np.array([10.0, 10, 5, 0])
    util, covered = simulate(series, 10)
    assert util == pytest.approx(25 / 40) and covered == pytest.approx(25 / 4)
    opt = term_option(
        10, 10, od_per_unit=1.0, discount=0.4, upfront_share=1.0, term=12, payment="all_upfront"
    )
    assert opt.hourly_cost == pytest.approx(6.0)
    assert opt.upfront == pytest.approx(6.0 * 12 * 730)
    assert opt.monthly_savings == pytest.approx(4.0 * 730)
    assert opt.breakeven_month == pytest.approx(6.0 * 12 / 10)  # upfront / monthly od avoided
    assert term_option(10, 10, 1.0, 0.4, 0.0, 12, "no_upfront").breakeven_month == 0


def test_step_vs_ramp():
    drop = np.r_[np.full(60, 50.0), np.full(30, 0.5)]
    assert find_step(drop)[0] == 60
    ramp = np.r_[np.full(40, 10.0), np.linspace(10, 40, 50)]
    assert find_step(ramp) is None
    st = analyze(np.repeat(ramp, 24), 0, 90)
    assert st.sizing_reason == "ramp" and st.sizing_start_day == 60


def test_business_hours_detection():
    day = np.r_[np.zeros(8), np.full(12, 4.0), np.zeros(4)]
    st = analyze(np.tile(day, 35), 0, 90)
    assert st.business_hours_ratio > 3 or st.business_hours_ratio == float("inf")


# ---------------------------------------------------------------- acceptance: AWS


def test_expiring_compute_sp_is_renewed_and_urgent(runs):
    _, a, _ = runs["small"]
    (sp,) = recs(a, kind="aws_sp_compute")
    assert sp.action == "renew" and sp.urgent  # ends in 10 days
    assert sp.hourly_commitment > 0 and "ending" in sp.rationale
    _, large, _ = runs["large"]
    (sp,) = recs(large, kind="aws_sp_compute")
    assert sp.action == "renew" and not sp.urgent  # ends in 65 days


def test_flat_unreserved_rds_and_elasticache_get_1y_ris(runs):
    _, a, _ = runs["small"]
    (pg,) = recs(a, kind="aws_ri", instance_type="db.r6g.xlarge")
    assert pg.term_months == 12 and pg.quantity == 1 and pg.action == "purchase"
    assert "PostgreSQL" in pg.details["pool_label"]
    (cache,) = recs(a, kind="aws_ri", service="elasticache")
    assert cache.quantity == 3 and cache.term_months == 12


def test_cluster_with_expiring_partial_reservation_renew_and_add(runs):
    _, a, _ = runs["large"]
    (cache,) = recs(a, kind="aws_ri", service="elasticache")
    assert cache.action == "renew" and cache.quantity == 6
    assert cache.details["renew_quantity"] == 4 and cache.details["add_quantity"] == 2
    assert "Renew 4" in cache.rationale and "add 2" in cache.rationale


def test_stranded_ri_after_migration(runs):
    tenant, a, _ = runs["large"]
    (exchange,) = recs(a, action="exchange", provider="aws")
    assert exchange.details["from_instance_type"] == "m5.xlarge"
    assert exchange.instance_type == "m7i.xlarge"
    assert "Do not buy more m5" in exchange.rationale
    stranded = [
        u for u in a.summary["underutilized_commitments"] if u["instance_type"] == "m5.xlarge"
    ]
    assert stranded and stranded[0]["status"].startswith("stranded")
    # No new m5 commitments of any kind.
    assert not [
        r
        for r in a.result.recommendations
        if r.action != "exchange" and (r.instance_family == "m5" or r.instance_type == "m5.xlarge")
    ]
    # m7i is sized only on data after the switch (day 120 of 180).
    switch = (tenant.start + timedelta(days=120)).isoformat()
    m7i = pool_report(a, "aws_ri", family="m7i")
    assert m7i.recommendation.details["stability"]["sizing_from"] >= switch
    # the exchange is used first; only the remainder is bought
    assert exchange.quantity + m7i.recommendation.quantity <= 50


def test_growing_workload_sizes_above_lookback_floor(runs):
    _, a, _ = runs["large"]
    report = pool_report(a, "aws_ri", family="c6i")
    sizes, st = report.sizes, report.stability
    assert st.p10_lookback < sizes["balanced"] < sizes["aggressive"]
    assert sizes["conservative"] <= sizes["balanced"]


def test_things_that_must_not_drive_commitments(runs):
    _, a, _ = runs["large"]
    families = {r.instance_family for r in a.result.recommendations}
    types = {r.instance_type for r in a.result.recommendations}
    assert "t3" not in families  # staging, business hours only
    assert "c7i" not in families  # nightly batch
    assert "c5.2xlarge" not in types  # Spot fleet
    assert "m7g" not in families  # started 7 days ago
    reasons = {p.pool.family: p.skipped for p in a.result.pools if p.pool.kind == "aws_ri"}
    assert reasons["t3"].startswith("no steady floor")
    assert reasons["c7i"].startswith("no steady floor")
    assert reasons["m7g"].startswith("too new")


def test_windows_is_sp_only_and_sql_server_needs_exact_type(runs):
    _, a, _ = runs["large"]
    assert not [
        p
        for p in a.result.pools
        if p.pool.kind == "aws_ri" and p.pool.service == "ec2" and p.pool.detail != "Linux"
    ]
    (m6i_sp,) = recs(a, kind="aws_sp_ec2", instance_family="m6i")
    assert m6i_sp.hourly_commitment > 0
    sql = next(
        r for r in recs(a, kind="aws_ri", service="rds") if "SQL Server" in r.details["pool_label"]
    )
    assert sql.instance_type == "db.r5.xlarge" and sql.details["pool"]["measure"] == "qty"
    assert sql.details["renew_quantity"] == 2 and sql.details["add_quantity"] == 1


# ---------------------------------------------------------------- acceptance: Azure


def test_azure_reservations(runs):
    _, a, _ = runs["medium"]
    (d4s,) = recs(a, kind="azure_ri", instance_type="Standard_D4s_v5")
    assert d4s.quantity == 5  # 20 running, 15 reserved
    (ramp,) = recs(a, kind="azure_ri", instance_type="Standard_E4s_v5")
    floor_instances = ramp.details["percentiles"]["p10"] / 2  # 2 units per D/E 4-vCPU VM
    assert 5 < ramp.quantity <= floor_instances < 20
    assert ramp.details["stability"]["sizing_reason"] == "ramp"
    (cosmos,) = recs(a, kind="azure_ri", service="Azure Cosmos DB")
    assert cosmos.quantity == 100  # 10k RU/s floor, not the 40k peaks
    assert {r.instance_type: r.quantity for r in recs(a, service="SQL Database")} == {
        "SQLDB_GP_Gen5": 16,
        "SQLDB_BC_Gen5": 8,
    }
    (app,) = recs(a, service="Azure App Service")
    assert app.instance_type == "P1v3" and app.quantity == 4
    (pg,) = recs(a, service="Azure Database for PostgreSQL")
    assert pg.quantity == 2


def test_azure_ri_exchange(runs):
    _, a, _ = runs["medium"]
    (exchange,) = recs(a, action="exchange")
    assert exchange.details["from_instance_type"] == "Standard_D4s_v4"
    assert exchange.instance_type == "Standard_D4as_v5" and exchange.quantity == 10
    assert "exchange" in exchange.rationale.lower()
    assert not recs(a, action="purchase", instance_type="Standard_D4as_v5")


# ---------------------------------------------------------------- short history, invariants


def test_short_history(runs):
    _, a, _ = runs["startup"]
    assert a.summary["warnings"] and "21 days" in a.summary["warnings"][0]
    assert a.summary["risk_profile"] == "conservative"
    for r in a.result.recommendations:
        assert r.term_months == 12 and r.payment_option in ("no_upfront", None)
    assert not recs(a, instance_type="Standard_D4s_v5")  # started on day 7
    assert not recs(a, instance_family="m6i")  # started on day 10


@pytest.mark.parametrize("profile", ["conservative", "balanced", "aggressive"])
def test_utilization_invariant_per_profile(tmp_path, profile):
    tenant = generate("small", days=60)
    a = analyze_synthetic(tenant, UsageStore(str(tmp_path)), profile)
    plan = [r for r in a.result.recommendations if r.action in ("purchase", "renew")]
    assert plan
    for r in plan:
        assert TARGETS[profile] <= r.expected_utilization_pct <= 100.0
        for opt in r.details["options"]:
            # projected savings never exceed the on-demand cost of what is covered
            assert opt["monthly_savings"] <= opt["monthly_cost_after"] / (1 - opt["discount"])


def test_invariants_on_all_profiles(runs):
    for _, a, _ in runs.values():
        for r in a.result.recommendations:
            if r.expected_utilization_pct is not None:
                assert r.expected_utilization_pct <= 100.0
            if r.action in ("purchase", "renew"):
                assert r.expected_utilization_pct >= 95.0 or a.summary["risk_profile"] != "balanced"
                assert r.rationale and r.details["chart"]["hourly"]
                assert r.risk in ("low", "med", "high")


def test_discounts_come_from_the_price_table(runs):
    _, a, _ = runs["small"]
    (pg,) = recs(a, kind="aws_ri", instance_type="db.r6g.xlarge")
    by_option = {
        (o["term_months"], o["payment_option"]): o["discount"] for o in pg.details["options"]
    }
    assert by_option[(12, "no_upfront")] == pytest.approx(discount("aws", "ri", 12))
    assert by_option[(36, "all_upfront")] == pytest.approx(discount("aws", "ri", 36, "all_upfront"))
    assert len(by_option) == 6  # 1y/3y x No/Partial/All Upfront


def test_summary(runs):
    tenant, a, _ = runs["large"]
    s = a.summary
    assert s["projected_annual_savings"] == pytest.approx(12 * s["projected_monthly_savings"])
    assert s["effective_savings_rate_new_pct"] > s["effective_savings_rate_current_pct"]
    assert [p["rank"] for p in s["purchase_plan"]] == list(range(1, len(s["purchase_plan"]) + 1))
    assert {e["kind"] for e in s["expiring_commitments"]} >= {"aws_sp_compute", "aws_ri"}
    assert s["coverage_pct"] is not None and 0 < s["coverage_pct"] < 100
    comparison = s["native_comparison"]
    assert comparison["by_kind"] and comparison["explanations"]
    assert any("30 days" in e for e in comparison["explanations"])
    assert date.fromisoformat(s["as_of"]) == tenant.end


def test_backtest_replays_the_plan_on_unseen_days(runs):
    from app.engine.config import EngineConfig
    from app.services.backtest import backtest
    from app.synthetic.analysis import commitment_infos, price_rows

    tenant, _, store = runs["large"]
    bt = backtest(
        store,
        tenant.tenant_id,
        commitment_infos(tenant),
        price_rows(tenant),
        EngineConfig(),
        tenant.end,
    )
    assert bt["available"] and bt["train_until"] == (tenant.end - timedelta(days=30)).isoformat()
    assert bt["recommendations"]
    assert 80 <= bt["savings_accuracy_pct"] <= 110
    for r in bt["recommendations"]:
        assert 0 <= r["realized_utilization_pct"] <= 100
    tenant, _, store = runs["startup"]
    short = backtest(store, tenant.tenant_id, [], price_rows(tenant), EngineConfig(), tenant.end)
    assert not short["available"] and "history" in short["reason"]


def _t2_nano(term_months, payment_option, savings):
    return {
        "provider": "aws",
        "kind": "aws_ri",
        "term_months": term_months,
        "payment_option": payment_option,
        "lookback_days": 30,
        "quantity": 2,
        "estimated_monthly_savings": savings,
    }


NATIVE_T2_NANO = [
    _t2_nano(36, "all_upfront", 5.29),
    _t2_nano(36, "no_upfront", 4.81),
    _t2_nano(12, "all_upfront", 3.63),
    _t2_nano(12, "no_upfront", 3.20),
]


def test_native_comparison_counts_one_term_payment_option():
    """The same opportunity comes back once per term/payment option; they aren't additive."""
    from types import SimpleNamespace

    from app.engine.summary import compare_native

    result = SimpleNamespace(recommendations=[], pools=[])
    (row,) = compare_native(result, NATIVE_T2_NANO)["by_kind"]
    # No engine plan: the best native option.
    assert (row["native_term_months"], row["native_payment_option"]) == (36, "all_upfront")
    assert row["native_monthly_savings"] == pytest.approx(5.29)
    assert row["native_quantity"] == 2 and row["native_count"] == 1
    assert row["native_options"] == 4

    engine = SimpleNamespace(
        provider="aws",
        kind="aws_ri",
        plan_rank=1,
        hourly_commitment=None,
        quantity=2,
        monthly_savings=3.0,
        term_months=12,
        payment_option="no_upfront",
        action="buy",
    )
    result = SimpleNamespace(recommendations=[engine], pools=[])
    (row,) = compare_native(result, NATIVE_T2_NANO)["by_kind"]
    # With an engine plan: the native option it chose, so both columns are like for like.
    assert (row["native_term_months"], row["native_payment_option"]) == (12, "no_upfront")
    assert row["native_monthly_savings"] == pytest.approx(3.20)


def test_small_savings_are_held_back_and_reported(runs):
    """Below the savings floor a recommendation is held back, not dropped: the summary lists it."""
    from app.engine.config import EngineConfig
    from app.services.analysis import analyze
    from app.synthetic.analysis import commitment_infos, price_rows

    tenant, a, store = runs["large"]
    # A large account keeps the absolute $5 floor.
    assert a.summary["savings_floor_monthly"] == 5.0
    buys = [
        r for r in a.result.recommendations if r.plan_rank is not None and r.action == "purchase"
    ]
    smallest = min(buys, key=lambda r: r.monthly_savings)

    def run(config):
        return analyze(
            store, tenant.tenant_id, commitment_infos(tenant), price_rows(tenant), config,
            as_of=tenant.end, native=[],
        )  # fmt: skip

    # Raise the bar just above the smallest buy (no share-based lowering).
    raised = run(EngineConfig(min_monthly_savings=smallest.monthly_savings + 0.01,
                              min_savings_share=1.0))  # fmt: skip
    held = raised.summary["below_threshold"]
    assert any(h["monthly_savings"] == smallest.monthly_savings for h in held)
    assert all(h["monthly_savings"] < raised.summary["savings_floor_monthly"] for h in held)
    planned = {p["description"] for p in raised.summary["purchase_plan"]}
    assert not planned & {h["description"] for h in held}

    # A tiny share of spend lowers the bar to the absolute floor.
    lowered = run(EngineConfig(min_monthly_savings=1000.0, min_savings_share=1e-9))
    assert lowered.summary["savings_floor_monthly"] == 1.0
