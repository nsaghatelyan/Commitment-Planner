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
from app.pricing.keys import AWS_SERVICE_CODES, aws_ec2_key, aws_os, aws_usage_key
from app.pricing.types import HOURS_PER_MONTH, PriceRecord

# Pricing API service code -> short service (every AWS service the engine prices).
SERVICE_CODES = {code: service for service, code in AWS_SERVICE_CODES.items()}
LEASE_MONTHS = {"1yr": 12, "3yr": 36}
# Savings Plans rates priced per usage type, by plan type: products asked for, and the
# price model the rates are stored under. (EC2 rates are fetched by savings_plan_rates.)
USAGE_SP_PLANS = {
    "Compute": (("Fargate", "Lambda"), "sp"),
    "SageMaker": (("SageMaker",), "sp_sagemaker"),
    "Database": (
        ("RDS", "ElastiCache", "OpenSearch", "DynamoDB", "DocDB", "Neptune", "Timestream",
         "DMS", "Keyspaces", "DSQL"),
        "sp_database",
    ),
}  # fmt: skip
# Savings Plans product type -> short service.
SP_PRODUCT_SERVICES = {
    "RDS": "rds", "ElastiCache": "elasticache", "OpenSearch": "opensearch",
    "DynamoDB": "dynamodb", "DocDB": "docdb", "Neptune": "neptune", "Timestream": "timestream",
    "DMS": "dms", "Keyspaces": "keyspaces", "DSQL": "dsql", "Fargate": "fargate",
    "Lambda": "lambda", "SageMaker": "sagemaker",
}  # fmt: skip
SP_OS = {
    "Linux/UNIX": "Linux",
    "Windows": "Windows",
    "Red Hat Enterprise Linux": "RHEL",
    "SUSE Linux": "SUSE",
}


