"""Existing AWS commitments: Describe* APIs in the role's account, plus Cost Explorer for
commitments owned by other member accounts and for DynamoDB reserved capacity (which has no
public Describe API in boto3)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any

from app.collectors.cache import CountingCaller
from app.collectors.types import (
    AWS_RI,
    AWS_SP_COMPUTE,
    AWS_SP_DATABASE,
    AWS_SP_EC2,
    AWS_SP_SAGEMAKER,
    CommitmentRecord,
    UtilizationRecord,
)
from app.usage.normalize import instance_family

SP_KINDS = {
    "Compute": AWS_SP_COMPUTE,
    "EC2Instance": AWS_SP_EC2,
    "SageMaker": AWS_SP_SAGEMAKER,
    "Database": AWS_SP_DATABASE,
}

PAYMENT_OPTIONS = {
    "all upfront": "all_upfront",
    "partial upfront": "partial_upfront",
    "no upfront": "no_upfront",
    "heavy utilization": "all_upfront",
    "medium utilization": "partial_upfront",
    "light utilization": "no_upfront",
}


def payment_option(value: str | None) -> str | None:
    if not value:
        return None
    return PAYMENT_OPTIONS.get(value.lower().replace("_", " "), value)


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def _months(seconds: float) -> int:
    return round(float(seconds) / (365 * 24 * 3600) * 12)


def _hourly(recurring: list[dict[str, Any]], amount_key: str, freq_key: str) -> Decimal | None:
    total = Decimal(0)
    for charge in recurring or []:
        if str(charge.get(freq_key, "")).lower() == "hourly":
            total += Decimal(str(charge.get(amount_key, 0)))
    return total or None


def _account_from_arn(arn: str | None) -> str | None:
    parts = (arn or "").split(":")
    return parts[4] if len(parts) > 4 and parts[4] else None


def amortized_hourly(record: CommitmentRecord) -> Decimal | None:
    """Hourly cost of the whole commitment with upfront spread over the term.

    For savings plans this is the hourly commitment itself. For reservations, upfront_cost and
    recurring_hourly_cost are totals for all units, so no quantity factor is applied here.
    """
    if record.hourly_commitment is not None:
        return record.hourly_commitment
    upfront = record.upfront_cost or Decimal(0)
    if record.recurring_hourly_cost is None and not upfront:
        return None
    hours = Decimal(record.term_months) * Decimal(730)
    spread = upfront / hours if hours else Decimal(0)
    return ((record.recurring_hourly_cost or Decimal(0)) + spread).quantize(Decimal("0.000001"))


def savings_plans(client: Any, call: CountingCaller) -> list[CommitmentRecord]:
    out = []
    token = None
    while True:
        kwargs: dict[str, Any] = {"states": ["active", "queued", "payment-pending"]}
        if token:
            kwargs["nextToken"] = token
        resp = call("savingsplans:DescribeSavingsPlans", client.describe_savings_plans, **kwargs)
        for sp in resp.get("savingsPlans", []):
            out.append(
                CommitmentRecord(
                    kind=SP_KINDS.get(sp.get("savingsPlanType", ""), AWS_SP_COMPUTE),
                    provider_commitment_id=sp["savingsPlanArn"],
                    owner_account_id=_account_from_arn(sp.get("savingsPlanArn")),
                    start_at=_dt(sp["start"]),
                    end_at=_dt(sp["end"]),
                    term_months=_months(sp.get("termDurationInSeconds", 0)),
                    scope="organization",
                    service=", ".join(sp.get("productTypes", [])) or None,
                    region=sp.get("region") or None,
                    instance_family=sp.get("ec2InstanceFamily") or None,
                    hourly_commitment=_dec(sp.get("commitment")),
                    payment_option=payment_option(sp.get("paymentOption")),
                    upfront_cost=_dec(sp.get("upfrontPaymentAmount")),
                    recurring_hourly_cost=_dec(sp.get("recurringPaymentAmount")),
                    state=sp.get("state"),
                    attributes={"savingsPlanId": sp.get("savingsPlanId")},
                )
            )
        token = resp.get("nextToken")
        if not token:
            return out


def _ri(
    *,
    rid: str,
    account: str | None,
    service: str,
    region: str,
    instance_type: str,
    count: int,
    start: Any,
    duration: int,
    fixed: Any,
    usage_price: Any,
    recurring: Decimal | None,
    offering: str | None,
    state: str | None,
    scope: str | None = "Region",
    attributes: dict[str, Any] | None = None,
) -> CommitmentRecord:
    start_at = _dt(start)
    # API prices are per unit; the record stores totals for the whole reservation.
    hourly = ((recurring or Decimal(0)) + (_dec(usage_price) or Decimal(0))) * count
    upfront = (_dec(fixed) or Decimal(0)) * count
    return CommitmentRecord(
        kind=AWS_RI,
        provider_commitment_id=rid,
        owner_account_id=account,
        start_at=start_at,
        end_at=start_at + timedelta(seconds=duration),
        term_months=_months(duration),
        scope=scope,
        service=service,
        region=region,
        instance_type=instance_type,
        instance_family=instance_family(instance_type),
        quantity=count,
        payment_option=payment_option(offering),
        upfront_cost=upfront,
        recurring_hourly_cost=hourly or None,
        state=state,
        attributes=attributes or {},
    )


def _tolerant(call: CountingCaller, warn, api: str, fn, **kwargs) -> dict:
    """A Describe call that may fail for one region/service without failing collection
    (service not offered in the region, or not allowed by the IAM policy)."""
    from botocore.exceptions import BotoCoreError, ClientError

    from app.collectors.aws.errors import explain

    try:
        return call(api, fn, **kwargs)
    except (ClientError, BotoCoreError) as exc:
        if warn:
            warn(f"{api}: {explain(exc)}")
        return {}


@cache
def _offered_regions(service: str) -> frozenset[str]:
    import boto3

    return frozenset(boto3.session.Session().get_available_regions(service))


def offered_in(service: str, region: str) -> bool:
    """Per botocore's endpoint data. Unknown (no data for the service) counts as offered."""
    regions = _offered_regions(service)
    return not regions or region in regions


