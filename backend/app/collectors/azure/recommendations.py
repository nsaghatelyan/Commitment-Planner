"""Azure's own savings plan (benefitRecommendations) and reservation recommendations."""

from decimal import Decimal
from typing import Any

from app.collectors.azure.http import AzureHttp
from app.collectors.cache import CountingCaller
from app.collectors.types import AZURE_RI, AZURE_SP_COMPUTE, NativeRecommendation
from app.usage.normalize import instance_family

BENEFIT_API = "2023-11-01"
CONSUMPTION_API = "2023-05-01"
TERMS = {"P1Y": 12, "P3Y": 36}
LOOKBACK = {"Last7Days": 7, "Last30Days": 30, "Last60Days": 60}


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("value")
    return Decimal(str(value)) if value not in (None, "") else None


def _monthly(period_amount: Decimal | None, lookback_days: int) -> Decimal | None:
    """The APIs report savings over the lookback period; scale to a 30-day month."""
    if period_amount is None:
        return None
    return (period_amount * Decimal(30) / Decimal(lookback_days)).quantize(Decimal("0.01"))


def savings_plan_recommendations(
    http: AzureHttp,
    call: CountingCaller,
    scope: str,
    terms: list[str],
    lookback: str = "Last30Days",
) -> list[NativeRecommendation]:
    out = []
    days = LOOKBACK[lookback]
    for term in terms:
        params = {
            "api-version": BENEFIT_API,
            "$filter": f"properties/lookBackPeriod eq '{lookback}' and properties/term eq '{term}'",
            "$expand": "properties/allRecommendationDetails",
        }
        for item in http.paged(
            f"{scope.rstrip('/')}/providers/Microsoft.CostManagement/benefitRecommendations",
            params,
            call,
            "azure:BenefitRecommendations",
        ):
            props = item.get("properties", {})
            detail = props.get("recommendationDetails", {})
            if not detail.get("commitmentAmount"):
                continue
            out.append(
                NativeRecommendation(
                    provider="azure",
                    kind=AZURE_SP_COMPUTE,
                    term_months=TERMS.get(props.get("term", term), 12),
                    payment_option=None,
                    lookback_days=days,
                    hourly_commitment=_amount(detail.get("commitmentAmount")),
                    estimated_monthly_savings=_monthly(_amount(detail.get("savingsAmount")), days),
                    scope=props.get("scope") or item.get("kind"),
                    raw=item,
                )
            )
    return out


def reservation_recommendations(
    http: AzureHttp,
    call: CountingCaller,
    scope: str,
    terms: list[str],
    lookback: str = "Last30Days",
    recommendation_scope: str = "Shared",
) -> list[NativeRecommendation]:
    out = []
    days = LOOKBACK[lookback]
    for term in terms:
        params = {
            "api-version": CONSUMPTION_API,
            "$filter": (
                f"properties/scope eq '{recommendation_scope}' "
                f"and properties/lookBackPeriod eq '{lookback}' and properties/term eq '{term}'"
            ),
        }
        for item in http.paged(
            f"{scope.rstrip('/')}/providers/Microsoft.Consumption/reservationRecommendations",
            params,
            call,
            "azure:ReservationRecommendations",
        ):
            props = item.get("properties", {})
            sku = props.get("skuName") or item.get("sku")
            if isinstance(sku, dict):
                sku = sku.get("name")
            out.append(
                NativeRecommendation(
                    provider="azure",
                    kind=AZURE_RI,
                    term_months=TERMS.get(props.get("term", term), 12),
                    payment_option=None,
                    lookback_days=days,
                    region=props.get("location") or item.get("location"),
                    instance_type=sku,
                    instance_family=instance_family(sku),
                    quantity=_amount(props.get("recommendedQuantity")),
                    estimated_monthly_savings=_monthly(_amount(props.get("netSavings")), days),
                    scope=props.get("scope", recommendation_scope),
                    raw=item,
                )
            )
    return out
