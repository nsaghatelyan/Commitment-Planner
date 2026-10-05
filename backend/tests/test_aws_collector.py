from datetime import UTC, date, datetime
from decimal import Decimal

import boto3
import pytest
from botocore.stub import ANY, Stubber
from moto import mock_aws

from app.collectors.aws import AwsCollector
from app.collectors.aws.cost_explorer import CostExplorerBootstrap, classify_purchase_type
from app.collectors.aws.session import assumed_role_session
from app.collectors.aws.usage_types import database_engine, operating_system, parse_usage_type
from app.collectors.cache import ApiCache, CountingCaller
from app.collectors.types import CallStats
from tests.conftest import FIXTURES, fixture_json

ROLE = "arn:aws:iam::111111111111:role/SavingsToolReadOnly"
EC2 = "Amazon Elastic Compute Cloud - Compute"


class Stubs:
    """client_factory that hands out stubbed clients, one per (service, region)."""

    def __init__(self) -> None:
        self.stubbers: dict[tuple[str, str | None], Stubber] = {}
        self.clients: dict[tuple[str, str | None], object] = {}

    def __call__(self, service: str, region: str | None):
        key = (service, region)
        if key not in self.clients:
            client = boto3.client(
                service,
                region_name=region or "us-east-1",
                aws_access_key_id="x",
                aws_secret_access_key="x",
            )
            self.clients[key] = client
            self.stubbers[key] = Stubber(client)
            self.stubbers[key].activate()
        return self.clients[key]

    def stub(self, service: str, region: str | None = None) -> Stubber:
        self(service, region)
        return self.stubbers[(service, region)]

    def assert_done(self) -> None:
        for s in self.stubbers.values():
            s.assert_no_pending_responses()


def _collector(stubs, **kw) -> AwsCollector:
    return AwsCollector(ROLE, "ext-id-1234567890", client_factory=stubs, **kw)


def _ce_page(groups, start="2026-09-01", end="2026-09-02", keys=("USAGE_TYPE", "OPERATION")):
    return {
        "GroupDefinitions": [{"Type": "DIMENSION", "Key": k} for k in keys],
        "ResultsByTime": [
            {"TimePeriod": {"Start": start, "End": end}, "Groups": groups, "Estimated": False}
        ],
    }


def _metrics(amortized, unblended, qty, unit="Hrs"):
    return {
        "AmortizedCost": {"Amount": str(amortized), "Unit": "USD"},
        "UnblendedCost": {"Amount": str(unblended), "Unit": "USD"},
        "UsageQuantity": {"Amount": str(qty), "Unit": unit},
        "NormalizedUsageAmount": {"Amount": str(qty * 8), "Unit": "N/A"},
    }


def _dims(*values):
    return {
        "DimensionValues": [{"Value": v, "Attributes": {}} for v in values],
        "ReturnSize": len(values),
        "TotalSize": len(values),
    }


# ---------------------------------------------------------------- decoding helpers


def test_usage_type_parsing():
    assert parse_usage_type("USE2-BoxUsage:m5.large") == {
        "region": "us-east-2",
        "usage_kind": "BoxUsage",
        "instance_type": "m5.large",
        "tenancy": "Shared",
    }
    assert parse_usage_type("BoxUsage:t3.micro")["region"] == "us-east-1"
    assert parse_usage_type("EUW1-DedicatedUsage:c5.xlarge")["tenancy"] == "Dedicated"
    assert parse_usage_type("APS3-InstanceUsage:db.r5.large")["region"] == "ap-south-1"
    assert parse_usage_type("USE1-DataTransfer-Out-Bytes")["instance_type"] is None
    assert operating_system("RunInstances:0002") == "Windows"
    assert operating_system("RunInstances") == "Linux"
    assert database_engine("CreateDBInstance:0012") == "SQL Server SE"
    assert classify_purchase_type("Standard Reserved Instances") == "ri"
    assert classify_purchase_type("Savings Plans") == "sp"
    assert classify_purchase_type("Spot Instances") == "spot"
    assert classify_purchase_type("On Demand Instances") == "od"


