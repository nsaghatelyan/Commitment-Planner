"""AWS public prices: Pricing API (on-demand and RI) and Savings Plans offering rates.

Runs with the tool's own AWS credentials; client roles are not needed for public prices.
"""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

from app.collectors.aws.commitments import payment_option
from app.collectors.cache import CountingCaller
from app.pricing.keys import aws_ec2_key, aws_generic_key, aws_os, aws_rds_key
from app.pricing.types import HOURS_PER_MONTH, PriceRecord

SERVICE_CODES = {
    "AmazonEC2": "ec2",
    "AmazonRDS": "rds",
    "AmazonElastiCache": "elasticache",
    "AmazonES": "opensearch",
    "AmazonRedshift": "redshift",
    "AmazonMemoryDB": "memorydb",
}
LEASE_MONTHS = {"1yr": 12, "3yr": 36}
SP_OS = {
    "Linux/UNIX": "Linux",
    "Windows": "Windows",
    "Red Hat Enterprise Linux": "RHEL",
    "SUSE Linux": "SUSE",
}


def _sku_key(service_code: str, attrs: dict[str, str]) -> str | None:
    itype = attrs.get("instanceType")
    if not itype:
        return None
    if service_code == "AmazonEC2":
        os_name = aws_os(attrs.get("operatingSystem"), attrs.get("preInstalledSw"))
        return aws_ec2_key(itype, os_name, attrs.get("tenancy"))
    if service_code == "AmazonRDS":
        return aws_rds_key(itype, attrs.get("databaseEngine", ""), attrs.get("deploymentOption"))
    return aws_generic_key(SERVICE_CODES.get(service_code, service_code), itype)


def parse_product(service_code: str, product_json: str | dict, today: date) -> list[PriceRecord]:
    item = json.loads(product_json) if isinstance(product_json, str) else product_json
    attrs = item.get("product", {}).get("attributes", {})
    key = _sku_key(service_code, attrs)
    region = attrs.get("regionCode")
    if not key or not region:
        return []
    service = SERVICE_CODES.get(service_code, service_code)
    base_attrs = {"sku": item["product"].get("sku"), "usagetype": attrs.get("usagetype")}
    out = []
    for term in item.get("terms", {}).get("OnDemand", {}).values():
        for dim in term.get("priceDimensions", {}).values():
            usd = dim.get("pricePerUnit", {}).get("USD")
            if usd is None or Decimal(usd) == 0:
                continue
            out.append(
                PriceRecord(
                    provider="aws",
                    service=service,
                    sku_key=key,
                    region=region,
                    pricing_model="on_demand",
                    unit=dim.get("unit", "Hrs"),
                    price_per_unit=Decimal(usd),
                    effective_from=today,
                    attributes=base_attrs,
                )
            )
    for term in item.get("terms", {}).get("Reserved", {}).values():
        ta = term.get("termAttributes", {})
        months = LEASE_MONTHS.get(ta.get("LeaseContractLength", ""))
        if not months:
            continue
        upfront = Decimal(0)
        hourly = Decimal(0)
        for dim in term.get("priceDimensions", {}).values():
            usd = Decimal(dim.get("pricePerUnit", {}).get("USD", "0"))
            if dim.get("unit") == "Quantity":
                upfront += usd
            else:
                hourly += usd
        effective = hourly + upfront / (months * HOURS_PER_MONTH)
        out.append(
            PriceRecord(
                provider="aws",
                service=service,
                sku_key=key,
                region=region,
                pricing_model="ri",
                unit="Hrs",
                price_per_unit=effective.quantize(Decimal("0.00000001")),
                term_months=months,
                payment_option=payment_option(ta.get("PurchaseOption")),
                effective_from=today,
                attributes={
                    **base_attrs,
                    "offering_class": ta.get("OfferingClass"),
                    "upfront": str(upfront),
                    "hourly": str(hourly),
                },
            )
        )
    return out


def on_demand_and_ri_prices(
    pricing: Any,
    call: CountingCaller,
    service_code: str,
    regions: list[str],
    today: date,
    extra_filters: dict[str, str] | None = None,
) -> Iterator[PriceRecord]:
    """pricing: boto3 'pricing' client (us-east-1). One paginated query per region."""
    for region in regions:
        filters = [{"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}]
        if service_code == "AmazonEC2":
            filters += [
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Compute Instance"},
            ]
        for field, value in (extra_filters or {}).items():
            filters.append({"Type": "TERM_MATCH", "Field": field, "Value": value})
        token = None
        while True:
            kwargs: dict[str, Any] = {
                "ServiceCode": service_code,
                "Filters": filters,
                "FormatVersion": "aws_v1",
                "MaxResults": 100,
            }
            if token:
                kwargs["NextToken"] = token
            resp = call("pricing:GetProducts", pricing.get_products, **kwargs)
            for product in resp.get("PriceList", []):
                yield from parse_product(service_code, product, today)
            token = resp.get("NextToken")
            if not token:
                break


def savings_plan_rates(
    savingsplans: Any,
    call: CountingCaller,
    regions: list[str],
    today: date,
    plan_types: tuple[str, ...] = ("Compute", "EC2Instance"),
) -> Iterator[PriceRecord]:
    token = None
    while True:
        kwargs: dict[str, Any] = {
            "savingsPlanTypes": list(plan_types),
            "products": ["EC2"],
            "serviceCodes": ["AmazonEC2"],
            "filters": [{"name": "region", "values": regions}],
            "maxResults": 1000,
        }
        if token:
            kwargs["nextToken"] = token
        resp = call(
            "savingsplans:DescribeSavingsPlansOfferingRates",
            savingsplans.describe_savings_plans_offering_rates,
            **kwargs,
        )
        for rate in resp.get("searchResults", []):
            props = {p["name"]: p["value"] for p in rate.get("properties", [])}
            offering = rate.get("savingsPlanOffering", {})
            itype = props.get("instanceType")
            if not itype or "BoxUsage" not in rate.get("usageType", "BoxUsage"):
                continue
            os_name = SP_OS.get(
                props.get("productDescription", ""), props.get("productDescription")
            )
            months = round(int(offering.get("durationSeconds", 0)) / (365 * 24 * 3600) * 12)
            yield PriceRecord(
                provider="aws",
                service="ec2",
                sku_key=aws_ec2_key(itype, os_name or "Linux", props.get("tenancy")),
                region=props.get("region", ""),
                # Compute SP rates are "sp"; EC2 Instance SP rates "sp_instance" (they differ).
                pricing_model="sp_instance" if offering.get("planType") == "EC2Instance" else "sp",
                unit=rate.get("unit", "Hrs"),
                price_per_unit=Decimal(str(rate["rate"])),
                term_months=months,
                payment_option=payment_option(offering.get("paymentOption")),
                currency=offering.get("currency", "USD"),
                effective_from=today,
                attributes={
                    "plan_type": offering.get("planType"),
                    "usage_type": rate.get("usageType"),
                },
            )
        token = resp.get("nextToken")
        if not token:
            return
