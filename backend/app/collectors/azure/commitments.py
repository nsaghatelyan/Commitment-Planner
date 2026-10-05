"""Existing Azure reservations and savings plans, and their daily utilization."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.collectors.azure.http import AzureHttp
from app.collectors.cache import CountingCaller
from app.collectors.types import AZURE_RI, AZURE_SP_COMPUTE, CommitmentRecord, UtilizationRecord
from app.usage.normalize import instance_family

CAPACITY_API = "2022-11-01"
BENEFITS_API = "2022-11-01"
COST_API = "2023-11-01"
TERMS = {"P1Y": 12, "P3Y": 36, "P5Y": 60}
HOURS_PER_MONTH = Decimal(730)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None


def commitment_key(arm_id: str) -> str:
    """Last GUID of an ARM id; benefit APIs sometimes return bare ids."""
    return arm_id.rstrip("/").rsplit("/", 1)[-1].lower()


def _payment(billing_plan: str | None) -> str | None:
    if not billing_plan:
        return None
    return "all_upfront" if billing_plan.lower() == "upfront" else "monthly"


def reservations(http: AzureHttp, call: CountingCaller) -> list[CommitmentRecord]:
    out = []
    orders = list(
        http.paged(
            "/providers/Microsoft.Capacity/reservationOrders",
            {"api-version": CAPACITY_API},
            call,
            "azure:ListReservationOrders",
        )
    )
    for order in orders:
        detail = call(
            "azure:GetReservationOrder",
            http.request,
            method="GET",
            url=order["id"],
            params={"api-version": CAPACITY_API, "$expand": "planInformation"},
        )
        oprops = detail.get("properties", {})
        total = _dec(
            oprops.get("planInformation", {}).get("pricingCurrencyTotal", {}).get("amount")
        )
        original_qty = Decimal(str(oprops.get("originalQuantity") or 0))
        term = TERMS.get(oprops.get("term", ""), 12)
        payment = _payment(oprops.get("billingPlan"))
        for res in http.paged(
            f"{order['id']}/reservations",
            {"api-version": CAPACITY_API},
            call,
            "azure:ListReservations",
        ):
            props = res.get("properties", {})
            if props.get("provisioningState") not in (None, "Succeeded", "Creating"):
                continue
            qty = int(props.get("quantity") or 0)
            share = (Decimal(qty) / original_qty) if original_qty else Decimal(1)
            cost = total * share if total is not None else None
            upfront = cost if payment == "all_upfront" else Decimal(0)
            monthly = cost / (term * HOURS_PER_MONTH) if cost and payment == "monthly" else None
            sku = res.get("sku", {}).get("name")
            start = _dt(props.get("benefitStartTime") or props.get("effectiveDateTime"))
            end = _dt(props.get("expiryDateTimeUtc") or props.get("expiryDateTime"))
            out.append(
                CommitmentRecord(
                    kind=AZURE_RI,
                    provider_commitment_id=res["id"].lower(),
                    owner_account_id=_first_subscription(props),
                    start_at=start or datetime.now(UTC),
                    end_at=end or (start or datetime.now(UTC)) + timedelta(days=term * 30),
                    term_months=term,
                    scope=props.get("appliedScopeType"),
                    service=props.get("reservedResourceType"),
                    region=res.get("location"),
                    instance_type=sku,
                    instance_family=instance_family(sku),
                    quantity=qty,
                    payment_option=payment,
                    upfront_cost=upfront,
                    recurring_hourly_cost=monthly,
                    state=props.get("provisioningState"),
                    attributes={
                        "displayName": props.get("displayName"),
                        "instanceFlexibility": props.get("instanceFlexibility"),
                        "orderId": order["id"].lower(),
                    },
                )
            )
    return out


def _first_subscription(props: dict[str, Any]) -> str | None:
    for scope in props.get("appliedScopes") or []:
        if scope.startswith("/subscriptions/"):
            return scope.split("/")[2]
    return None


def savings_plans(http: AzureHttp, call: CountingCaller) -> list[CommitmentRecord]:
    out = []
    for order in http.paged(
        "/providers/Microsoft.BillingBenefits/savingsPlanOrders",
        {"api-version": BENEFITS_API},
        call,
        "azure:ListSavingsPlanOrders",
    ):
        for sp in http.paged(
            f"{order['id']}/savingsPlans",
            {"api-version": BENEFITS_API},
            call,
            "azure:ListSavingsPlans",
        ):
            props = sp.get("properties", {})
            if props.get("provisioningState") not in (None, "Succeeded", "Creating"):
                continue
            term = TERMS.get(props.get("term", ""), 12)
            hourly = _dec(props.get("commitment", {}).get("amount"))
            start = _dt(props.get("benefitStartTime") or props.get("effectiveDateTime"))
            end = _dt(props.get("expiryDateTime"))
            out.append(
                CommitmentRecord(
                    kind=AZURE_SP_COMPUTE,
                    provider_commitment_id=sp["id"].lower(),
                    owner_account_id=_billing_subscription(props),
                    start_at=start or datetime.now(UTC),
                    end_at=end or (start or datetime.now(UTC)) + timedelta(days=term * 30),
                    term_months=term,
                    scope=props.get("appliedScopeType"),
                    service="Compute",
                    hourly_commitment=hourly,
                    payment_option="monthly"
                    if props.get("billingPlan") == "P1M"
                    else "all_upfront",
                    recurring_hourly_cost=hourly if props.get("billingPlan") == "P1M" else None,
                    state=props.get("provisioningState"),
                    attributes={
                        "displayName": props.get("displayName"),
                        "orderId": order["id"].lower(),
                        "currency": props.get("commitment", {}).get("currencyCode"),
                    },
                )
            )
    return out


def _billing_subscription(props: dict[str, Any]) -> str | None:
    sub = props.get("billingScopeId") or ""
    return sub.split("/")[2] if sub.startswith("/subscriptions/") else None


def utilization(
    http: AzureHttp, call: CountingCaller, scopes: list[str], start: date, end: date
) -> list[UtilizationRecord]:
    """Daily benefit utilization (reservations and savings plans) for each scope.

    `scopes`: the billing scope for EA/MCA, or each order's id for PAYG/CSP.
    """
    out = []
    last = end - timedelta(days=1)
    for scope in scopes:
        params = {
            "api-version": COST_API,
            "grainParameter": "Daily",
            "$filter": (
                f"properties/usageDate ge {start.isoformat()} "
                f"and properties/usageDate le {last.isoformat()}"
            ),
        }
        for item in http.paged(
            f"{scope.rstrip('/')}/providers/Microsoft.CostManagement/benefitUtilizationSummaries",
            params,
            call,
            "azure:BenefitUtilizationSummaries",
        ):
            props = item.get("properties", {})
            benefit = props.get("benefitId") or props.get("benefitResourceId")
            usage_date = props.get("usageDate")
            if not benefit or not usage_date:
                continue
            out.append(
                UtilizationRecord(
                    provider_commitment_id=commitment_key(benefit),
                    date=datetime.fromisoformat(usage_date).date(),
                    utilization_pct=_dec(props.get("avgUtilizationPercentage")) or Decimal(0),
                )
            )
    return out
