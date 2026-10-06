"""AWS's own Savings Plans and Reserved Instance purchase recommendations."""

from decimal import Decimal
from typing import Any

from app.collectors.aws.commitments import SP_KINDS, payment_option
from app.collectors.cache import CountingCaller
from app.collectors.types import AWS_RI, NativeRecommendation
from app.usage.normalize import instance_family

TERMS = {"ONE_YEAR": 12, "THREE_YEARS": 36}
LOOKBACK = {"SEVEN_DAYS": 7, "THIRTY_DAYS": 30, "SIXTY_DAYS": 60}
SP_TYPES = {
    "COMPUTE_SP": "Compute",
    "EC2_INSTANCE_SP": "EC2Instance",
    "SAGEMAKER_SP": "SageMaker",
    "DATABASE_SP": "Database",
}
# Database Savings Plans are sold only as 1-year No Upfront; other combinations would be
# rejected (and each Cost Explorer call is billed).
SP_TYPE_OPTIONS = {"DATABASE_SP": ({"ONE_YEAR"}, {"NO_UPFRONT"})}
RI_SERVICES = [
    "Amazon Elastic Compute Cloud - Compute",
    "Amazon Relational Database Service",
    "Amazon ElastiCache",
    "Amazon OpenSearch Service",
    "Amazon Redshift",
    # Cost Explorer's name for it; elsewhere the service is "Amazon MemoryDB".
    "Amazon MemoryDB Service",
    "Amazon DynamoDB Service",
]


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None


def savings_plan_recommendations(
    ce: Any,
    call: CountingCaller,
    terms: list[str],
    payments: list[str],
    lookback: str = "THIRTY_DAYS",
) -> list[NativeRecommendation]:
    out = []
    for sp_type, kind_key in SP_TYPES.items():
        allowed_terms, allowed_payments = SP_TYPE_OPTIONS.get(sp_type, (None, None))
        for term in terms:
            if allowed_terms is not None and term not in allowed_terms:
                continue
            for payment in payments:
                if allowed_payments is not None and payment not in allowed_payments:
                    continue
                kwargs = {
                    "SavingsPlansType": sp_type,
                    "TermInYears": term,
                    "PaymentOption": payment,
                    "LookbackPeriodInDays": lookback,
                    "AccountScope": "PAYER",
                }
                resp = call(
                    "ce:GetSavingsPlansPurchaseRecommendation",
                    ce.get_savings_plans_purchase_recommendation,
                    cache_key=kwargs,
                    **kwargs,
                )
                rec = resp.get("SavingsPlansPurchaseRecommendation", {})
                for detail in rec.get("SavingsPlansPurchaseRecommendationDetails", []):
                    sp = detail.get("SavingsPlansDetails", {})
                    out.append(
                        NativeRecommendation(
                            provider="aws",
                            kind=SP_KINDS[kind_key],
                            term_months=TERMS[term],
                            payment_option=payment_option(payment),
                            lookback_days=LOOKBACK[lookback],
                            region=sp.get("Region"),
                            instance_family=sp.get("InstanceFamily"),
                            hourly_commitment=_dec(detail.get("HourlyCommitmentToPurchase")),
                            estimated_monthly_savings=_dec(
                                detail.get("EstimatedMonthlySavingsAmount")
                            ),
                            upfront_cost=_dec(detail.get("UpfrontCost")),
                            scope="organization",
                            raw=detail,
                        )
                    )
    return out


def reservation_recommendations(
    ce: Any,
    call: CountingCaller,
    terms: list[str],
    payments: list[str],
    lookback: str = "THIRTY_DAYS",
) -> list[NativeRecommendation]:
    out = []
    for service in RI_SERVICES:
        for term in terms:
            for payment in payments:
                kwargs = {
                    "Service": service,
                    "TermInYears": term,
                    "PaymentOption": payment,
                    "LookbackPeriodInDays": lookback,
                    "AccountScope": "PAYER",
                }
                resp = call(
                    "ce:GetReservationPurchaseRecommendation",
                    ce.get_reservation_purchase_recommendation,
                    cache_key=kwargs,
                    **kwargs,
                )
                for rec in resp.get("Recommendations", []):
                    for detail in rec.get("RecommendationDetails", []):
                        # InstanceDetails holds one service-specific dict, e.g. EC2InstanceDetails.
                        inst = next(iter(detail.get("InstanceDetails", {}).values()), {})
                        itype = inst.get("InstanceType") or inst.get("NodeType")
                        out.append(
                            NativeRecommendation(
                                provider="aws",
                                kind=AWS_RI,
                                term_months=TERMS[term],
                                payment_option=payment_option(payment),
                                lookback_days=LOOKBACK[lookback],
                                region=inst.get("Region"),
                                instance_type=itype,
                                instance_family=inst.get("Family") or instance_family(itype),
                                quantity=_dec(detail.get("RecommendedNumberOfInstancesToPurchase")),
                                estimated_monthly_savings=_dec(
                                    detail.get("EstimatedMonthlySavingsAmount")
                                ),
                                upfront_cost=_dec(detail.get("UpfrontCost")),
                                scope=detail.get("AccountId"),
                                raw={"service": service, **detail},
                            )
                        )
    return out