def reserved_instances(
    session_client,
    call: CountingCaller,
    regions: list[str],
    account_id: str | None,
    warn=None,
) -> list[CommitmentRecord]:
    """session_client(service, region) -> boto3 client."""
    out: list[CommitmentRecord] = []

    def call_(api, fn, **kwargs):
        # e.g. MemoryDB has no endpoint at all in some regions; that isn't worth a warning.
        if not offered_in(api.split(":", 1)[0], region):
            return {}
        return _tolerant(call, warn, f"{api} ({region})", fn, **kwargs)

    for region in regions:
        ec2 = session_client("ec2", region)
        resp = call_(
            "ec2:DescribeReservedInstances",
            ec2.describe_reserved_instances,
            Filters=[{"Name": "state", "Values": ["active", "payment-pending"]}],
        )
        for ri in resp.get("ReservedInstances", []):
            out.append(
                _ri(
                    rid=ri["ReservedInstancesId"],
                    account=account_id,
                    service="Amazon Elastic Compute Cloud - Compute",
                    region=region,
                    instance_type=ri["InstanceType"],
                    count=ri.get("InstanceCount", 1),
                    start=ri["Start"],
                    duration=ri["Duration"],
                    fixed=ri.get("FixedPrice"),
                    usage_price=ri.get("UsagePrice"),
                    recurring=_hourly(ri.get("RecurringCharges"), "Amount", "Frequency"),
                    offering=ri.get("OfferingType"),
                    state=ri.get("State"),
                    scope=ri.get("Scope"),
                    attributes={
                        "platform": ri.get("ProductDescription"),
                        "tenancy": ri.get("InstanceTenancy"),
                        "offering_class": ri.get("OfferingClass"),
                        "availability_zone": ri.get("AvailabilityZone"),
                    },
                )
            )

        rds = session_client("rds", region)
        resp = call_("rds:DescribeReservedDBInstances", rds.describe_reserved_db_instances)
        for ri in resp.get("ReservedDBInstances", []):
            if ri.get("State") not in ("active", "payment-pending"):
                continue
            out.append(
                _ri(
                    rid=ri.get("ReservedDBInstanceArn") or ri["ReservedDBInstanceId"],
                    account=_account_from_arn(ri.get("ReservedDBInstanceArn")) or account_id,
                    service="Amazon Relational Database Service",
                    region=region,
                    instance_type=ri["DBInstanceClass"],
                    count=ri.get("DBInstanceCount", 1),
                    start=ri["StartTime"],
                    duration=ri["Duration"],
                    fixed=ri.get("FixedPrice"),
                    usage_price=ri.get("UsagePrice"),
                    recurring=_hourly(
                        ri.get("RecurringCharges"),
                        "RecurringChargeAmount",
                        "RecurringChargeFrequency",
                    ),
                    offering=ri.get("OfferingType"),
                    state=ri.get("State"),
                    attributes={
                        "database_engine": ri.get("ProductDescription"),
                        "deployment_option": "Multi-AZ" if ri.get("MultiAZ") else "Single-AZ",
                    },
                )
            )

        cache = session_client("elasticache", region)
        resp = call_("elasticache:DescribeReservedCacheNodes", cache.describe_reserved_cache_nodes)
        for ri in resp.get("ReservedCacheNodes", []):
            if ri.get("State") not in ("active", "payment-pending"):
                continue
            out.append(
                _ri(
                    rid=ri.get("ReservationARN") or ri["ReservedCacheNodeId"],
                    account=_account_from_arn(ri.get("ReservationARN")) or account_id,
                    service="Amazon ElastiCache",
                    region=region,
                    instance_type=ri["CacheNodeType"],
                    count=ri.get("CacheNodeCount", 1),
                    start=ri["StartTime"],
                    duration=ri["Duration"],
                    fixed=ri.get("FixedPrice"),
                    usage_price=ri.get("UsagePrice"),
                    recurring=_hourly(
                        ri.get("RecurringCharges"),
                        "RecurringChargeAmount",
                        "RecurringChargeFrequency",
                    ),
                    offering=ri.get("OfferingType"),
                    state=ri.get("State"),
                    attributes={"engine": ri.get("ProductDescription")},
                )
            )

        search = session_client("opensearch", region)
        resp = call_("opensearch:DescribeReservedInstances", search.describe_reserved_instances)
        for ri in resp.get("ReservedInstances", []):
            if ri.get("State") not in ("active", "payment-pending"):
                continue
            out.append(
                _ri(
                    rid=ri["ReservedInstanceId"],
                    account=account_id,
                    service="Amazon OpenSearch Service",
                    region=region,
                    instance_type=ri["InstanceType"],
                    count=ri.get("InstanceCount", 1),
                    start=ri["StartTime"],
                    duration=ri["Duration"],
                    fixed=ri.get("FixedPrice"),
                    usage_price=ri.get("UsagePrice"),
                    recurring=_hourly(
                        ri.get("RecurringCharges"),
                        "RecurringChargeAmount",
                        "RecurringChargeFrequency",
                    ),
                    offering=ri.get("PaymentOption"),
                    state=ri.get("State"),
                )
            )

        redshift = session_client("redshift", region)
        resp = call_("redshift:DescribeReservedNodes", redshift.describe_reserved_nodes)
        for ri in resp.get("ReservedNodes", []):
            if ri.get("State") not in ("active", "payment-pending"):
                continue
            out.append(
                _ri(
                    rid=ri["ReservedNodeId"],
                    account=account_id,
                    service="Amazon Redshift",
                    region=region,
                    instance_type=ri["NodeType"],
                    count=ri.get("NodeCount", 1),
                    start=ri["StartTime"],
                    duration=ri["Duration"],
                    fixed=ri.get("FixedPrice"),
                    usage_price=ri.get("UsagePrice"),
                    recurring=_hourly(
                        ri.get("RecurringCharges"),
                        "RecurringChargeAmount",
                        "RecurringChargeFrequency",
                    ),
                    offering=ri.get("OfferingType"),
                    state=ri.get("State"),
                )
            )

        memorydb = session_client("memorydb", region)
        resp = call_("memorydb:DescribeReservedNodes", memorydb.describe_reserved_nodes)
        for ri in resp.get("ReservedNodes", []):
            if ri.get("State") not in ("active", "payment-pending"):
                continue
            out.append(
                _ri(
                    rid=ri.get("ARN") or ri["ReservationId"],
                    account=_account_from_arn(ri.get("ARN")) or account_id,
                    service="Amazon MemoryDB",
                    region=region,
                    instance_type=ri["NodeType"],
                    count=ri.get("NodeCount", 1),
                    start=ri["StartTime"],
                    duration=ri["Duration"],
                    fixed=ri.get("FixedPrice"),
                    usage_price=None,
                    recurring=_hourly(
                        ri.get("RecurringCharges"),
                        "RecurringChargeAmount",
                        "RecurringChargeFrequency",
                    ),
                    offering=ri.get("OfferingType"),
                    state=ri.get("State"),
                )
            )
    return out