def test_daily_by_service_fixture_parses():
    resp = fixture_json("aws/ce_get_cost_and_usage_daily_by_service.json")
    rows = CostExplorerBootstrap(None, None, "111111111111").parse_response(resp)
    assert len(rows) == 3
    ec2 = rows[0]
    assert ec2["service_name"] == EC2 and ec2["service_category"] == "Compute"
    assert ec2["billed_cost"] == pytest.approx(398.10)
    assert ec2["effective_cost"] == pytest.approx(412.55)
    assert ec2["charge_period_start"] == datetime(2026, 9, 1, tzinfo=UTC)
    assert ec2["billing_account_id"] == "111111111111"


# ---------------------------------------------------------------- connection / accounts


def test_assume_role_with_external_id():
    with mock_aws():
        session = assumed_role_session(ROLE, "ext-id-1234567890", "test", "us-east-1")
        ident = session.client("sts").get_caller_identity()
        assert ":assumed-role/SavingsToolReadOnly/test" in ident["Arn"]


def test_connection_test_and_org_accounts():
    stubs = Stubs()
    stubs.stub("ce").add_response(
        "get_cost_and_usage",
        {"ResultsByTime": []},
        {"TimePeriod": ANY, "Granularity": "DAILY", "Metrics": ["UnblendedCost"]},
    )
    org = stubs.stub("organizations")
    org.add_response(
        "describe_organization",
        {"Organization": {"Id": "o-abc", "MasterAccountId": "111111111111"}},
    )
    org.add_response(
        "list_accounts",
        {"Accounts": [{"Id": "111111111111", "Name": "payer"}], "NextToken": "t2"},
    )
    org.add_response(
        "list_accounts", {"Accounts": [{"Id": "222222222222", "Name": "prod"}]}, {"NextToken": "t2"}
    )
    c = _collector(stubs)
    c.test_connection()
    accounts = c.list_accounts()
    assert [(a.external_account_id, a.name, a.is_payer) for a in accounts] == [
        ("111111111111", "payer", True),
        ("222222222222", "prod", False),
    ]
    assert c.stats.calls == {
        "ce:GetCostAndUsage": 1,
        "organizations:DescribeOrganization": 1,
        "organizations:ListAccounts": 2,
    }
    stubs.assert_done()


def test_standalone_account_without_organizations():
    stubs = Stubs()
    stubs.stub("organizations").add_client_error(
        "describe_organization", service_error_code="AWSOrganizationsNotInUseException"
    )
    stubs.stub("sts").add_response(
        "get_caller_identity",
        {
            "Account": "999999999999",
            "Arn": "arn:aws:iam::111111111111:root",
            "UserId": "AIDAEXAMPLE",
        },
    )
    accounts = _collector(stubs).list_accounts()
    assert [(a.external_account_id, a.is_payer) for a in accounts] == [("999999999999", True)]


# ---------------------------------------------------------------- bootstrap


