# ruff: noqa: C408  (keyword-style dicts read better for column data)
"""Regenerate the small Parquet export fixtures: python tests/fixtures/make_parquet_fixtures.py

Shapes follow AWS Data Exports (FOCUS 1.0 and CUR 2.0) and Azure Cost Management FOCUS exports.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).parent
H0 = datetime(2026, 9, 1, 10, tzinfo=UTC)
H1 = datetime(2026, 9, 1, 11, tzinfo=UTC)
SP_ARN = "arn:aws:savingsplans::111111111111:savingsplan/8f1c2a2e-1b5e-4c7e-9d7e-1a1b1c1d1e1f"
RI_ARN = (
    "arn:aws:ec2:us-east-1:222222222222:reserved-instances/7a6b5c4d-1111-2222-3333-444455556666"
)
TAGS = pa.map_(pa.string(), pa.string())


def aws_focus() -> pa.Table:
    rows = [
        # on-demand usage
        dict(
            ChargeCategory="Usage",
            PricingCategory="Standard",
            BilledCost=0.192,
            EffectiveCost=0.192,
            ListCost=0.192,
            ConsumedQuantity=1.0,
            x_UsageType="USE1-BoxUsage:m5.xlarge",
            ResourceId="i-0aaa",
            ResourceName="web-01",
            CommitmentDiscountId=None,
            CommitmentDiscountType=None,
            CommitmentDiscountStatus=None,
            Tags=[("env", "prod")],
        ),
        # SP-covered usage
        dict(
            ChargeCategory="Usage",
            PricingCategory="Committed",
            BilledCost=0.0,
            EffectiveCost=0.1286,
            ListCost=0.192,
            ConsumedQuantity=1.0,
            x_UsageType="USE1-BoxUsage:m5.xlarge",
            ResourceId="i-0bbb",
            ResourceName="web-02",
            CommitmentDiscountId=SP_ARN,
            CommitmentDiscountType="Savings Plan",
            CommitmentDiscountStatus="Used",
            Tags=[("env", "prod")],
        ),
        # unused part of the SP for that hour
        dict(
            ChargeCategory="Usage",
            PricingCategory="Committed",
            BilledCost=0.0,
            EffectiveCost=0.0714,
            ListCost=0.0,
            ConsumedQuantity=None,
            x_UsageType="ComputeSP:1yrNoUpfront",
            ResourceId=None,
            ResourceName=None,
            CommitmentDiscountId=SP_ARN,
            CommitmentDiscountType="Savings Plan",
            CommitmentDiscountStatus="Unused",
            Tags=[],
        ),
        # recurring SP fee
        dict(
            ChargeCategory="Purchase",
            PricingCategory="Committed",
            BilledCost=0.2,
            EffectiveCost=0.0,
            ListCost=0.0,
            ConsumedQuantity=None,
            x_UsageType="ComputeSP:1yrNoUpfront",
            ResourceId=None,
            ResourceName=None,
            CommitmentDiscountId=SP_ARN,
            CommitmentDiscountType="Savings Plan",
            CommitmentDiscountStatus=None,
            Tags=[],
        ),
        # spot
        dict(
            ChargeCategory="Usage",
            PricingCategory="Dynamic",
            BilledCost=0.11,
            EffectiveCost=0.11,
            ListCost=0.34,
            ConsumedQuantity=1.0,
            x_UsageType="USE1-SpotUsage:c5.2xlarge",
            ResourceId="i-0ccc",
            ResourceName="spark-01",
            CommitmentDiscountId=None,
            CommitmentDiscountType=None,
            CommitmentDiscountStatus=None,
            Tags=[("app", "spark")],
        ),
    ]
    common = dict(
        BillingAccountId="111111111111",
        BillingAccountName="payer",
        BillingCurrency="USD",
        ChargePeriodStart=H0,
        ChargePeriodEnd=H1,
        RegionId="us-east-1",
        RegionName="US East (N. Virginia)",
        ServiceCategory="Compute",
        ServiceName="Amazon Elastic Compute Cloud",
        SkuId="ABCD1234",
        SubAccountId="222222222222",
        SubAccountName="prod",
        ConsumedUnit="Hrs",
        PricingUnit="Hrs",
        ProviderName="AWS",
        PublisherName="AWS",
        x_ServiceCode="AmazonEC2",
        x_Operation="RunInstances",
    )
    data = [{**common, **r} for r in rows]
    cols = {k: [d[k] for d in data] for k in data[0]}
    schema_overrides = {
        "Tags": TAGS,
        "ChargePeriodStart": pa.timestamp("ms", tz="UTC"),
        "ChargePeriodEnd": pa.timestamp("ms", tz="UTC"),
    }
    return pa.table({k: pa.array(v, schema_overrides.get(k)) for k, v in cols.items()})


def aws_cur2() -> pa.Table:
    base = dict(
        bill_payer_account_id="111111111111",
        line_item_usage_account_id="222222222222",
        line_item_usage_account_name="prod",
        line_item_usage_start_date=H0,
        line_item_usage_end_date=H1,
        line_item_product_code="AmazonEC2",
        product_sku="ABCD1234",
        product_region_code="us-east-1",
        product_instance_type="m5.xlarge",
        product_instance_family="General purpose",
        product_operating_system="Linux",
        product_tenancy="Shared",
        product_database_engine=None,
        product_deployment_option=None,
        pricing_term="OnDemand",
        pricing_unit="Hrs",
        line_item_usage_type="USE1-BoxUsage:m5.xlarge",
        savings_plan_savings_plan_a_r_n="",
        reservation_reservation_a_r_n="",
        savings_plan_savings_plan_effective_cost=0.0,
        reservation_effective_cost=0.0,
        savings_plan_total_commitment_to_date=0.0,
        savings_plan_used_commitment=0.0,
        reservation_unused_amortized_upfront_fee_for_billing_period=0.0,
        reservation_unused_recurring_fee=0.0,
        line_item_normalized_usage_amount=8.0,
        line_item_usage_amount=1.0,
        pricing_public_on_demand_cost=0.192,
        line_item_resource_id="i-0aaa",
        resource_tags=[("user_env", "prod")],
    )
    rows = [
        dict(line_item_line_item_type="Usage", line_item_unblended_cost=0.192),
        dict(
            line_item_line_item_type="SavingsPlanCoveredUsage",
            line_item_unblended_cost=0.192,
            savings_plan_savings_plan_a_r_n=SP_ARN,
            savings_plan_savings_plan_effective_cost=0.1286,
            line_item_resource_id="i-0bbb",
        ),
        dict(
            line_item_line_item_type="SavingsPlanNegation",
            line_item_unblended_cost=-0.192,
            savings_plan_savings_plan_a_r_n=SP_ARN,
            line_item_resource_id="i-0bbb",
        ),
        dict(
            line_item_line_item_type="SavingsPlanRecurringFee",
            line_item_unblended_cost=0.2,
            savings_plan_savings_plan_a_r_n=SP_ARN,
            savings_plan_total_commitment_to_date=0.2,
            savings_plan_used_commitment=0.1286,
            line_item_resource_id="",
            line_item_usage_amount=None,
            pricing_public_on_demand_cost=0.0,
        ),
        dict(
            line_item_line_item_type="DiscountedUsage",
            line_item_unblended_cost=0.0,
            reservation_reservation_a_r_n=RI_ARN,
            reservation_effective_cost=0.12,
            line_item_resource_id="i-0ddd",
        ),
        dict(
            line_item_line_item_type="RIFee",
            line_item_unblended_cost=0.24,
            reservation_reservation_a_r_n=RI_ARN,
            reservation_unused_recurring_fee=0.12,
            line_item_resource_id="",
            line_item_usage_amount=2.0,
            pricing_public_on_demand_cost=0.0,
        ),
        dict(
            line_item_line_item_type="Tax",
            line_item_unblended_cost=0.05,
            line_item_resource_id="",
            line_item_usage_amount=None,
            pricing_public_on_demand_cost=0.0,
        ),
    ]
    data = [{**base, **r} for r in rows]
    cols = {k: [d.get(k) for d in data] for k in base | rows[0]}
    types = {
        "resource_tags": TAGS,
        "line_item_usage_start_date": pa.timestamp("ms", tz="UTC"),
        "line_item_usage_end_date": pa.timestamp("ms", tz="UTC"),
        "product_database_engine": pa.string(),
        "product_deployment_option": pa.string(),
    }
    return pa.table({k: pa.array(v, types.get(k)) for k, v in cols.items()})


def azure_focus() -> pa.Table:
    sub = "3f2a1b0c-1111-2222-3333-444455556666"
    res = (
        f"/subscriptions/{sub}/resourceGroups/rg-api/providers/Microsoft.Compute/"
        "virtualMachines/vm-api-01"
    )
    ri = (
        "/providers/Microsoft.Capacity/reservationOrders/aaaa1111-0000-0000-0000-000000000001/"
        "reservations/bbbb2222-0000-0000-0000-000000000002"
    )
    common = dict(
        BillingAccountId="84251234",
        BillingAccountName="Contoso EA",
        BillingCurrency="USD",
        ChargePeriodStart="2026-09-01T00:00:00Z",
        ChargePeriodEnd="2026-09-02T00:00:00Z",
        RegionId="eastus",
        RegionName="East US",
        ServiceCategory="Compute",
        ServiceName="Virtual Machines",
        SkuId="DZH318Z0BQPS",
        SubAccountId=sub,
        SubAccountName="prod",
        ConsumedUnit="Hours",
        PricingUnit="1 Hour",
        ProviderName="Microsoft",
        ResourceId=res,
        ResourceName="vm-api-01",
        x_SkuDetails=json.dumps(
            {"ServiceType": "Standard_D4s_v3", "VCPUs": "4", "ImageType": "Canonical"}
        ),
        Tags=json.dumps({"app": "api", "env": "prod"}),
    )
    rows = [
        dict(
            ChargeCategory="Usage",
            PricingCategory="Standard",
            BilledCost=2.304,
            EffectiveCost=2.304,
            ListCost=4.608,
            ConsumedQuantity=12.0,
            CommitmentDiscountId=None,
            CommitmentDiscountType=None,
            CommitmentDiscountStatus=None,
        ),
        dict(
            ChargeCategory="Usage",
            PricingCategory="Committed",
            BilledCost=0.0,
            EffectiveCost=0.9216,
            ListCost=2.304,
            ConsumedQuantity=12.0,
            CommitmentDiscountId=ri,
            CommitmentDiscountType="Reservation",
            CommitmentDiscountStatus="Used",
        ),
        dict(
            ChargeCategory="Usage",
            PricingCategory="Committed",
            BilledCost=0.0,
            EffectiveCost=0.4,
            ListCost=0.0,
            ConsumedQuantity=None,
            CommitmentDiscountId=ri,
            CommitmentDiscountType="Reservation",
            CommitmentDiscountStatus="Unused",
            ResourceId=None,
            ResourceName=None,
        ),
        dict(
            ChargeCategory="Purchase",
            PricingCategory="Committed",
            BilledCost=1200.0,
            EffectiveCost=0.0,
            ListCost=0.0,
            ConsumedQuantity=None,
            CommitmentDiscountId=ri,
            CommitmentDiscountType="Reservation",
            CommitmentDiscountStatus=None,
            ResourceId=None,
            ResourceName=None,
        ),
    ]
    data = [{**common, **r} for r in rows]
    cols = {k: [d[k] for d in data] for k in data[0]}
    return pa.table(
        {
            k: pa.array(v, pa.string() if k.startswith("Commitment") else None)
            for k, v in cols.items()
        }
    )


if __name__ == "__main__":
    pq.write_table(aws_focus(), HERE / "aws" / "focus_1_0_export.parquet")
    pq.write_table(aws_cur2(), HERE / "aws" / "cur_2_0_export.parquet")
    pq.write_table(azure_focus(), HERE / "azure" / "focus_export.parquet")
    print("written")