def _sku_key(service_code: str, attrs: dict[str, str]) -> str | None:
    if service_code == "AmazonEC2":
        itype = attrs.get("instanceType")
        if not itype:
            return None
        os_name = aws_os(attrs.get("operatingSystem"), attrs.get("preInstalledSw"))
        return aws_ec2_key(itype, os_name, attrs.get("tenancy"))
    usage_type = attrs.get("usagetype")
    if not usage_type:
        return None
    return aws_usage_key(
        SERVICE_CODES.get(service_code, service_code), usage_type, attrs.get("operation")
    )


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
        # Tiered prices (DynamoDB's free tier, Lambda volume tiers): the first paid tier.
        dims = sorted(
            term.get("priceDimensions", {}).values(),
            key=lambda d: float(d.get("beginRange") or 0),
        )
        dim = next((d for d in dims if Decimal(d.get("pricePerUnit", {}).get("USD", "0"))), None)
        if dim is None:
            continue
        out.append(
            PriceRecord(
                provider="aws",
                service=service,
                sku_key=key,
                region=region,
                pricing_model="on_demand",
                unit=dim.get("unit", "Hrs"),
                price_per_unit=Decimal(dim["pricePerUnit"]["USD"]),
                effective_from=today,
                attributes=base_attrs,
            )
        )
    for term in item.get("terms", {}).get("Reserved", {}).values():
        ta = term.get("termAttributes", {})
        months = LEASE_MONTHS.get(ta.get("LeaseContractLength", ""))
        # Standard RIs only; convertible ones would collide on the price identity.
        if not months or ta.get("OfferingClass", "standard") != "standard":
            continue
        upfront = Decimal(0)
        hourly = Decimal(0)
        unit = "Hrs"
        for dim in term.get("priceDimensions", {}).values():
            usd = Decimal(dim.get("pricePerUnit", {}).get("USD", "0"))
            if dim.get("unit") == "Quantity":
                upfront += usd
            else:
                hourly += usd
                unit = dim.get("unit", unit)
        effective = hourly + upfront / (months * HOURS_PER_MONTH)
        payment = payment_option(ta.get("PurchaseOption"))
        if ta.get("PurchaseOption") == "Heavy Utilization" and upfront and hourly:
            payment = "partial_upfront"  # DynamoDB reserved capacity: upfront fee + hourly
        out.append(
            PriceRecord(
                provider="aws",
                service=service,
                sku_key=key,
                region=region,
                pricing_model="ri",
                unit=unit,
                price_per_unit=effective.quantize(Decimal("1e-14")),
                term_months=months,
                payment_option=payment,
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
    instance_types: list[str] | None = None,
) -> Iterator[PriceRecord]:
    token = None
    filters = [{"name": "region", "values": regions}]
    if instance_types:
        filters.append({"name": "instanceType", "values": instance_types})
    while True:
        kwargs: dict[str, Any] = {
            "savingsPlanTypes": list(plan_types),
            "products": ["EC2"],
            "serviceCodes": ["AmazonEC2"],
            "filters": filters,
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


def usage_savings_plan_rates(
    savingsplans: Any,
    call: CountingCaller,
    plan_type: str,
    regions: list[str],
    today: date,
    usage_types: list[str] | None = None,
) -> Iterator[PriceRecord]:
    """Savings Plans rates keyed by usage type and operation, for every non-EC2 product of a
    plan type (Compute: Fargate, Lambda; SageMaker; Database: RDS/Aurora, ElastiCache,
    OpenSearch, DynamoDB, DocumentDB, Neptune, Timestream, DMS, Keyspaces, Aurora DSQL).
    usage_types narrows the query to exactly what a client uses."""
    products, model = USAGE_SP_PLANS[plan_type]
    rates: dict[tuple, list[PriceRecord]] = {}
    chunks = (
        [usage_types[i : i + 100] for i in range(0, len(usage_types), 100)]
        if usage_types
        else [None]
    )
    for chunk in chunks:
        token = None
        while True:
            kwargs: dict[str, Any] = {
                "savingsPlanTypes": [plan_type],
                "products": list(products),
                "filters": [{"name": "region", "values": regions}],
                "maxResults": 1000,
            }
            if chunk:
                kwargs["usageTypes"] = chunk
            if token:
                kwargs["nextToken"] = token
            resp = call(
                "savingsplans:DescribeSavingsPlansOfferingRates",
                savingsplans.describe_savings_plans_offering_rates,
                **kwargs,
            )
            for rate in resp.get("searchResults", []):
                record = _usage_sp_record(rate, model, today)
                if record:
                    ident = (record.sku_key, record.region, record.term_months,
                             record.payment_option)  # fmt: skip
                    rates.setdefault(ident, []).append(record)
            token = resp.get("nextToken")
            if not token:
                break
    for records in rates.values():
        # Two different rates behind one key would be a guess; skip it.
        if len({r.price_per_unit for r in records}) == 1:
            yield records[0]


def _usage_sp_record(rate: dict[str, Any], model: str, today: date) -> PriceRecord | None:
    service = SP_PRODUCT_SERVICES.get(rate.get("productType", ""))
    props = {p["name"]: p["value"] for p in rate.get("properties", [])}
    region, usage_type = props.get("region"), rate.get("usageType")
    if not service or not region or not usage_type:
        return None
    offering = rate.get("savingsPlanOffering", {})
    return PriceRecord(
        provider="aws",
        service=service,
        sku_key=aws_usage_key(service, usage_type, rate.get("operation")),
        region=region,
        pricing_model=model,
        unit=rate.get("unit", "Hrs"),
        price_per_unit=Decimal(str(rate["rate"])),
        term_months=round(int(offering.get("durationSeconds", 0)) / (365 * 24 * 3600) * 12),
        payment_option=payment_option(offering.get("paymentOption")),
        currency=offering.get("currency", "USD"),
        effective_from=today,
        attributes={"plan_type": offering.get("planType"), "usage_type": usage_type},
    )
