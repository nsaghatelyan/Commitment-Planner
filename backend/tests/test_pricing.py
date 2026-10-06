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


def test_usage_savings_plan_rates_key_every_variant_exactly():
    """Database Savings Plans rates, shapes as returned by the live API (us-east-2): each usage
    type + operation gets its own key, so variants that used to collide are all priced."""
    client = boto3.client(
        "savingsplans", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    stub = Stubber(client)
    results = [
        _db_rate("RDS", "USE2-InstanceUsage:db.m5.large", "CreateDBInstance:0002", "0.1368",
                 "db.m5.large", "MySQL"),
        _db_rate("RDS", "USE2-Multi-AZClusterUsage:db.m5.large", "CreateDBInstance:0014",
                 "0.3420", "db.m5.large", "PostgreSQL"),
        # SQL Server editions share an engine name but not an operation.
        _db_rate("RDS", "USE2-InstanceUsage:db.m5.large", "CreateDBInstance:0012", "0.70",
                 "db.m5.large", "SQL Server"),
        _db_rate("RDS", "USE2-InstanceUsage:db.m5.large", "CreateDBInstance:0015", "1.40",
                 "db.m5.large", "SQL Server"),
        _db_rate("ElastiCache", "USE2-NodeUsage:cache.m7g.large", "CreateCacheCluster:Valkey",
                 "0.10112", "cache.m7g.large", "Valkey"),
        _db_rate("RDS", "USE2-Aurora:ServerlessV2Usage", "CreateDBInstance:0021", "0.096",
                 "", "Aurora PostgreSQL", unit="ACU-Hr"),
        _db_rate("DynamoDB", "USE2-ReadCapacityUnit-Hrs", "CommittedThroughput", "0.0001144",
                 "", "DynamoDB Provisioned Read Units", unit="ReadCapacityUnit-Hrs"),
        # Two rates behind one usage type + operation would be a guess: skipped.
        _db_rate("DocDB", "USE2-InstanceUsage:db.r6g.large", "CreateDBInstance:0023", "0.2",
                 "db.r6g.large", "General"),
        _db_rate("DocDB", "USE2-InstanceUsage:db.r6g.large", "CreateDBInstance:0023", "0.3",
                 "db.r6g.large", "General"),
    ]  # fmt: skip
    for r in results:
        r["properties"] = [p for p in r["properties"] if p["value"]]
    usage_types = ["USE2-InstanceUsage:db.m5.large", "USE2-ReadCapacityUnit-Hrs"]
    stub.add_response(
        "describe_savings_plans_offering_rates",
        {"searchResults": results},
        {
            "savingsPlanTypes": ["Database"],
            "products": list(aws.USAGE_SP_PLANS["Database"][0]),
            "filters": [{"name": "region", "values": ["us-east-2"]}],
            "maxResults": 1000,
            "usageTypes": usage_types,
        },
    )
    stub.activate()
    rates = aws.usage_savings_plan_rates(
        client, CountingCaller(CallStats()), "Database", ["us-east-2"], TODAY, usage_types
    )
    by_key = {r.sku_key: r for r in rates}
    assert set(by_key) == {
        "rds|InstanceUsage:db.m5.large|CreateDBInstance:0002",
        "rds|Multi-AZClusterUsage:db.m5.large|CreateDBInstance:0014",
        "rds|InstanceUsage:db.m5.large|CreateDBInstance:0012",
        "rds|InstanceUsage:db.m5.large|CreateDBInstance:0015",
        "elasticache|NodeUsage:cache.m7g.large|CreateCacheCluster:Valkey",
        "rds|Aurora:ServerlessV2Usage|CreateDBInstance:0021",
        "dynamodb|ReadCapacityUnit-Hrs|CommittedThroughput",
    }
    mysql = by_key["rds|InstanceUsage:db.m5.large|CreateDBInstance:0002"]
    assert (mysql.pricing_model, mysql.term_months, mysql.payment_option) == (
        "sp_database",
        12,
        "no_upfront",
    )
    assert mysql.price_per_unit == Decimal("0.1368") and mysql.service == "rds"
    rcu = by_key["dynamodb|ReadCapacityUnit-Hrs|CommittedThroughput"]
    assert rcu.unit == "ReadCapacityUnit-Hrs" and rcu.price_per_unit == Decimal("0.0001144")


def test_usage_keys_match_across_usage_prices_and_rates():
    """The same RDS instance, as Cost Explorer bills it and as the Pricing API sells it."""
    from app.pricing.keys import usage_sku_key

    usage_key = usage_sku_key(
        "aws", "Amazon Relational Database Service", "db.r6g.large",
        database_engine="Aurora MySQL", usage_type="USE2-InstanceUsageIOOptimized:db.r6g.large",
        operation="CreateDBInstance:0016",
    )  # fmt: skip
    product = {
        "product": {
            "sku": "S1",
            "attributes": {
                "instanceType": "db.r6g.large",
                "databaseEngine": "Aurora MySQL",
                "regionCode": "us-east-2",
                "usagetype": "USE2-InstanceUsageIOOptimized:db.r6g.large",
                "operation": "CreateDBInstance:0016",
            },
        },
        "terms": {
            "OnDemand": {
                "t": {"priceDimensions": {"d": {"unit": "Hrs", "pricePerUnit": {"USD": "0.338"}}}}
            }
        },
    }
    (od,) = aws.parse_product("AmazonRDS", product, TODAY)
    assert (
        od.sku_key == usage_key == "rds|InstanceUsageIOOptimized:db.r6g.large|CreateDBInstance:0016"
    )
    # us-east-1 usage types carry no region prefix; other regions' prefixes are stripped.
    assert usage_sku_key("aws", "AWS Lambda", None, usage_type="Lambda-GB-Second") == (
        "lambda|Lambda-GB-Second|"
    )


def test_tiered_on_demand_and_dynamodb_reserved_capacity():
    """DynamoDB: a free tier before the paid one, and reserved capacity as an upfront fee plus
    an hourly rate per capacity unit (shapes from the live Pricing API, us-east-2)."""
    product = {
        "product": {
            "sku": "D1",
            "attributes": {
                "regionCode": "us-east-2",
                "usagetype": "USE2-ReadCapacityUnit-Hrs",
                "operation": "CommittedThroughput",
            },
        },
        "terms": {
            "OnDemand": {
                "t": {
                    "priceDimensions": {
                        "paid": {"unit": "ReadCapacityUnit-Hrs", "beginRange": "18600",
                                 "pricePerUnit": {"USD": "0.0001300000"}},
                        "free": {"unit": "ReadCapacityUnit-Hrs", "beginRange": "0",
                                 "pricePerUnit": {"USD": "0.0000000000"}},
                    }
                }
            },
            "Reserved": {
                "r": {
                    "termAttributes": {"LeaseContractLength": "1yr", "OfferingClass": "standard",
                                       "PurchaseOption": "Heavy Utilization"},
                    "priceDimensions": {
                        "h": {"unit": "ReadCapacityUnit-Hrs", "pricePerUnit": {"USD": "0.000025"}},
                        "u": {"unit": "Quantity", "pricePerUnit": {"USD": "0.3"}},
                    },
                }
            },
        },
    }  # fmt: skip
    od, ri = aws.parse_product("AmazonDynamoDB", product, TODAY)
    key = "dynamodb|ReadCapacityUnit-Hrs|CommittedThroughput"
    assert od.sku_key == ri.sku_key == key
    assert od.price_per_unit == Decimal("0.0001300000")
    assert (ri.unit, ri.term_months, ri.payment_option) == ("ReadCapacityUnit-Hrs", 12,
                                                            "partial_upfront")  # fmt: skip
    # $0.000025/hour + $0.30 upfront spread over the year
    assert ri.price_per_unit == (Decimal("0.000025") + Decimal("0.3") / 8760).quantize(
        Decimal("1e-14")
    )


def test_region_prefixes_stripped_including_eu_west_1():
    from app.collectors.aws.usage_types import parse_usage_type
    from app.pricing.keys import strip_region

    assert strip_region("EU-Notebk:ml.m5.xlarge") == "Notebk:ml.m5.xlarge"
    assert strip_region("USE2-InstanceUsage:db.m5.large") == "InstanceUsage:db.m5.large"
    # us-east-1 has no prefix; usage types that merely contain a dash are left alone.
    assert strip_region("ECS-Managed-Instances:c5.large") == "ECS-Managed-Instances:c5.large"
    assert parse_usage_type("EU-BoxUsage:m5.large")["region"] == "eu-west-1"
