from datetime import date
from decimal import Decimal

import boto3
from botocore.stub import Stubber

from app.collectors.cache import CountingCaller
from app.collectors.types import CallStats
from app.pricing import aws, azure
from app.pricing.keys import aws_ec2_key, aws_os, azure_key
from tests.conftest import fixture_json

TODAY = date(2026, 10, 5)


def test_aws_product_on_demand_and_ri():
    records = aws.parse_product("AmazonEC2", fixture_json("aws/pricing_ec2_product.json"), TODAY)
    key = aws_ec2_key("m5.xlarge", "Windows with SQL Server Standard", "Shared")
    assert {r.sku_key for r in records} == {key}
    od = next(r for r in records if r.pricing_model == "on_demand")
    assert od.price_per_unit == Decimal("1.1520000000") and od.region == "us-east-1"
    ri_1y = next(r for r in records if r.pricing_model == "ri" and r.term_months == 12)
    # $8760 upfront over 8760 hours, no hourly fee
    assert ri_1y.price_per_unit == Decimal("1.00000000")
    assert ri_1y.payment_option == "all_upfront"
    assert ri_1y.attributes["offering_class"] == "standard"
    ri_3y = next(r for r in records if r.term_months == 36)
    assert ri_3y.price_per_unit == Decimal("0.75000000") and ri_3y.payment_option == "no_upfront"


def test_convertible_ri_offerings_are_skipped():
    product = fixture_json("aws/pricing_ec2_product.json")
    for term in product["terms"]["Reserved"].values():
        term["termAttributes"]["OfferingClass"] = "convertible"
    records = aws.parse_product("AmazonEC2", product, TODAY)
    assert [r.pricing_model for r in records] == ["on_demand"]


