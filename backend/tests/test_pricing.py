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
