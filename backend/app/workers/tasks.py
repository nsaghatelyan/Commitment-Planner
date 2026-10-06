"""Background jobs (arq). Collection and price sync are blocking, so they run in a thread."""

import asyncio
from typing import Any

from app.db import SessionLocal

DEFAULT_AWS_PRICE_REGIONS = ("us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1")
DEFAULT_AZURE_PRICE_REGIONS = ("eastus", "eastus2", "westus2", "westeurope", "northeurope")


def _collect(
    cloud_connection_id: str, collection_run_id: str | None, analyze: bool
) -> dict[str, Any]:
    from app.services.analysis import run_analysis as analyze_tenant
    from app.services.collection import run_collection

    with SessionLocal() as session:
        run = run_collection(session, cloud_connection_id, collection_run_id=collection_run_id)
        out = {"run_id": str(run.id), "status": run.status, "rows": run.rows_ingested}
        if analyze and run.status == "succeeded":
            analysis = analyze_tenant(session, run.tenant_id)
            out["analysis_run_id"] = str(analysis.id)
            out["analysis_status"] = analysis.status
        return out


async def collect_usage(
    ctx: dict[str, Any],
    cloud_connection_id: str,
    collection_run_id: str | None = None,
    analyze: bool = True,
) -> dict[str, Any]:
    """Pull usage, commitments, utilization, native recommendations and prices for one
    connection, then (by default) re-run the analysis for its client."""
    return await asyncio.to_thread(_collect, cloud_connection_id, collection_run_id, analyze)


def _analyze(tenant_id: str, analysis_run_id: str | None, risk_profile: str | None):
    from app.services.analysis import run_analysis as run

    with SessionLocal() as session:
        result = run(session, tenant_id, analysis_run_id=analysis_run_id, risk_profile=risk_profile)
        return {"analysis_run_id": str(result.id), "status": result.status}


async def run_analysis(
    ctx: dict[str, Any],
    tenant_id: str,
    analysis_run_id: str | None = None,
    risk_profile: str | None = None,
) -> dict[str, Any]:
    """Run the engine over stored usage and write recommendations to an analysis_run."""
    return await asyncio.to_thread(_analyze, tenant_id, analysis_run_id, risk_profile)


def _refresh_prices(aws_regions: list[str], azure_regions: list[str]) -> dict[str, int]:
    import boto3

    from app.collectors.azure.http import AzureHttp
    from app.collectors.cache import CountingCaller
    from app.collectors.types import CallStats
    from app.pricing import aws, azure
    from app.services.pricing import upsert_prices
    from app.timeutil import utc_today

    today = utc_today()
    call = CountingCaller(CallStats())
    pricing = boto3.client("pricing", region_name="us-east-1")
    savingsplans = boto3.client("savingsplans", region_name="us-east-1")
    counts = {}
    with SessionLocal() as session:
        for code in aws.SERVICE_CODES:
            counts[code] = upsert_prices(
                session, aws.on_demand_and_ri_prices(pricing, call, code, aws_regions, today)
            )
        counts["aws_sp"] = upsert_prices(
            session, aws.savings_plan_rates(savingsplans, call, aws_regions, today)
        )
        for plan_type in aws.USAGE_SP_PLANS:
            counts[f"aws_sp_{plan_type.lower()}_usage"] = upsert_prices(
                session,
                aws.usage_savings_plan_rates(savingsplans, call, plan_type, aws_regions, today),
            )
        counts["azure"] = upsert_prices(
            session, azure.retail_prices(AzureHttp(None), call, azure_regions, today=today)
        )
    return counts


async def refresh_prices(
    ctx: dict[str, Any],
    aws_regions: list[str] | None = None,
    azure_regions: list[str] | None = None,
) -> dict[str, int]:
    """Refresh the price table from the AWS Pricing API and the Azure Retail Prices API."""
    return await asyncio.to_thread(
        _refresh_prices,
        list(aws_regions or DEFAULT_AWS_PRICE_REGIONS),
        list(azure_regions or DEFAULT_AZURE_PRICE_REGIONS),
    )