def _stub_bootstrap(stubs: Stubs) -> None:
    ce = stubs.stub("ce")
    ce.add_response("get_dimension_values", _dims(EC2, "Amazon Simple Storage Service"))
    ce.add_response(
        "get_dimension_values", _dims("On Demand Instances", "Savings Plans", "Spot Instances")
    )
    # EC2 x on-demand: detail fixture (two usage types), with a second page
    detail = fixture_json("aws/ce_get_cost_and_usage_detail.json")
    ce.add_response("get_cost_and_usage", {**detail, "NextPageToken": "p2"})
    ce.add_response(
        "get_cost_and_usage",
        _ce_page([]),
        {**_detail_params("On Demand Instances"), "NextPageToken": "p2"},
    )
    # EC2 x savings plans: covered usage shows the on-demand rate as unblended
    ce.add_response(
        "get_cost_and_usage",
        _ce_page(
            [
                {
                    "Keys": ["USE1-BoxUsage:m5.xlarge", "RunInstances"],
                    "Metrics": _metrics(61.7, 92.16, 480),
                }
            ]
        ),
        _detail_params("Savings Plans"),
    )
    # EC2 x spot
    ce.add_response(
        "get_cost_and_usage",
        _ce_page(
            [
                {
                    "Keys": ["USE1-SpotUsage:c5.2xlarge", "RunInstances:SV001"],
                    "Metrics": _metrics(26.1, 26.1, 240),
                }
            ]
        ),
    )
    # everything else, by service and account
    ce.add_response(
        "get_cost_and_usage",
        _ce_page(
            [
                {
                    "Keys": ["Amazon Simple Storage Service", "222222222222"],
                    "Metrics": _metrics(21.4, 21.4, 88000, "GB-Mo"),
                }
            ],
            keys=("SERVICE", "LINKED_ACCOUNT"),
        ),
    )
    stubs.stub("sts").add_response(
        "get_caller_identity",
        {
            "Account": "111111111111",
            "Arn": "arn:aws:iam::111111111111:root",
            "UserId": "AIDAEXAMPLE",
        },
    )


def _detail_params(purchase_type: str):
    return {
        "TimePeriod": {"Start": "2026-09-01", "End": "2026-09-02"},
        "Granularity": "DAILY",
        "Metrics": ANY,
        "GroupBy": [
            {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
            {"Type": "DIMENSION", "Key": "OPERATION"},
        ],
        "Filter": {
            "And": [
                {"Dimensions": {"Key": "SERVICE", "Values": [EC2]}},
                {"Dimensions": {"Key": "PURCHASE_TYPE", "Values": [purchase_type]}},
                {
                    "Dimensions": {
                        "Key": "RECORD_TYPE",
                        "Values": ["Usage", "DiscountedUsage", "SavingsPlanCoveredUsage"],
                    }
                },
            ]
        },
    }


def test_cost_explorer_bootstrap(tmp_path):
    stubs = Stubs()
    _stub_bootstrap(stubs)
    c = _collector(stubs, cache=ApiCache(tmp_path / "cache", 3600))
    (table,) = list(c.bootstrap_usage(date(2026, 9, 1), date(2026, 9, 2)))
    stubs.assert_done()
    rows = {(r["sku_id"] or r["service_name"], r["pricing_category"]): r for r in table.to_pylist()}

    od = rows[("USE1-BoxUsage:m5.xlarge|RunInstances", "On-Demand")]
    assert od["region"] == "us-east-1" and od["instance_type"] == "m5.xlarge"
    assert od["instance_family"] == "m5" and od["operating_system"] == "Linux"
    assert od["billed_cost"] == od["on_demand_equiv_cost"] == pytest.approx(92.16)
    win = rows[("EUW1-BoxUsage:m5.large|RunInstances:0002", "On-Demand")]
    assert win["region"] == "eu-west-1" and win["operating_system"] == "Windows"

    sp = rows[("USE1-BoxUsage:m5.xlarge|RunInstances", "Commitment-Based")]
    assert sp["billed_cost"] == 0
    assert sp["effective_cost"] == pytest.approx(61.7)
    assert sp["on_demand_equiv_cost"] == pytest.approx(92.16)
    assert (sp["commitment_type"], sp["commitment_status"]) == ("Savings Plan", "Used")

    spot = rows[("USE1-SpotUsage:c5.2xlarge|RunInstances:SV001", "Spot")]
    assert spot["on_demand_equiv_cost"] is None

    s3 = rows[("Amazon Simple Storage Service", "On-Demand")]
    assert s3["sub_account_id"] == "222222222222" and s3["usage_unit"] == "GB-Mo"

    # 2 dimension lookups + 5 cost-and-usage pages, all billed at $0.01
    assert c.stats.calls["ce:GetCostAndUsage"] == 5
    assert c.stats.calls["ce:GetDimensionValues"] == 2

    # Same request again: served from the cache, no API calls (stubs would raise).
    stubs2 = Stubs()
    stubs2.stub("sts").add_response(
        "get_caller_identity",
        {
            "Account": "111111111111",
            "Arn": "arn:aws:iam::111111111111:root",
            "UserId": "AIDAEXAMPLE",
        },
    )
    c2 = _collector(stubs2, cache=ApiCache(tmp_path / "cache", 3600))
    (again,) = list(c2.bootstrap_usage(date(2026, 9, 1), date(2026, 9, 2)))
    assert again.num_rows == table.num_rows
    assert "ce:GetCostAndUsage" not in c2.stats.calls
    assert c2.stats.cache_hits == 7


# ---------------------------------------------------------------- export


def test_data_exports_reader():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="client-cur")
        body = (FIXTURES / "aws/focus_1_0_export.parquet").read_bytes()
        prefix = "exports/focus/savings-tool"
        s3.put_object(
            Bucket="client-cur",
            Key=f"{prefix}/data/BILLING_PERIOD=2026-09/part-0.parquet",
            Body=body,
        )
        s3.put_object(
            Bucket="client-cur",
            Key=f"{prefix}/data/BILLING_PERIOD=2026-09/part-1.parquet",
            Body=body,
        )
        s3.put_object(
            Bucket="client-cur", Key=f"{prefix}/metadata/BILLING_PERIOD=2026-09/m.json", Body=b"{}"
        )
        c = AwsCollector(
            ROLE,
            "x" * 16,
            export_bucket="client-cur",
            export_prefix="exports/focus",
            client_factory=lambda svc, region: boto3.client(svc, region_name="us-east-1"),
        )
        (table,) = list(c.export_usage(None))
        assert table.num_rows == 10  # two files of five rows, one billing period
        assert set(table.column("provider").to_pylist()) == {"aws"}
        assert list(c.export_usage(since=date(2099, 1, 1))) == []
        assert c.stats.calls["s3:GetObject"] == 2