def test_aws_pricing_pagination():
    client = boto3.client(
        "pricing", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    stub = Stubber(client)
    product = __import__("json").dumps(fixture_json("aws/pricing_ec2_product.json"))
    stub.add_response(
        "get_products", {"PriceList": [product], "NextToken": "n2", "FormatVersion": "aws_v1"}
    )
    stub.add_response("get_products", {"PriceList": [], "FormatVersion": "aws_v1"})
    stub.activate()
    stats = CallStats()
    out = list(
        aws.on_demand_and_ri_prices(
            client, CountingCaller(stats), "AmazonEC2", ["us-east-1"], TODAY
        )
    )
    assert len(out) == 3 and stats.calls == {"pricing:GetProducts": 2}
    stub.assert_no_pending_responses()


def test_aws_savings_plan_rates():
    client = boto3.client(
        "savingsplans", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    stub = Stubber(client)
    stub.add_response(
        "describe_savings_plans_offering_rates",
        {
            "searchResults": [
                {
                    "savingsPlanOffering": {
                        "offeringId": "o1",
                        "paymentOption": "No Upfront",
                        "planType": "Compute",
                        "durationSeconds": 31536000,
                        "currency": "USD",
                    },
                    "rate": "0.1286",
                    "unit": "Hrs",
                    "productType": "EC2",
                    "serviceCode": "AmazonEC2",
                    "usageType": "BoxUsage:m5.xlarge",
                    "operation": "RunInstances",
                    "properties": [
                        {"name": "region", "value": "us-east-1"},
                        {"name": "instanceType", "value": "m5.xlarge"},
                        {"name": "productDescription", "value": "Linux/UNIX"},
                        {"name": "tenancy", "value": "shared"},
                    ],
                },
                {
                    "savingsPlanOffering": {
                        "offeringId": "o1",
                        "paymentOption": "No Upfront",
                        "planType": "Compute",
                        "durationSeconds": 31536000,
                    },
                    "rate": "0.01",
                    "unit": "Hrs",
                    "usageType": "Fargate-vCPU-Hours:perCPU",
                    "properties": [{"name": "region", "value": "us-east-1"}],
                },
            ]
        },
    )
    stub.activate()
    (rate,) = list(
        aws.savings_plan_rates(client, CountingCaller(CallStats()), ["us-east-1"], TODAY)
    )
    assert rate.sku_key == "ec2|m5.xlarge|Linux|Shared"
    assert (rate.pricing_model, rate.term_months, rate.payment_option) == ("sp", 12, "no_upfront")
    assert rate.price_per_unit == Decimal("0.1286")


def test_azure_retail_items():
    items = fixture_json("azure/retail_prices_meter.json")["Items"]
    records = [r for item in items for r in azure.parse_item(item, TODAY)]
    key = azure_key("Virtual Machines", "Standard_D4s_v3", "Virtual Machines DSv3 Series")
    assert key == "azure|Virtual Machines|Standard_D4s_v3|Linux"
    by_model = {(r.pricing_model, r.term_months): r for r in records}
    assert set(by_model) == {("on_demand", None), ("sp", 12), ("sp", 36), ("ri", 12)}
    assert by_model[("on_demand", None)].price_per_unit == Decimal("0.192")
    assert by_model[("sp", 36)].price_per_unit == Decimal("0.0921")
    # reservation price is for the whole term; stored per hour
    assert by_model[("ri", 12)].price_per_unit == Decimal("0.11518265")  # 1009 / 8760
    assert all(r.region == "eastus" and r.sku_key == key for r in records)  # spot row skipped


def test_os_keys():
    assert aws_os("Linux", "NA") == "Linux"
    assert aws_os("Windows", "SQL Ent") == "Windows with SQL Server Enterprise"
    assert aws_os("Red Hat Enterprise Linux", None) == "RHEL"
    assert azure_key(
        "Virtual Machines", "Standard_D2s_v3", "Virtual Machines Dsv3 Series Windows"
    ).endswith("|Windows")


def _db_rate(product, usage_type, operation, rate, instance_type, description, unit="Hrs"):
    return {
        "savingsPlanOffering": {
            "offeringId": "db1",
            "paymentOption": "No Upfront",
            "planType": "Database",
            "durationSeconds": 31536000,
            "currency": "USD",
        },
        "rate": rate,
        "unit": unit,
        "productType": product,
        "usageType": usage_type,
        "operation": operation,
        "properties": [
            {"name": "region", "value": "us-east-2"},
            {"name": "instanceType", "value": instance_type},
            {"name": "productDescription", "value": description},
        ],
    }


def test_aws_database_savings_plan_rates():
    """Shapes as returned by the live API (us-east-2, October 2026)."""
    client = boto3.client(
        "savingsplans", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    stub = Stubber(client)
    results = [
        _db_rate("RDS", "USE2-InstanceUsage:db.m5.large", "CreateDBInstance:0002", "0.1368",
                 "db.m5.large", "MySQL"),
        _db_rate("RDS", "USE2-Multi-AZUsage:db.m5.large", "CreateDBInstance:0014", "0.2848",
                 "db.m5.large", "PostgreSQL"),
        # Same key as the plain Multi-AZ instance: dropped rather than collide.
        _db_rate("RDS", "USE2-Multi-AZClusterUsage:db.m5.large", "CreateDBInstance:0014",
                 "0.3420", "db.m5.large", "PostgreSQL"),
        # Two editions behind one engine name: ambiguous, so skipped.
        _db_rate("RDS", "USE2-InstanceUsage:db.m5.large", "CreateDBInstance:0012", "0.70",
                 "db.m5.large", "SQL Server"),
        _db_rate("RDS", "USE2-InstanceUsage:db.m5.large", "CreateDBInstance:0015", "1.40",
                 "db.m5.large", "SQL Server"),
        _db_rate("OpenSearch", "USE2-ESInstance:m7g.medium", "ESDomain", "0.0544",
                 "m7g.medium.search", "Instance"),
        # Valkey-only ElastiCache rates aren't keyed by engine: left out.
        _db_rate("ElastiCache", "USE2-NodeUsage:cache.m7g.large", "CreateCacheCluster:Valkey",
                 "0.10112", "cache.m7g.large", "Valkey"),
        # Serverless units have no instance type.
        _db_rate("RDS", "USE2-Aurora:ServerlessV2Usage", "CreateDBInstance:0021", "0.096",
                 None, "Aurora PostgreSQL", unit="ACU-Hr"),
    ]  # fmt: skip
    results[-1]["properties"] = [p for p in results[-1]["properties"] if p["value"]]
    stub.add_response(
        "describe_savings_plans_offering_rates",
        {"searchResults": results},
        {
            "savingsPlanTypes": ["Database"],
            "products": ["RDS", "OpenSearch"],
            "filters": [{"name": "region", "values": ["us-east-2"]}],
            "maxResults": 1000,
        },
    )
    stub.activate()
    rates = aws.database_savings_plan_rates(
        client, CountingCaller(CallStats()), ["us-east-2"], TODAY
    )
    by_key = {r.sku_key: r for r in rates}
    assert set(by_key) == {
        "rds|db.m5.large|MySQL|Single-AZ",
        "rds|db.m5.large|PostgreSQL|Multi-AZ",
        "opensearch|m7g.medium",
    }
    mysql = by_key["rds|db.m5.large|MySQL|Single-AZ"]
    assert (mysql.pricing_model, mysql.term_months, mysql.payment_option) == (
        "sp_database",
        12,
        "no_upfront",
    )
    assert mysql.price_per_unit == Decimal("0.1368") and mysql.service == "rds"
    assert by_key["rds|db.m5.large|PostgreSQL|Multi-AZ"].price_per_unit == Decimal("0.2848")


def test_rds_prices_only_for_keyed_deployments():
    """Aurora I/O-Optimized, Multi-AZ clusters etc. would collide with the plain instance key."""
    product = {
        "product": {
            "sku": "S1",
            "attributes": {
                "instanceType": "db.r6g.large",
                "databaseEngine": "Aurora MySQL",
                "deploymentOption": "Single-AZ",
                "regionCode": "us-east-2",
                "usagetype": "USE2-InstanceUsageIOOptimized:db.r6g.large",
            },
        },
        "terms": {
            "OnDemand": {
                "t": {"priceDimensions": {"d": {"unit": "Hrs", "pricePerUnit": {"USD": "0.338"}}}}
            }
        },
    }
    assert aws.parse_product("AmazonRDS", product, TODAY) == []
    product["product"]["attributes"]["usagetype"] = "USE2-InstanceUsage:db.r6g.large"
    (od,) = aws.parse_product("AmazonRDS", product, TODAY)
    assert od.sku_key == "rds|db.r6g.large|Aurora MySQL|Single-AZ"


def test_opensearch_keys_match_usage_and_prices():
    from app.pricing.keys import aws_generic_key, usage_sku_key

    price_key = aws_generic_key("opensearch", "m7g.medium.search")
    usage_key = usage_sku_key("aws", "Amazon OpenSearch Service", "m7g.medium")
    assert price_key == usage_key == "opensearch|m7g.medium"