def ri_utilization(
    ce: Any, call: CountingCaller, start: date, end: date, daily: bool = True
) -> tuple[list[UtilizationRecord], dict[str, dict[str, Any]]]:
    """Utilization per reservation (org-wide), plus each reservation's attributes.

    Cost Explorer rejects Granularity when grouping by SUBSCRIPTION_ID, so daily figures take
    one (billed) call per day; daily=False makes one call covering the whole window."""
    records: list[UtilizationRecord] = []
    attributes: dict[str, dict[str, Any]] = {}
    step = timedelta(days=1) if daily else end - start
    day = start
    while day < end:
        _ri_utilization_period(ce, call, day, min(day + step, end), records, attributes)
        day += step
    return records, attributes


def _ri_utilization_period(
    ce: Any,
    call: CountingCaller,
    start: date,
    end: date,
    records: list[UtilizationRecord],
    attributes: dict[str, dict[str, Any]],
) -> None:
    token = None
    while True:
        kwargs: dict[str, Any] = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "GroupBy": [{"Type": "DIMENSION", "Key": "SUBSCRIPTION_ID"}],
        }
        if token:
            kwargs["NextPageToken"] = token
        resp = call(
            "ce:GetReservationUtilization",
            ce.get_reservation_utilization,
            cache_key=kwargs,
            **kwargs,
        )
        for period in resp.get("UtilizationsByTime", []):
            for group in period.get("Groups", []):
                attrs = group.get("Attributes", {})
                rid = attrs.get("leaseId") or group.get("Value")
                if not rid:
                    continue
                attributes[rid] = attrs
                util = group.get("Utilization", {})
                purchased = _dec(util.get("PurchasedHours")) or Decimal(0)
                unused_hours = _dec(util.get("UnusedHours")) or Decimal(0)
                fee = _dec(util.get("TotalAmortizedFee")) or Decimal(0)
                unused_cost = fee * unused_hours / purchased if purchased else None
                records.append(
                    UtilizationRecord(
                        provider_commitment_id=rid,
                        date=start,
                        utilization_pct=_dec(util.get("UtilizationPercentage")) or Decimal(0),
                        unused_cost=unused_cost,
                        used_amount=fee - unused_cost if unused_cost is not None else None,
                        net_savings=_dec(util.get("NetRISavings")),
                    )
                )
        token = resp.get("NextPageToken")
        if not token:
            return