# ---------------------------------------------------------------- commitments


def _stub_commitments(stubs: Stubs) -> None:
    stubs.stub("savingsplans").add_response(
        "describe_savings_plans",
        {
            "savingsPlans": [
                {
                    "savingsPlanId": "8f1c2a2e",
                    "savingsPlanArn": "arn:aws:savingsplans::111111111111:savingsplan/8f1c2a2e",
                    "start": "2026-01-01T00:00:00.000Z",
                    "end": "2027-01-01T00:00:00.000Z",
                    "state": "active",
                    "savingsPlanType": "Compute",
                    "paymentOption": "No Upfront",
                    "productTypes": ["EC2", "Fargate", "Lambda"],
                    "currency": "USD",
                    "commitment": "1.5",
                    "upfrontPaymentAmount": "0",
                    "recurringPaymentAmount": "1.5",
                    "termDurationInSeconds": 31536000,
                }
            ]
        },
        {"states": ["active", "queued", "payment-pending"]},
    )
    stubs.stub("ec2").add_response("describe_regions", {"Regions": [{"RegionName": "us-east-1"}]})
    stubs.stub("ec2", "us-east-1").add_response(
        "describe_reserved_instances",
        {
            "ReservedInstances": [
                {
                    "ReservedInstancesId": "7a6b5c4d-1111-2222-3333-444455556666",
                    "InstanceType": "m5.xlarge",
                    "InstanceCount": 4,
                    "Start": datetime(2025, 3, 1, tzinfo=UTC),
                    "End": datetime(2028, 3, 1, tzinfo=UTC),
                    "Duration": 94608000,
                    "FixedPrice": 1000.0,
                    "UsagePrice": 0.0,
                    "OfferingType": "Partial Upfront",
                    "RecurringCharges": [{"Amount": 0.04, "Frequency": "Hourly"}],
                    "ProductDescription": "Linux/UNIX",
                    "Scope": "Region",
                    "State": "active",
                    "InstanceTenancy": "default",
                    "OfferingClass": "standard",
                }
            ]
        },
    )
    stubs.stub("rds", "us-east-1").add_response(
        "describe_reserved_db_instances",
        {
            "ReservedDBInstances": [
                {
                    "ReservedDBInstanceId": "sqlserver-ri",
                    "ReservedDBInstanceArn": "arn:aws:rds:us-east-1:333333333333:ri:sqlserver-ri",
                    "DBInstanceClass": "db.r5.xlarge",
                    "StartTime": datetime(2026, 5, 1, tzinfo=UTC),
                    "Duration": 31536000,
                    "FixedPrice": 9000.0,
                    "UsagePrice": 0.0,
                    "DBInstanceCount": 2,
                    "ProductDescription": "sqlserver-se(li)",
                    "OfferingType": "All Upfront",
                    "MultiAZ": True,
                    "State": "active",
                    "RecurringCharges": [],
                }
            ]
        },
    )
    stubs.stub("elasticache", "us-east-1").add_response(
        "describe_reserved_cache_nodes", {"ReservedCacheNodes": []}
    )
    stubs.stub("opensearch", "us-east-1").add_response(
        "describe_reserved_instances", {"ReservedInstances": []}
    )
    stubs.stub("redshift", "us-east-1").add_response(
        "describe_reserved_nodes", {"ReservedNodes": []}
    )
    stubs.stub("memorydb", "us-east-1").add_response(
        "describe_reserved_nodes", {"ReservedNodes": []}
    )
    stubs.stub("ce").add_response(
        "get_reservation_utilization", fixture_json("aws/ce_get_reservation_utilization.json")
    )
    stubs.stub("sts").add_response(
        "get_caller_identity",
        {
            "Account": "111111111111",
            "Arn": "arn:aws:iam::111111111111:root",
            "UserId": "AIDAEXAMPLE",
        },
    )


