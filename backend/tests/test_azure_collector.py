import gzip
import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pyarrow.csv as pacsv
import pytest

from app.collectors.azure import AzureCollector
from app.collectors.azure.export import latest_runs
from app.collectors.azure.http import AzureHttp, AzureHttpError
from app.collectors.azure.query import QueryBootstrap, merge_cost_types, month_chunks
from app.collectors.azure.scopes import validate_scope
from app.collectors.cache import CountingCaller
from app.collectors.types import CallStats
from app.pricing.azure import MeterCatalog
from tests.conftest import FIXTURES, fixture_json, fixture_parquet

EA_SCOPE = "/providers/Microsoft.Billing/billingAccounts/84251234"
SUB = "3f2a1b0c-1111-2222-3333-444455556666"
METER = "9f1e2d3c-0000-4000-8000-00000000d4s3"
SP_ORDER = "cccc3333-0000-0000-0000-000000000003"
RI_ORDER = "aaaa1111-0000-0000-0000-000000000001"


class Token:
    token = "test-token"


class Credential:
    def get_token(self, *scopes):
        assert scopes == ("https://management.azure.com/.default",)
        return Token()


class Router:
    """httpx MockTransport handler serving fixtures by path."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.query_bodies: list[dict] = []
        self.fail_first: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        path = url.path
        if url.host == "management.azure.com":
            assert request.headers["authorization"] == "Bearer test-token"
        if path in self.fail_first:
            self.fail_first.discard(path)
            return httpx.Response(429, headers={"retry-after": "2"})
        if url.host == "prices.azure.com":
            return _json("azure/retail_prices_meter.json")
        if path.endswith("/providers/Microsoft.CostManagement/query"):
            return httpx.Response(200, json=self.query(request))
        if path == "/subscriptions":
            return _json("azure/subscriptions.json")
        if path == "/providers/Microsoft.Capacity/reservationOrders":
            return _json("azure/reservation_orders.json")
        if path.endswith("/reservations"):
            return _json("azure/reservations.json")
        if path.startswith("/providers/Microsoft.Capacity/reservationOrders/"):
            assert url.params["$expand"] == "planInformation"
            return _json("azure/reservation_order_detail.json")
        if path == "/providers/Microsoft.BillingBenefits/savingsPlanOrders":
            return _json("azure/savings_plan_orders.json")
        if path.endswith("/savingsPlans"):
            return _json("azure/savings_plans.json")
        if path.endswith("/benefitUtilizationSummaries"):
            return _json("azure/benefit_utilization.json")
        if path.endswith("/benefitRecommendations"):
            return _json("azure/benefit_recommendations.json")
        if path.endswith("/reservationRecommendations"):
            return _json("azure/reservation_recommendations.json")
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})

    def query(self, request: httpx.Request) -> dict:
        body = json.loads(request.content)
        self.query_bodies.append(body)
        if "$skiptoken" in str(request.url):
            return fixture_json("azure/query_detail_ondemand_page2.json")
        groups = [g["name"] for g in body["dataset"]["grouping"]]
        flt = json.dumps(body["dataset"].get("filter", {}))
        if groups == ["MeterId", "SubscriptionId"] and '"OnDemand"' in flt:
            return fixture_json("azure/query_detail_ondemand.json")
        if groups == ["MeterId", "ChargeType"] and '"Reservation"' in flt:
            return fixture_json("azure/query_detail_reservation.json")
        if groups == ["ServiceName", "SubscriptionId"]:
            return fixture_json("azure/query_amortizedcost_daily_by_service.json")
        if groups == ["ServiceName", "PricingModel"]:
            return {
                "properties": {
                    "nextLink": None,
                    "columns": [
                        {"name": "Cost", "type": "Number"},
                        {"name": "UsageDate", "type": "Number"},
                        {"name": "ServiceName", "type": "String"},
                        {"name": "PricingModel", "type": "String"},
                        {"name": "Currency", "type": "String"},
                    ],
                    "rows": [[1200.0, 20260901, "Virtual Machines", "Reservation", "USD"]],
                }
            }
        return fixture_json("azure/query_empty.json")


def _json(name: str) -> httpx.Response:
    return httpx.Response(200, json=fixture_json(name))


def _http(router: Router) -> AzureHttp:
    return AzureHttp(Credential(), transport=httpx.MockTransport(router), sleep=lambda s: None)


def _collector(router: Router, agreement="ea", scope=EA_SCOPE, **kw) -> AzureCollector:
    return AzureCollector(_http(router), agreement_type=agreement, billing_scope=scope, **kw)


# ---------------------------------------------------------------- scopes / http


@pytest.mark.parametrize(
    ("agreement", "scope"),
    [
        ("ea", "/providers/Microsoft.Billing/billingAccounts/84251234"),
        ("mca", "/providers/Microsoft.Billing/billingAccounts/a:b_2019-05-31/billingProfiles/PF12"),
        ("payg", f"/subscriptions/{SUB}"),
        ("csp", "/providers/Microsoft.Billing/billingAccounts/p1/customers/c1"),
    ],
)
def test_valid_billing_scopes(agreement, scope):
    assert validate_scope(agreement, scope) == scope


def test_invalid_billing_scope():
    with pytest.raises(ValueError, match="billingProfiles"):
        validate_scope("mca", "/providers/Microsoft.Billing/billingAccounts/84251234")
    with pytest.raises(ValueError, match="agreement_type"):
        validate_scope("enterprise", EA_SCOPE)


def test_throttling_is_retried_then_errors_surface():
    router = Router()
    router.fail_first.add("/subscriptions")
    c = _collector(router)
    subs = c.list_accounts()
    assert [s.name for s in subs] == ["prod", "dev"]
    assert len([r for r in router.requests if r.url.path == "/subscriptions"]) == 2
    http = _http(router)
    with pytest.raises(AzureHttpError) as err:
        http.request("GET", "/nope")
    assert err.value.status == 404


# ---------------------------------------------------------------- query bootstrap


def test_month_chunks():
    assert list(month_chunks(date(2026, 8, 30), date(2026, 10, 2))) == [
        (date(2026, 8, 30), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 1)),
    ]


def test_query_bootstrap():
    router = Router()
    c = _collector(router)
    (table,) = list(c.bootstrap_usage(date(2026, 9, 1), date(2026, 9, 3)))
    rows = table.to_pylist()

    od = [r for r in rows if r["sku_id"] == METER and r["pricing_category"] == "On-Demand"]
    assert len(od) == 2  # both pages of the paged response
    assert od[0]["instance_type"] == "Standard_D4s_v3"  # from the meter catalog
    assert od[0]["region"] == "eastus" and od[0]["operating_system"] == "Linux"
    assert od[0]["sub_account_id"] == SUB and od[0]["billing_account_id"] == "84251234"
    assert od[0]["billed_cost"] == od[0]["effective_cost"] == pytest.approx(4.608)

    used = next(r for r in rows if r["commitment_status"] == "Used")
    assert used["billed_cost"] == 0 and used["commitment_type"] == "Reservation"
    assert used["on_demand_equiv_cost"] == pytest.approx(0.192 * 24)  # retail price x hours
    unused = next(r for r in rows if r["commitment_status"] == "Unused")
    assert unused["effective_cost"] == pytest.approx(0.4) and unused["billed_cost"] == 0

    purchase = next(r for r in rows if r["charge_category"] == "Purchase")
    assert purchase["billed_cost"] == 1200 and purchase["effective_cost"] == 0

    storage = [r for r in rows if r["service_name"] == "Storage"]
    assert len(storage) == 2

    # 4 pricing models + 1 page-2 + other services + purchases; meters looked up once.
    assert c.stats.calls["azure:CostManagementQuery"] == 7
    assert c.stats.calls["azure:RetailPrices"] == 1
    body = router.query_bodies[0]
    assert body["type"] == "AmortizedCost"
    assert body["timePeriod"] == {"from": "2026-09-01T00:00:00Z", "to": "2026-09-02T23:59:59Z"}
    assert body["dataset"]["granularity"] == "Daily"


def test_daily_by_service_actual_and_amortized_fixtures():
    actual = fixture_json("azure/query_actualcost_daily_by_service.json")
    amortized = fixture_json("azure/query_amortizedcost_daily_by_service.json")
    from app.collectors.azure.query import parse_query_rows

    merged = merge_cost_types(parse_query_rows(actual), parse_query_rows(amortized))
    q = QueryBootstrap(None, None, EA_SCOPE, None, "84251234")
    rows = [q.to_usage_pair(r) for r in merged]
    vm = next(
        r
        for r in rows
        if r["service_name"] == "Virtual Machines"
        and r["charge_period_start"] == datetime(2026, 9, 1, tzinfo=UTC)
    )
    assert vm["billed_cost"] == pytest.approx(310.25)
    assert vm["effective_cost"] == pytest.approx(352.40)
    ri_purchase = next(r for r in rows if r["service_name"] == "Reserved VM Instances")
    assert ri_purchase["billed_cost"] == pytest.approx(1200.0)
    assert ri_purchase["effective_cost"] == 0
    assert sum(r["billed_cost"] for r in rows) == pytest.approx(
        310.25 + 1200 + 42.10 + 305.90 + 41.80
    )


# ---------------------------------------------------------------- commitments


def test_commitments_and_utilization_ea():
    router = Router()
    c = _collector(router)
    records = {r.kind: r for r in c.collect_commitments()}
    ri = records["azure_ri"]
    assert ri.instance_type == "Standard_D4s_v3" and ri.instance_family == "Ds_v3"
    assert (ri.quantity, ri.term_months, ri.payment_option) == (4, 12, "all_upfront")
    assert ri.upfront_cost == Decimal("4036.0") and ri.region == "eastus"
    assert ri.scope == "Shared"
    sp = records["azure_sp_compute"]
    assert sp.hourly_commitment == Decimal("2.5") and sp.term_months == 36
    assert sp.payment_option == "monthly" and sp.owner_account_id == SUB

    util = c.collect_utilization(date(2026, 9, 1), date(2026, 9, 2))
    by_id = {u.provider_commitment_id: u for u in util}
    # bare benefit ids are mapped back to the stored ARM ids
    assert by_id[ri.provider_commitment_id].utilization_pct == Decimal("80.0")
    assert by_id[sp.provider_commitment_id].utilization_pct == Decimal("96.5")
    util_calls = [r for r in router.requests if r.url.path.endswith("benefitUtilizationSummaries")]
    assert [r.url.path for r in util_calls] == [
        f"{EA_SCOPE}/providers/Microsoft.CostManagement/benefitUtilizationSummaries"
    ]
    assert util_calls[0].url.params["grainParameter"] == "Daily"


def test_payg_utilization_is_read_per_order():
    router = Router()
    c = _collector(router, agreement="payg", scope=f"/subscriptions/{SUB}")
    c.collect_utilization(date(2026, 9, 1), date(2026, 9, 2))
    paths = sorted(
        r.url.path for r in router.requests if r.url.path.endswith("benefitUtilizationSummaries")
    )
    suffix = "/providers/Microsoft.CostManagement/benefitUtilizationSummaries"
    assert paths == [
        f"/providers/microsoft.billingbenefits/savingsplanorders/{SP_ORDER}{suffix}",
        f"/providers/microsoft.capacity/reservationorders/{RI_ORDER}{suffix}",
    ]


def test_native_recommendations():
    router = Router()
    recs = _collector(router, rec_terms=("P3Y",)).collect_native_recommendations()
    sp = next(r for r in recs if r.kind == "azure_sp_compute")
    assert sp.hourly_commitment == Decimal("3.1")
    assert sp.estimated_monthly_savings == Decimal("2600.00")  # 30-day lookback -> per month
    ris = [r for r in recs if r.kind == "azure_ri"]
    assert [(r.instance_type, r.region, r.quantity) for r in ris] == [
        ("Standard_D4s_v3", "eastus", Decimal(3)),
        ("Standard_E8s_v3", "westeurope", Decimal(2)),
    ]
    assert ris[1].estimated_monthly_savings == Decimal("90.00")  # legacy shape (plain number)


# ---------------------------------------------------------------- export


class FakeBlobs:
    def __init__(self, blobs: dict[str, tuple[bytes, datetime]]) -> None:
        self.blobs = blobs

    def list(self, prefix: str):
        return [(n, t) for n, (_, t) in self.blobs.items() if n.startswith(prefix)]

    def read(self, name: str) -> bytes:
        return self.blobs[name][0]


def test_focus_export_reads_newest_run_only():
    parquet = (FIXTURES / "azure/focus_export.parquet").read_bytes()
    buf = io.BytesIO()
    pacsv.write_csv(fixture_parquet("azure/focus_export.parquet"), buf)
    csv_gz = gzip.compress(buf.getvalue())
    old, new = datetime(2026, 9, 2, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC)
    base = "exports/focus/savings-tool/20260901-20260930"
    blobs = FakeBlobs(
        {
            f"{base}/run-old/part_0_0001.parquet": (parquet, old),
            f"{base}/run-new/part_0_0001.parquet": (parquet, new),
            f"{base}/run-new/part_1_0001.csv.gz": (csv_gz, new),
            f"{base}/run-new/manifest.json": (b"{}", new),
        }
    )
    assert set(latest_runs(blobs.list(""))) == {"20260901-20260930"}
    c = _collector(Router(), export_source=blobs, export_prefix="exports/focus")
    (table,) = list(c.export_usage(None))
    assert table.num_rows == 8  # 4 rows from the parquet + 4 from the csv of the newest run
    assert set(table.column("instance_type").to_pylist()) == {"Standard_D4s_v3"}
    assert list(c.export_usage(since=date(2026, 10, 1))) == []


# ---------------------------------------------------------------- meter catalog


def test_meter_catalog_prefers_consumption_rows():
    router = Router()
    catalog = MeterCatalog(_http(router), CountingCaller(CallStats()))
    meters = catalog.lookup([METER.upper(), METER])
    assert meters[METER]["type"] == "Consumption"
    assert meters[METER]["armSkuName"] == "Standard_D4s_v3"
    catalog.lookup([METER])
    assert len(router.requests) == 1
    assert "meterId eq" in router.requests[0].url.params["$filter"]