def ri_from_ce_attributes(rid: str, attrs: dict[str, Any]) -> CommitmentRecord | None:
    """Build a commitment from Cost Explorer reservation attributes (other accounts, DynamoDB)."""
    try:
        start_at = _dt(attrs["startDateTime"])
        end_at = _dt(attrs["endDateTime"])
    except (KeyError, ValueError):
        return None
    months = round((end_at - start_at).days / 30.4)
    instance_type = attrs.get("instanceType") or None
    return CommitmentRecord(
        kind=AWS_RI,
        provider_commitment_id=rid,
        owner_account_id=attrs.get("accountId"),
        start_at=start_at,
        end_at=end_at,
        term_months=12 if months <= 18 else 36,
        scope=attrs.get("scope"),
        service=attrs.get("service") or attrs.get("subscriptionType"),
        region=attrs.get("region"),
        instance_type=instance_type,
        instance_family=instance_family(instance_type),
        quantity=int(attrs["numberOfInstances"]) if attrs.get("numberOfInstances") else None,
        state=attrs.get("subscriptionStatus"),
        attributes={"source": "cost_explorer", **attrs},
    )


def sp_utilization(
    ce: Any, call: CountingCaller, start: date, end: date
) -> tuple[list[UtilizationRecord], dict[str, dict[str, Any]]]:
    """One GetSavingsPlansUtilizationDetails call per day (the API has no daily granularity)."""
    records: list[UtilizationRecord] = []
    attributes: dict[str, dict[str, Any]] = {}
    day = start
    while day < end:
        token = None
        while True:
            kwargs: dict[str, Any] = {
                "TimePeriod": {
                    "Start": day.isoformat(),
                    "End": (day + timedelta(days=1)).isoformat(),
                }
            }
            if token:
                kwargs["NextToken"] = token
            resp = call(
                "ce:GetSavingsPlansUtilizationDetails",
                ce.get_savings_plans_utilization_details,
                cache_key=kwargs,
                **kwargs,
            )
            for detail in resp.get("SavingsPlansUtilizationDetails", []):
                arn = detail["SavingsPlanArn"]
                attributes[arn] = detail.get("Attributes", {})
                util = detail.get("Utilization", {})
                records.append(
                    UtilizationRecord(
                        provider_commitment_id=arn,
                        date=day,
                        utilization_pct=_dec(util.get("UtilizationPercentage")) or Decimal(0),
                        unused_cost=_dec(util.get("UnusedCommitment")),
                        used_amount=_dec(util.get("UsedCommitment")),
                        net_savings=_dec(detail.get("Savings", {}).get("NetSavings")),
                    )
                )
            token = resp.get("NextToken")
            if not token:
                break
        day += timedelta(days=1)
    return records, attributes