def test_commitment_inventory():
    stubs = Stubs()
    _stub_commitments(stubs)
    records = {r.provider_commitment_id: r for r in _collector(stubs).collect_commitments()}
    stubs.assert_done()
    sp = records["arn:aws:savingsplans::111111111111:savingsplan/8f1c2a2e"]
    assert (sp.kind, sp.term_months, sp.payment_option) == ("aws_sp_compute", 12, "no_upfront")
    assert sp.hourly_commitment == Decimal("1.5") and sp.owner_account_id == "111111111111"

    ri = records["7a6b5c4d-1111-2222-3333-444455556666"]
    assert (ri.kind, ri.instance_type, ri.instance_family, ri.quantity) == (
        "aws_ri",
        "m5.xlarge",
        "m5",
        4,
    )
    assert ri.term_months == 36 and ri.payment_option == "partial_upfront"
    # totals for all four instances
    assert ri.upfront_cost == Decimal("4000.0")
    assert ri.recurring_hourly_cost == Decimal("0.16")

    rds = records["arn:aws:rds:us-east-1:333333333333:ri:sqlserver-ri"]
    assert rds.owner_account_id == "333333333333"
    assert rds.attributes["deployment_option"] == "Multi-AZ"

    # DynamoDB reserved capacity in a member account only shows up through Cost Explorer;
    # the EC2 RI also reported there is not duplicated.
    dynamo = records["dynamo-res-0001"]
    assert dynamo.service == "Amazon DynamoDB" and dynamo.owner_account_id == "333333333333"
    assert len(records) == 4


def test_utilization():
    stubs = Stubs()
    ce = stubs.stub("ce")
    ce.add_response(
        "get_reservation_utilization", fixture_json("aws/ce_get_reservation_utilization.json")
    )
    for day in ("2026-09-01", "2026-09-02"):
        ce.add_response(
            "get_savings_plans_utilization_details",
            {
                "SavingsPlansUtilizationDetails": [
                    {
                        "SavingsPlanArn": "arn:aws:savingsplans::111111111111:savingsplan/8f1c2a2e",
                        "Attributes": {"SavingsPlansType": "ComputeSavingsPlans"},
                        "Utilization": {
                            "TotalCommitment": "36",
                            "UsedCommitment": "30.6",
                            "UnusedCommitment": "5.4",
                            "UtilizationPercentage": "85",
                        },
                        "Savings": {"NetSavings": "9.1"},
                    }
                ],
                "TimePeriod": {"Start": day, "End": day},
            },
        )
    records = _collector(stubs).collect_utilization(date(2026, 9, 1), date(2026, 9, 3))
    stubs.assert_done()
    ri = next(r for r in records if r.provider_commitment_id.startswith("7a6b"))
    assert ri.utilization_pct == Decimal(75)
    assert ri.unused_cost == Decimal("11.52") * 24 / 96
    sp = [r for r in records if r.provider_commitment_id.endswith("8f1c2a2e")]
    assert [r.date for r in sp] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert sp[0].unused_cost == Decimal("5.4")


# ---------------------------------------------------------------- native recommendations


def test_native_recommendations():
    stubs = Stubs()
    ce = stubs.stub("ce")
    ce.add_response(
        "get_savings_plans_purchase_recommendation",
        {
            "SavingsPlansPurchaseRecommendation": {
                "SavingsPlansPurchaseRecommendationDetails": [
                    {
                        "SavingsPlansDetails": {"OfferingId": "o1"},
                        "AccountId": "111111111111",
                        "UpfrontCost": "0",
                        "EstimatedMonthlySavingsAmount": "820.5",
                        "HourlyCommitmentToPurchase": "2.75",
                    }
                ]
            }
        },
        {
            "SavingsPlansType": "COMPUTE_SP",
            "TermInYears": "ONE_YEAR",
            "PaymentOption": "NO_UPFRONT",
            "LookbackPeriodInDays": "THIRTY_DAYS",
            "AccountScope": "PAYER",
        },
    )
    ce.add_response("get_savings_plans_purchase_recommendation", {})
    ce.add_response("get_savings_plans_purchase_recommendation", {})
    ce.add_response(
        "get_reservation_purchase_recommendation",
        {
            "Recommendations": [
                {
                    "RecommendationDetails": [
                        {
                            "AccountId": "222222222222",
                            "InstanceDetails": {
                                "EC2InstanceDetails": {
                                    "Family": "m5",
                                    "InstanceType": "m5.large",
                                    "Region": "us-east-1",
                                    "Platform": "Linux/UNIX",
                                }
                            },
                            "RecommendedNumberOfInstancesToPurchase": "6",
                            "UpfrontCost": "0",
                            "EstimatedMonthlySavingsAmount": "95.2",
                        }
                    ]
                }
            ]
        },
    )
    for _ in range(5):
        ce.add_response("get_reservation_purchase_recommendation", {})
    c = _collector(stubs, rec_terms=("ONE_YEAR",), rec_payments=("NO_UPFRONT",))
    recs = c.collect_native_recommendations()
    stubs.assert_done()
    assert [(r.kind, r.term_months, r.payment_option) for r in recs] == [
        ("aws_sp_compute", 12, "no_upfront"),
        ("aws_ri", 12, "no_upfront"),
    ]
    assert recs[0].hourly_commitment == Decimal("2.75")
    assert recs[1].instance_type == "m5.large" and recs[1].quantity == Decimal(6)
    assert c.stats.calls["ce:GetReservationPurchaseRecommendation"] == 6


def test_counting_caller_counts_misses_only(tmp_path):
    stats = CallStats()
    call = CountingCaller(stats, ApiCache(tmp_path, 3600))
    assert call("x:Op", lambda **kw: {"n": kw["n"]}, cache_key={"n": 1}, n=1) == {"n": 1}
    assert call("x:Op", lambda **kw: {"n": 2}, cache_key={"n": 1}, n=1) == {"n": 1}
    assert stats.calls == {"x:Op": 1} and stats.cache_hits == 1
