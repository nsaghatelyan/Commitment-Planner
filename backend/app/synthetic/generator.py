"""Synthetic tenants: hourly normalized usage with commitments applied, plus the commitments,
their daily utilization, matching prices and provider-style native recommendations.

Commitments are applied each hour the way the clouds do it: reservations first, then
savings plans (EC2 Instance SPs before Compute SPs on AWS), each SP covering the usage with the
highest discount first. Rows follow the conventions in app.usage.schema:
  - covered usage: billed_cost=0, effective_cost at the commitment rate, status Used;
  - a partly covered row is split into a covered row and an on-demand row;
  - unused commitment: one Unused row per commitment-hour;
  - purchase fees: charge_category=Purchase rows with billed_cost (upfront on the purchase
    hour; recurring fees as one row per day at 00:00 to keep volume down).
For each commitment-hour, Used + Unused effective_cost equals the amortized hourly cost.
"""

import math
import random
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa

from app.collectors import types as k
from app.collectors.aws.usage_types import RDS_ENGINES, REGION_PREFIXES
from app.collectors.types import (
    AccountInfo,
    CommitmentRecord,
    NativeRecommendation,
    UtilizationRecord,
)
from app.engine.pools import AWS_DATABASE_SP_SERVICES as DATABASE_SP_SERVICES
from app.engine.pools import AWS_RI_SERVICES
from app.pricing.keys import usage_sku_key
from app.pricing.types import PriceRecord
from app.synthetic import catalog as cat
from app.usage.normalize import instance_family
from app.usage.schema import (
    USAGE_SCHEMA,
    ChargeCategory,
    CommitmentStatus,
    CommitmentType,
    PricingCategory,
)

NAMESPACE = uuid.UUID("5b0f6a52-6f1e-4d55-9b52-1c3c6f0a7e11")
EPS = 1e-9
EC2 = "Amazon Elastic Compute Cloud - Compute"
RDS = "Amazon Relational Database Service"
CACHE = "Amazon ElastiCache"
FARGATE = "Amazon Elastic Container Service"
DYNAMODB = "Amazon DynamoDB"
DOCDB = "Amazon DocumentDB (with MongoDB compatibility)"
LAMBDA = "AWS Lambda"
SAGEMAKER = "Amazon SageMaker"
_PREFIX = {region: prefix for prefix, region in REGION_PREFIXES.items()}
_RDS_OPERATION = {engine: f"CreateDBInstance:{code}" for code, engine in RDS_ENGINES.items()}


def _usage_type(region: str, usage: str) -> str:
    """AWS usage type as billed: region-prefixed except in us-east-1."""
    return usage if region == "us-east-1" else f"{_PREFIX[region]}-{usage}"


VM = "Virtual Machines"


# --------------------------------------------------------------------------- model


@dataclass
class Account:
    provider: str
    id: str
    name: str
    is_payer: bool = False


@dataclass
class Resource:
    provider: str
    account: Account
    region: str
    service_name: str
    service_category: str
    resource_id: str
    resource_name: str
    od_hourly: float
    units: float  # normalized units per hour
    schedule: Callable[[datetime], bool]
    instance_type: str | None = None
    operating_system: str | None = None
    tenancy: str | None = None
    database_engine: str | None = None
    deployment_option: str | None = None
    # AWS billing usage type and operation (the price key for every service but EC2).
    usage_type: str | None = None
    operation: str | None = None
    spot: bool = False
    usage_unit: str = "Hrs"
    quantity: float = 1.0
    sp_eligible: bool = True
    # Optional per-hour multiplier on quantity, units and cost (throughput that varies).
    scale: Callable[[datetime], float] | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def family(self) -> str | None:
        return instance_family(self.instance_type)

    @property
    def sku_key(self) -> str | None:
        return usage_sku_key(
            self.provider,
            self.service_name,
            self.instance_type,
            self.operating_system,
            self.tenancy,
            self.database_engine,
            self.deployment_option,
            self.usage_unit,
            self.usage_type,
            self.operation,
        )


@dataclass
class SynthCommitment:
    record: CommitmentRecord
    provider: str
    ctype: CommitmentType
    amortized_hourly: float
    matches: Callable[[Resource], bool]
    # Reservations: capacity in normalized units. Savings plans: discount.
    capacity_units: float = 0.0
    discount: float = 0.0
    priority: int = 0  # lower applies first among savings plans

    def active(self, hour: datetime) -> bool:
        return self.record.start_at <= hour < self.record.end_at


@dataclass
class SyntheticConnection:
    provider: str
    name: str
    accounts: list[Account]
    agreement_type: str | None = None
    billing_scope: str | None = None
    payer_account_id: str | None = None


@dataclass
class SyntheticTenant:
    profile: str
    tenant_id: str
    name: str
    start: date
    days: int
    connections: list[SyntheticConnection]
    commitments: list[CommitmentRecord]
    utilization: list[UtilizationRecord]
    usage: pa.Table
    prices: list[PriceRecord]
    native_recommendations: list[NativeRecommendation] = field(default_factory=list)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days)

    def account_infos(self, provider: str) -> list[AccountInfo]:
        return [
            AccountInfo(a.id, a.name, a.is_payer)
            for c in self.connections
            if c.provider == provider
            for a in c.accounts
        ]


# --------------------------------------------------------------------------- schedules


def always(_: datetime) -> bool:
    return True


def business_hours(h: datetime) -> bool:
    return h.weekday() < 5 and 8 <= h.hour < 20


def nightly(start_hour: int, end_hour: int) -> Callable[[datetime], bool]:
    return lambda h: start_hour <= h.hour < end_hour


def autoscale(index: int, size: int, base: float, peak: float) -> Callable[[datetime], bool]:
    """Instance `index` of a fleet runs when diurnal demand needs more than `index` nodes."""

    def run(h: datetime) -> bool:
        weekday = 1.0 if h.weekday() < 5 else 0.6
        diurnal = 0.5 - 0.5 * math.cos((h.hour - 3) / 24 * 2 * math.pi)
        demand = size * (base + (peak - base) * diurnal * weekday)
        return index < demand

    return run


def window(start: datetime | None, end: datetime | None, inner=always):
    def run(h: datetime) -> bool:
        return (start is None or h >= start) and (end is None or h < end) and inner(h)

    return run


# --------------------------------------------------------------------------- builder


class Builder:
    def __init__(self, profile: str, start: date, days: int, seed: int) -> None:
        self.profile = profile
        self.tenant_id = str(uuid.uuid5(NAMESPACE, f"tenant:{profile}"))
        self.start = start
        self.days = days
        self.t0 = datetime(start.year, start.month, start.day, tzinfo=UTC)
        self.t1 = self.t0 + timedelta(days=days)
        self.rng = random.Random(seed)
        self.resources: list[Resource] = []
        self.commitments: list[SynthCommitment] = []
        self.connections: list[SyntheticConnection] = []
        self.counter = 0

    def at(self, day: int, hour: int = 0) -> datetime:
        """Hour `day` days after the window start (negative: before it)."""
        return self.t0 + timedelta(days=day, hours=hour)

    def before_end(self, days: int) -> datetime:
        return self.t1 - timedelta(days=days)

    def _rid(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{uuid.uuid5(NAMESPACE, f'{self.profile}:{self.counter}').hex[:17]}"

    def _schedules(self, n, schedule, sched_factory, start=None, end=None, stagger=None):
        out = []
        for i in range(n):
            sch = sched_factory(i) if sched_factory else (schedule or always)
            s = stagger(i) if stagger else start
            out.append(window(s, end, sch) if (s or end) else sch)
        return out

    # ---- AWS resources
    def ec2(
        self,
        acct,
        region,
        itype,
        n=1,
        *,
        os="Linux",
        schedule=None,
        spot=False,
        app="web",
        env="prod",
        sched_factory=None,
        start=None,
        end=None,
        stagger=None,
    ):
        vcpu, od = cat.AWS_EC2[itype]
        if os == "Windows":
            od += cat.AWS_WINDOWS_PER_VCPU * vcpu
        for i, sch in enumerate(self._schedules(n, schedule, sched_factory, start, end, stagger)):
            rid = self._rid("i-")
            self.resources.append(
                Resource(
                    provider="aws",
                    account=acct,
                    region=region,
                    service_name=EC2,
                    service_category="Compute",
                    resource_id=f"arn:aws:ec2:{region}:{acct.id}:instance/{rid}",
                    resource_name=f"{app}-{env}-{i + 1:02d}",
                    od_hourly=od,
                    units=cat.aws_size_factor(itype),
                    schedule=sch,
                    instance_type=itype,
                    operating_system=os,
                    tenancy="Shared",
                    spot=spot,
                    tags={"app": app, "env": env, "team": app.split("-")[0]},
                )
            )

    def rds(self, acct, region, klass, engine, deployment="Single-AZ", n=1, app="db"):
        od = cat.AWS_RDS[(klass, engine, deployment)]
        factor = cat.aws_size_factor(klass) * (2 if deployment == "Multi-AZ" else 1)
        for i in range(n):
            name = f"{app}-{i + 1:02d}"
            self.resources.append(
                Resource(
                    provider="aws",
                    account=acct,
                    region=region,
                    service_name=RDS,
                    service_category="Databases",
                    resource_id=f"arn:aws:rds:{region}:{acct.id}:db:{name}",
                    resource_name=name,
                    od_hourly=od,
                    units=factor,
                    schedule=always,
                    instance_type=klass,
                    database_engine=engine,
                    deployment_option=deployment,
                    usage_type=_usage_type(
                        region,
                        f"{'Multi-AZUsage' if deployment == 'Multi-AZ' else 'InstanceUsage'}:{klass}",
                    ),
                    operation=_RDS_OPERATION.get(engine, "CreateDBInstance:0002"),
                    sp_eligible=False,
                    tags={"app": app, "env": "prod"},
                )
            )

    def cache(self, acct, region, node_type, n, app="cache"):
        for i in range(n):
            name = f"{app}-{i + 1:03d}"
            self.resources.append(
                Resource(
                    provider="aws",
                    account=acct,
                    region=region,
                    service_name=CACHE,
                    service_category="Databases",
                    resource_id=f"arn:aws:elasticache:{region}:{acct.id}:cluster:{name}",
                    resource_name=name,
                    od_hourly=cat.AWS_CACHE[node_type],
                    units=cat.aws_size_factor(node_type),
                    schedule=always,
                    instance_type=node_type,
                    database_engine="Redis",
                    usage_type=_usage_type(region, f"NodeUsage:{node_type}"),
                    operation="CreateCacheCluster:0002",
                    sp_eligible=False,
                    tags={"app": app, "env": "prod"},
                )
            )

    def fargate(self, acct, region, vcpus, app="jobs", schedule=always):
        self.resources.append(
            Resource(
                provider="aws",
                account=acct,
                region=region,
                service_name=FARGATE,
                service_category="Compute",
                resource_id=f"arn:aws:ecs:{region}:{acct.id}:service/{app}",
                resource_name=app,
                od_hourly=cat.FARGATE_VCPU_HOUR * vcpus,
                units=vcpus,
                schedule=schedule,
                usage_unit="vCPU-Hours",
                quantity=vcpus,
                usage_type=_usage_type(region, "Fargate-vCPU-Hours:perCPU"),
                operation="FargateTask",
                tags={"app": app, "env": "prod"},
            )
        )

    def dynamodb(self, acct, region, rcu, wcu, app="table", scale=None):
        """Provisioned capacity: one resource per capacity type, priced per unit-hour."""
        for usage, units, price in (
            ("ReadCapacityUnit-Hrs", rcu, cat.DYNAMODB_RCU_HOUR),
            ("WriteCapacityUnit-Hrs", wcu, cat.DYNAMODB_WCU_HOUR),
        ):
            self.resources.append(
                Resource(
                    provider="aws",
                    account=acct,
                    region=region,
                    service_name=DYNAMODB,
                    service_category="Databases",
                    resource_id=f"arn:aws:dynamodb:{region}:{acct.id}:table/{app}",
                    resource_name=app,
                    od_hourly=units * price,
                    units=units,
                    schedule=always,
                    usage_unit=usage,
                    quantity=units,
                    usage_type=_usage_type(region, usage),
                    operation="CommittedThroughput",
                    sp_eligible=False,
                    scale=scale,
                    tags={"app": app, "env": "prod"},
                )
            )

    def docdb(self, acct, region, klass, n=1, app="docs", schedule=None, start=None):
        """DocumentDB has no reservations: only Database Savings Plans can cover it."""
        for i, sch in enumerate(self._schedules(n, schedule, None, start, None, None)):
            name = f"{app}-{i + 1:02d}"
            self.resources.append(
                Resource(
                    provider="aws",
                    account=acct,
                    region=region,
                    service_name=DOCDB,
                    service_category="Databases",
                    resource_id=f"arn:aws:rds:{region}:{acct.id}:db:{name}",
                    resource_name=name,
                    od_hourly=cat.AWS_DOCDB[klass],
                    units=cat.aws_size_factor(klass),
                    schedule=sch,
                    instance_type=klass,
                    usage_type=_usage_type(region, f"InstanceUsage:{klass}"),
                    operation="CreateDBInstance:0023",
                    sp_eligible=False,
                    tags={"app": app, "env": "prod"},
                )
            )

    def lambda_fn(self, acct, region, gb_seconds_per_hour, app="fn", scale=None):
        self.resources.append(
            Resource(
                provider="aws",
                account=acct,
                region=region,
                service_name=LAMBDA,
                service_category="Compute",
                resource_id=f"arn:aws:lambda:{region}:{acct.id}:function:{app}",
                resource_name=app,
                od_hourly=gb_seconds_per_hour * cat.LAMBDA_GB_SECOND,
                units=gb_seconds_per_hour,
                schedule=always,
                usage_unit="Lambda-GB-Second",
                quantity=gb_seconds_per_hour,
                usage_type=_usage_type(region, "Lambda-GB-Second"),
                operation="Invoke",
                scale=scale,
                tags={"app": app, "env": "prod"},
            )
        )

    def sagemaker(self, acct, region, itype, n=1, app="model"):
        for i in range(n):
            name = f"{app}-{i + 1:02d}"
            self.resources.append(
                Resource(
                    provider="aws",
                    account=acct,
                    region=region,
                    service_name=SAGEMAKER,
                    service_category="Machine Learning",
                    resource_id=f"arn:aws:sagemaker:{region}:{acct.id}:endpoint/{name}",
                    resource_name=name,
                    od_hourly=cat.AWS_SAGEMAKER[itype],
                    units=1,
                    schedule=always,
                    instance_type=itype,
                    usage_type=_usage_type(region, f"Host:{itype}"),
                    operation="RunInstance",
                    sp_eligible=False,
                    tags={"app": app, "env": "prod"},
                )
            )

    # ---- Azure resources
    def vm(
        self,
        sub,
        region,
        size,
        n=1,
        *,
        os="Linux",
        schedule=None,
        app="web",
        env="prod",
        spot=False,
        sched_factory=None,
        start=None,
        end=None,
        stagger=None,
    ):
        vcpu, od = cat.AZURE_VM[size]
        if os == "Windows":
            od += cat.AZURE_WINDOWS_PER_VCPU * vcpu
        for i, sch in enumerate(self._schedules(n, schedule, sched_factory, start, end, stagger)):
            name = f"vm-{app}-{env}-{i + 1:02d}"
            self.resources.append(
                Resource(
                    provider="azure",
                    account=sub,
                    region=region,
                    service_name=VM,
                    service_category="Compute",
                    resource_id=(
                        f"/subscriptions/{sub.id}/resourceGroups/rg-{app}-{env}/providers/"
                        f"Microsoft.Compute/virtualMachines/{name}"
                    ),
                    resource_name=name,
                    od_hourly=od,
                    units=vcpu / 2,
                    schedule=sch,
                    instance_type=size,
                    operating_system=os,
                    spot=spot,
                    usage_unit="1 Hour",
                    tags={"app": app, "env": env, "costcenter": f"cc-{app[:3]}"},
                )
            )

    def azure_service(self, sub, region, key, n=1, *, app="data", scale=None):
        spec = cat.AZURE_OTHER[key]
        for i in range(n):
            name = f"{app}-{i + 1:02d}"
            self.resources.append(
                Resource(
                    provider="azure",
                    account=sub,
                    region=region,
                    service_name=spec.service,
                    service_category="Databases"
                    if "App Service" not in spec.service
                    else "Compute",
                    resource_id=f"/subscriptions/{sub.id}/resourceGroups/rg-{app}/providers/"
                    f"{spec.service.replace(' ', '')}/{name}",
                    resource_name=name,
                    od_hourly=spec.od,
                    units=spec.units,
                    schedule=always,
                    instance_type=spec.sku,
                    usage_unit=spec.usage_unit,
                    quantity=spec.units if spec.usage_unit != "1 Hour" else 1,
                    sp_eligible=spec.sp_eligible,
                    scale=scale,
                    tags={"app": app, "env": "prod"},
                )
            )

    # ---- commitments
    def _record(self, kind, start, term, payment, *, owner=None, **kw):
        return CommitmentRecord(
            kind=kind,
            provider_commitment_id=kw.pop("cid"),
            owner_account_id=owner,
            start_at=start,
            end_at=start + timedelta(days=round(term / 12 * 365)),
            term_months=term,
            payment_option=payment,
            state="active",
            **kw,
        )

    @staticmethod
    def _split(total_hourly: float, term: int, payment: str) -> tuple[Decimal, Decimal]:
        """(upfront total, recurring hourly) for an amortized hourly cost."""
        hours = term * 730
        share = cat.UPFRONT_SHARE.get(payment, 0.0)
        upfront = Decimal(str(round(total_hourly * hours * share, 2)))
        recurring = Decimal(str(round(total_hourly * (1 - share), 6)))
        return upfront, recurring

    def _ends_in(self, start: datetime | None, ends_in_days: int | None, term: int) -> datetime:
        """Commitment start so it expires `ends_in_days` after the window end."""
        if ends_in_days is None:
            return start
        return self.t1 + timedelta(days=ends_in_days) - timedelta(days=round(term / 12 * 365))

    def reservation(
        self,
        provider,
        owner,
        service,
        region,
        sku,
        count,
        term,
        payment,
        start=None,
        *,
        ends_in_days=None,
        os="Linux",
        engine=None,
        deployment=None,
        flexible=True,
        offering_class="standard",
    ):
        """A reservation. Size-flexible ones match the family (normalized units)."""
        start = self._ends_in(start, ends_in_days, term)
        if provider == "aws":
            disc_kind = "ri"
            if service == RDS:
                od = cat.AWS_RDS[(sku, engine, deployment)]
                units_each = cat.aws_size_factor(sku) * (2 if deployment == "Multi-AZ" else 1)
                flexible = flexible and engine not in cat.AWS_RDS_EXACT_ENGINES
            elif service == CACHE:
                od, units_each = cat.AWS_CACHE[sku], cat.aws_size_factor(sku)
            else:
                vcpu, od = cat.AWS_EC2[sku]
                units_each = cat.aws_size_factor(sku)
                flexible = flexible and os == "Linux"
            kind = k.AWS_RI
        else:
            kind = k.AZURE_RI
            if service == VM:
                vcpu, od = cat.AZURE_VM[sku]
                units_each, disc_kind = vcpu / 2, "ri"
            else:
                spec = next(s for s in cat.AZURE_OTHER.values() if s.sku == sku)
                od, units_each, disc_kind = spec.od, spec.units, spec.ri_kind
                flexible = flexible and spec.service != "Azure App Service"
        disc = cat.discount(provider, disc_kind, term, payment)
        amortized = od * (1 - disc) * count
        upfront, recurring = self._split(amortized, term, payment)
        family = instance_family(sku)
        if provider == "aws":
            cid = self._rid("ri-")
        else:
            order = uuid.uuid5(NAMESPACE, self._rid("order"))
            cid = (
                f"/providers/microsoft.capacity/reservationorders/{order}/reservations/"
                f"{uuid.uuid5(NAMESPACE, str(order))}"
            )

        def matches(r: Resource) -> bool:
            if r.provider != provider or r.region != region or r.spot or r.service_name != service:
                return False
            if service == RDS and (r.database_engine, r.deployment_option) != (engine, deployment):
                return False
            if service in (EC2, VM) and r.operating_system != os:
                return False
            return r.family == family if flexible else r.instance_type == sku

        attrs = {
            "platform": engine or os,
            "deployment_option": deployment,
            "size_flexible": flexible,
            "offering_class": offering_class,
        }
        if provider == "azure":
            attrs["orderId"] = cid.split("/reservations/")[0]
        record = self._record(
            kind,
            start,
            term,
            payment,
            owner=owner.id,
            cid=cid,
            scope="Region" if provider == "aws" else "Shared",
            service=service,
            region=region,
            instance_type=sku,
            instance_family=family,
            quantity=count,
            upfront_cost=upfront,
            recurring_hourly_cost=recurring,
            attributes=attrs,
        )
        self.commitments.append(
            SynthCommitment(
                record,
                provider,
                CommitmentType.RESERVATION,
                amortized,
                matches,
                capacity_units=units_each * count,
            )
        )

    def aws_sp(
        self,
        owner,
        kind,
        hourly,
        term,
        payment,
        start=None,
        *,
        ends_in_days=None,
        region=None,
        family=None,
    ):
        start = self._ends_in(start, ends_in_days, term)
        cid = (
            f"arn:aws:savingsplans::{owner.id}:savingsplan/{uuid.uuid5(NAMESPACE, self._rid('sp'))}"
        )
        if kind == k.AWS_SP_EC2:
            disc = cat.discount("aws", "ec2_sp", term, payment)

            def matches(r: Resource) -> bool:
                return (
                    r.provider == "aws"
                    and not r.spot
                    and r.region == region
                    and r.family == family
                    and r.service_name == EC2
                )

            priority = 0
        else:
            disc = cat.discount("aws", "compute_sp", term, payment)

            def matches(r: Resource) -> bool:
                return r.provider == "aws" and not r.spot and r.sp_eligible

            priority = 1 if term == 36 else 2
        upfront, recurring = self._split(hourly, term, payment)
        record = self._record(
            kind,
            start,
            term,
            payment,
            owner=owner.id,
            cid=cid,
            scope="organization",
            service="EC2" if kind == k.AWS_SP_EC2 else "EC2, Fargate, Lambda",
            region=region,
            instance_family=family,
            hourly_commitment=Decimal(str(hourly)),
            upfront_cost=upfront,
            recurring_hourly_cost=recurring,
        )
        self.commitments.append(
            SynthCommitment(
                record,
                "aws",
                CommitmentType.SAVINGS_PLAN,
                hourly,
                matches,
                discount=disc,
                priority=priority,
            )
        )

    def azure_sp(self, billing_sub, hourly, term, start=None, *, ends_in_days=None):
        start = self._ends_in(start, ends_in_days, term)
        order = uuid.uuid5(NAMESPACE, self._rid("sporder"))
        cid = (
            f"/providers/microsoft.billingbenefits/savingsplanorders/{order}/savingsplans/"
            f"{uuid.uuid5(NAMESPACE, str(order))}"
        )
        record = self._record(
            k.AZURE_SP_COMPUTE,
            start,
            term,
            "monthly",
            owner=billing_sub.id,
            cid=cid,
            scope="Shared",
            service="Compute",
            hourly_commitment=Decimal(str(hourly)),
            upfront_cost=Decimal(0),
            recurring_hourly_cost=Decimal(str(hourly)),
            attributes={"orderId": cid.split("/savingsplans/")[0]},
        )
        self.commitments.append(
            SynthCommitment(
                record,
                "azure",
                CommitmentType.SAVINGS_PLAN,
                hourly,
                lambda r: r.provider == "azure" and not r.spot and r.sp_eligible,
                discount=cat.discount("azure", "sp", term),
            )
        )

    # ---- simulation
    def build(self) -> SyntheticTenant:
        cols: dict[str, list[Any]] = {name: [] for name in USAGE_SCHEMA.names}
        daily_used: dict[tuple[str, date], float] = defaultdict(float)
        daily_total: dict[tuple[str, date], float] = defaultdict(float)
        daily_unused: dict[tuple[str, date], float] = defaultdict(float)
        billing = {c.provider: c for c in self.connections}

        def emit(r: Resource | None, start: datetime, end: datetime, **values: Any) -> None:
            base: dict[str, Any] = {
                "charge_period_start": start,
                "charge_period_end": end,
                "charge_category": ChargeCategory.USAGE.value,
            }
            if r is not None:
                conn = billing[r.provider]
                base.update(
                    provider=r.provider,
                    billing_account_id=conn.payer_account_id,
                    sub_account_id=r.account.id,
                    sub_account_name=r.account.name,
                    region=r.region,
                    service_category=r.service_category,
                    service_name=r.service_name,
                    sku_id=r.sku_key,
                    resource_id=r.resource_id,
                    resource_name=r.resource_name,
                    instance_family=r.family,
                    instance_type=r.instance_type,
                    operating_system=r.operating_system,
                    tenancy=r.tenancy,
                    database_engine=r.database_engine,
                    deployment_option=r.deployment_option,
                    usage_type=r.usage_type,
                    operation=r.operation,
                    usage_unit=r.usage_unit,
                    tags=list(r.tags.items()),
                )
            base.update(values)
            for name in USAGE_SCHEMA.names:
                cols[name].append(base.get(name))

        ris = [c for c in self.commitments if c.ctype == CommitmentType.RESERVATION]
        sps = sorted(
            (c for c in self.commitments if c.ctype == CommitmentType.SAVINGS_PLAN),
            key=lambda c: c.priority,
        )
        hour = self.t0
        while hour < self.t1:
            end = hour + timedelta(hours=1)
            # [resource, remaining fraction, scale] for each running resource this hour
            active = [
                [r, 1.0, r.scale(hour) if r.scale else 1.0]
                for r in self.resources
                if r.schedule(hour)
            ]
            for c in ris:
                if not c.active(hour):
                    continue
                rate = c.amortized_hourly / c.capacity_units
                cap = c.capacity_units
                for item in active:
                    r, frac, mult = item
                    if cap <= EPS or frac <= EPS or not c.matches(r):
                        continue
                    take_units = min(cap, r.units * mult * frac)
                    f = take_units / (r.units * mult)
                    self._covered(emit, r, c, hour, end, f, mult, take_units * rate)
                    item[1] -= f
                    cap -= take_units
                self._close_hour(
                    emit,
                    c,
                    hour,
                    end,
                    cap * rate,
                    c.amortized_hourly - cap * rate,
                    daily_used,
                    daily_total,
                    daily_unused,
                )
            for c in sps:
                if not c.active(hour):
                    continue
                commit = c.amortized_hourly
                for item in active:
                    r, frac, mult = item
                    if commit <= EPS or frac <= EPS or not c.matches(r):
                        continue
                    need = r.od_hourly * mult * frac * (1 - c.discount)
                    take = min(commit, need)
                    f = frac * take / need
                    self._covered(emit, r, c, hour, end, f, mult, take)
                    item[1] -= f
                    commit -= take
                self._close_hour(
                    emit,
                    c,
                    hour,
                    end,
                    commit,
                    c.amortized_hourly - commit,
                    daily_used,
                    daily_total,
                    daily_unused,
                )
            for r, frac, mult in active:
                if frac <= EPS:
                    continue
                od = r.od_hourly * mult * frac
                cost = od * (1 - cat.SPOT_DISCOUNT) if r.spot else od
                emit(
                    r,
                    hour,
                    end,
                    pricing_category=(
                        PricingCategory.SPOT if r.spot else PricingCategory.ON_DEMAND
                    ).value,
                    usage_quantity=r.quantity * mult * frac,
                    normalized_units=r.units * mult * frac,
                    list_cost=od,
                    billed_cost=cost,
                    effective_cost=cost,
                    on_demand_equiv_cost=None if r.spot else od,
                )
            self._other_services(emit, hour, end)
            if hour.hour == 0:
                self._fees(emit, hour)
            hour = end

        usage = pa.Table.from_pydict(cols, schema=USAGE_SCHEMA)
        utilization = [
            UtilizationRecord(
                provider_commitment_id=cid,
                date=day,
                utilization_pct=Decimal(str(round(100 * daily_used[(cid, day)] / total, 4))),
                unused_cost=Decimal(str(round(daily_unused[(cid, day)], 6))),
                used_amount=Decimal(str(round(daily_used[(cid, day)], 6))),
            )
            for (cid, day), total in sorted(daily_total.items())
            if total > 0
        ]
        return SyntheticTenant(
            profile=self.profile,
            tenant_id=self.tenant_id,
            name=f"Synthetic {self.profile}",
            start=self.start,
            days=self.days,
            connections=self.connections,
            commitments=[c.record for c in self.commitments],
            utilization=utilization,
            usage=usage,
            prices=self._prices(),
            native_recommendations=self._native(usage),
        )

    @staticmethod
    def _covered(emit, r, c: SynthCommitment, start, end, frac, mult, effective) -> None:
        od = r.od_hourly * mult * frac
        emit(
            r,
            start,
            end,
            pricing_category=PricingCategory.COMMITMENT.value,
            commitment_id=c.record.provider_commitment_id,
            commitment_type=c.ctype.value,
            commitment_status=CommitmentStatus.USED.value,
            usage_quantity=r.quantity * mult * frac,
            normalized_units=r.units * mult * frac,
            list_cost=od,
            billed_cost=0.0,
            effective_cost=effective,
            on_demand_equiv_cost=od,
        )

    def _close_hour(self, emit, c, start, end, unused, used, d_used, d_total, d_unused) -> None:
        key = (c.record.provider_commitment_id, start.date())
        d_used[key] += used
        d_total[key] += c.amortized_hourly
        d_unused[key] += max(unused, 0.0)
        if unused > EPS:
            emit(
                None,
                start,
                end,
                provider=c.provider,
                billing_account_id=self._payer(c.provider),
                sub_account_id=c.record.owner_account_id,
                region=c.record.region,
                service_name=c.record.service,
                instance_type=c.record.instance_type,
                instance_family=c.record.instance_family,
                pricing_category=PricingCategory.COMMITMENT.value,
                commitment_id=c.record.provider_commitment_id,
                commitment_type=c.ctype.value,
                commitment_status=CommitmentStatus.UNUSED.value,
                billed_cost=0.0,
                effective_cost=unused,
            )

    def _payer(self, provider: str) -> str | None:
        return next(c.payer_account_id for c in self.connections if c.provider == provider)

    def _fees(self, emit, day_start: datetime) -> None:
        for c in self.commitments:
            rec = c.record
            common = {
                "provider": c.provider,
                "billing_account_id": self._payer(c.provider),
                "sub_account_id": rec.owner_account_id,
                "region": rec.region,
                "service_name": rec.service,
                "charge_category": ChargeCategory.PURCHASE.value,
                "pricing_category": PricingCategory.COMMITMENT.value,
                "commitment_id": rec.provider_commitment_id,
                "commitment_type": c.ctype.value,
                "effective_cost": 0.0,
            }
            if rec.upfront_cost and day_start <= rec.start_at < day_start + timedelta(days=1):
                emit(
                    None,
                    rec.start_at,
                    rec.start_at + timedelta(hours=1),
                    billed_cost=float(rec.upfront_cost),
                    **common,
                )
            if rec.recurring_hourly_cost and rec.start_at <= day_start < rec.end_at:
                emit(
                    None,
                    day_start,
                    day_start + timedelta(days=1),
                    billed_cost=float(rec.recurring_hourly_cost) * 24,
                    **common,
                )

    def _other_services(self, emit, start, end) -> None:
        """Non-commitment spend (storage, network, monitoring) so totals look realistic."""
        services = {
            "aws": [
                ("Amazon Simple Storage Service", "Storage", 0.9),
                ("AWS Data Transfer", "Networking", 0.35),
                ("AmazonCloudWatch", "Management and Governance", 0.12),
            ],
            "azure": [
                ("Storage", "Storage", 0.8),
                ("Bandwidth", "Networking", 0.3),
                ("Azure Monitor", "Management and Governance", 0.1),
            ],
        }
        for conn in self.connections:
            res = [r for r in self.resources if r.provider == conn.provider]
            scale = len(res) / 10 or 0.2
            regions = sorted({r.region for r in res}) or ["global"]
            for acct in conn.accounts:
                for name, category, base in services[conn.provider]:
                    cost = base * scale / len(conn.accounts) * (0.9 + 0.2 * self.rng.random())
                    emit(
                        None,
                        start,
                        end,
                        provider=conn.provider,
                        billing_account_id=conn.payer_account_id,
                        sub_account_id=acct.id,
                        sub_account_name=acct.name,
                        region=self.rng.choice(regions),
                        service_name=name,
                        service_category=category,
                        pricing_category=PricingCategory.ON_DEMAND.value,
                        usage_quantity=cost * 10,
                        usage_unit="Units",
                        list_cost=cost,
                        billed_cost=cost,
                        effective_cost=cost,
                        on_demand_equiv_cost=cost,
                    )

    # ---- prices
    def _prices(self) -> list[PriceRecord]:
        """Every price the profile's resources need: on-demand, RI and SP for each term and
        payment option (AWS), or term (Azure)."""
        out: list[PriceRecord] = []
        seen: set[tuple[str, str]] = set()

        def add(
            provider, service, key, region, model, od, disc, term=None, payment=None, unit="Hrs"
        ):
            price = od if model == "on_demand" else od * (1 - disc)
            attrs = {}
            if payment in cat.UPFRONT_SHARE and term:
                share = cat.UPFRONT_SHARE[payment]
                attrs = {
                    "upfront": str(round(price * term * 730 * share, 4)),
                    "hourly": str(round(price * (1 - share), 8)),
                }
            out.append(
                PriceRecord(
                    provider,
                    service,
                    key,
                    region,
                    model,
                    unit,
                    Decimal(str(round(price, 8))),
                    self.start,
                    term_months=term,
                    payment_option=payment,
                    attributes=attrs,
                )
            )

        aws_payments = ("no_upfront", "partial_upfront", "all_upfront")
        for r in self.resources:
            key = r.sku_key
            if key is None or (key, r.region) in seen:
                continue
            seen.add((key, r.region))
            od = r.od_hourly
            if r.provider == "aws":
                service = key.split("|", 1)[0]

                def offer(model, kind, term, pay, service=service, key=key, r=r, od=od):
                    disc = cat.discount("aws", kind, term, pay)
                    add("aws", service, key, r.region, model, od, disc, term, pay)

                add("aws", service, key, r.region, "on_demand", od, 0)
                # What AWS sells for each service (see app.engine.pools).
                if service in DATABASE_SP_SERVICES:
                    offer("sp_database", "db_sp", 12, "no_upfront")
                if service == "dynamodb":
                    offer("ri", "dynamodb_ri", 12, "partial_upfront")
                    continue
                for term in (12, 36):
                    for pay in aws_payments:
                        if service == "fargate":
                            offer("sp", "fargate_sp", term, pay)
                        elif service == "lambda":
                            offer("sp", "lambda_sp", term, pay)
                        elif service == "sagemaker":
                            offer("sp_sagemaker", "sagemaker_sp", term, pay)
                        elif service == "ec2":
                            offer("sp", "compute_sp", term, pay)
                            offer("sp_instance", "ec2_sp", term, pay)
                            if r.operating_system == "Linux":  # Windows EC2: SP-only here
                                offer("ri", "ri", term, pay)
                        elif service in AWS_RI_SERVICES:
                            offer("ri", "ri", term, pay)
            else:
                unit = r.usage_unit
                per_unit = od / (r.quantity or 1) if unit != "1 Hour" else od
                add("azure", r.service_name, key, r.region, "on_demand", per_unit, 0, unit=unit)
                if r.service_name == VM:
                    ri_kind, sp_ok = "ri", True
                    ri_ok = r.operating_system == "Linux"
                else:
                    spec = next(s for s in cat.AZURE_OTHER.values() if s.sku == r.instance_type)
                    ri_kind, sp_ok, ri_ok = spec.ri_kind, spec.sp_eligible, True
                for term in (12, 36):
                    if ri_ok:
                        add(
                            "azure",
                            r.service_name,
                            key,
                            r.region,
                            "ri",
                            per_unit,
                            cat.discount("azure", ri_kind, term),
                            term,
                            unit=unit,
                        )
                    if sp_ok:
                        add(
                            "azure",
                            r.service_name,
                            key,
                            r.region,
                            "sp",
                            per_unit,
                            cat.discount("azure", "sp", term),
                            term,
                            unit=unit,
                        )
        return out

    # ---- native recommendations
    def _native(self, usage: pa.Table) -> list[NativeRecommendation]:
        """What the providers' own tools would say: 30-day averages of on-demand usage,
        with no view of seasonality, migrations or how RIs and SPs interact."""
        import duckdb

        con = duckdb.connect()
        con.execute("SET TimeZone = 'UTC'")
        con.register("u", usage)
        since = self.t1 - timedelta(days=30)
        hours = 30 * 24
        out: list[NativeRecommendation] = []
        sp_rows = con.execute(
            """SELECT provider, sum(on_demand_equiv_cost) FROM u
               WHERE pricing_category = 'On-Demand' AND charge_category = 'Usage'
                 AND service_name IN (?, ?, ?, ?) AND charge_period_start >= ?
               GROUP BY 1""",
            [EC2, FARGATE, VM, "Azure App Service", since],
        ).fetchall()
        for provider, od in sp_rows:
            disc = cat.discount(provider, "compute_sp" if provider == "aws" else "sp", 12)
            hourly = od / hours * (1 - disc)
            out.append(
                NativeRecommendation(
                    provider=provider,
                    kind=k.AWS_SP_COMPUTE if provider == "aws" else k.AZURE_SP_COMPUTE,
                    term_months=12,
                    payment_option="no_upfront" if provider == "aws" else None,
                    lookback_days=30,
                    hourly_commitment=Decimal(str(round(hourly, 3))),
                    estimated_monthly_savings=Decimal(str(round(od / 30 * 30.4 * disc * 0.9, 2))),
                    scope="organization" if provider == "aws" else "Shared",
                )
            )
        ri_rows = con.execute(
            """SELECT provider, service_name, region, instance_type,
                      sum(usage_quantity) / ? AS avg_qty, sum(on_demand_equiv_cost) AS od
               FROM u WHERE pricing_category = 'On-Demand' AND charge_category = 'Usage'
                 AND service_name IN (?, ?, ?) AND charge_period_start >= ?
               GROUP BY ALL HAVING avg_qty >= 1""",
            [hours, RDS, CACHE, VM, since],
        ).fetchall()
        for provider, service, region, itype, qty, od in ri_rows:
            disc = cat.discount(provider, "ri", 12)
            out.append(
                NativeRecommendation(
                    provider=provider,
                    kind=k.AWS_RI if provider == "aws" else k.AZURE_RI,
                    term_months=12,
                    payment_option="no_upfront" if provider == "aws" else None,
                    lookback_days=30,
                    region=region,
                    instance_type=itype,
                    instance_family=instance_family(itype),
                    quantity=Decimal(int(qty)),
                    estimated_monthly_savings=Decimal(str(round(od / 30 * 30.4 * disc * 0.9, 2))),
                    scope="Shared",
                    raw={"service": service},
                )
            )
        return out


# --------------------------------------------------------------------------- profiles


def _aws_accounts(b: Builder, names: list[str]) -> list[Account]:
    base = int(uuid.uuid5(NAMESPACE, b.profile).int % 10**11)
    return [
        Account("aws", f"{(base + i * 7919) % 10**12:012d}", n, is_payer=i == 0)
        for i, n in enumerate(names)
    ]


def _azure_subs(b: Builder, names: list[str]) -> list[Account]:
    return [Account("azure", str(uuid.uuid5(NAMESPACE, f"{b.profile}:sub:{n}")), n) for n in names]


def _small(b: Builder) -> None:
    """AWS only. An expiring Compute SP (urgent), flat unreserved RDS Postgres Multi-AZ and
    ElastiCache, business-hours batch."""
    (payer,) = accts = _aws_accounts(b, ["small-prod"])
    b.connections.append(SyntheticConnection("aws", "AWS", accts, payer_account_id=payer.id))
    b.ec2(payer, "us-east-1", "m5.large", 6, app="web")
    b.ec2(payer, "us-east-1", "c5.xlarge", 2, schedule=business_hours, app="batch")
    b.ec2(payer, "us-east-1", "t3.medium", 2, app="tools", env="dev")
    b.rds(payer, "us-east-1", "db.m5.large", "MySQL")
    b.rds(payer, "us-east-1", "db.r6g.xlarge", "PostgreSQL", "Multi-AZ", app="pg")
    b.cache(payer, "us-east-1", "cache.r6g.large", 3, app="sessions")
    b.aws_sp(payer, k.AWS_SP_COMPUTE, 0.30, 12, "no_upfront", ends_in_days=10)


def _medium(b: Builder) -> None:
    """Azure EA. 20 VMs vs 15 reserved, a ramping workload, a stranded D4s_v4 RI after a move
    to D4as_v5, Cosmos with peaks, flat SQL vCores, App Service and Postgres."""
    subs = _azure_subs(b, ["prod", "staging", "shared-services"])
    enrollment = "84251234"
    b.connections.append(
        SyntheticConnection(
            "azure",
            "Azure EA",
            subs,
            agreement_type="ea",
            billing_scope=f"/providers/Microsoft.Billing/billingAccounts/{enrollment}",
            payer_account_id=enrollment,
        )
    )
    prod, staging, shared = subs
    b.vm(prod, "eastus", "Standard_D4s_v5", 20, app="api")
    b.reservation("azure", prod, VM, "eastus", "Standard_D4s_v5", 15, 36, "all_upfront", b.at(-400))
    # Ramping 5 -> 20 over the last 60 days.
    ramp_start = b.before_end(60)
    b.vm(
        prod,
        "westeurope",
        "Standard_E4s_v5",
        20,
        app="ingest",
        stagger=lambda i: None if i < 5 else ramp_start + timedelta(days=4 * (i - 5)),
    )
    # Migration: D4s_v4 retired on day 60, replaced by D4as_v5; the v4 RI is stranded.
    switch = b.at(60)
    b.vm(prod, "eastus2", "Standard_D4s_v4", 10, app="svc", end=switch)
    b.vm(prod, "eastus2", "Standard_D4as_v5", 10, app="svc", start=switch)
    b.reservation("azure", prod, VM, "eastus2", "Standard_D4s_v4", 10, 12, "monthly", b.at(-30))

    def cosmos_load(h: datetime) -> float:
        # 10k RU/s floor, up to 40k RU/s in weekday business hours.
        peak = h.weekday() < 5 and 9 <= h.hour < 18
        return 1.0 + (3.0 * math.sin((h.hour - 9) / 9 * math.pi) if peak else 0.0)

    b.azure_service(
        shared, "eastus", "cosmos", 1, app="catalog-db", scale=lambda h: 100 * cosmos_load(h)
    )
    b.azure_service(prod, "eastus", "sql_gp", 16, app="sql-gp")
    b.azure_service(prod, "eastus", "sql_bc", 8, app="sql-bc")
    b.azure_service(prod, "eastus", "app_p1v3", 4, app="web-app")
    b.azure_service(prod, "eastus", "pg_d4ds", 2, app="pg")
    b.vm(prod, "eastus", "Standard_D4s_v3", 3, os="Windows", app="erp")
    b.vm(staging, "eastus", "Standard_D2s_v3", 4, schedule=business_hours, app="api", env="stg")
    b.vm(shared, "eastus", "Standard_B2s", 3, app="jump")


def _large(b: Builder) -> None:
    """AWS org + Azure MCA with layered SPs and RIs, a 50x m5 RI stranded by a move to m7i,
    a growing workload, staging, nightly batch, Spot, a brand-new workload, Windows, SQL Server
    RDS, an ElastiCache cluster with 6 nodes and 4 expiring reserved nodes."""
    accts = _aws_accounts(b, ["payer", "prod", "data", "staging", "legacy"])
    payer, prod, data, staging, legacy = accts
    b.connections.append(SyntheticConnection("aws", "AWS org", accts, payer_account_id=payer.id))
    use1, usw2 = "us-east-1", "us-west-2"
    # Steady prod fleets, partly covered by an m6i RI and an EC2 Instance SP.
    b.ec2(prod, use1, "m6i.xlarge", 20, app="web")
    b.ec2(prod, use1, "m6i.2xlarge", 6, app="api")
    b.ec2(prod, usw2, "m6i.large", 8, app="web-west")
    b.ec2(prod, use1, "m6i.xlarge", 4, os="Windows", app="win-iis")
    b.ec2(data, use1, "r5.xlarge", 10, app="analytics")
    b.reservation("aws", prod, EC2, use1, "m6i.xlarge", 12, 36, "all_upfront", b.at(-500))
    b.aws_sp(payer, k.AWS_SP_EC2, 1.2, 36, "no_upfront", b.at(-300), region=use1, family="m6i")
    # Growing workload: floor 40 -> 48 -> 56 normalized units, plus daytime autoscaling.
    b.ec2(
        prod,
        use1,
        "c6i.xlarge",
        7,
        app="search",
        stagger=lambda i: {5: b.at(120), 6: b.at(150)}.get(i),
    )
    b.ec2(
        prod,
        use1,
        "c6i.xlarge",
        3,
        app="search-burst",
        sched_factory=lambda i: autoscale(i, 3, 0.0, 1.0),
    )
    # Things that must not drive commitments.
    b.ec2(staging, use1, "t3.large", 8, schedule=business_hours, app="web", env="stg")
    b.ec2(data, use1, "c7i.2xlarge", 10, schedule=nightly(1, 5), app="etl")
    b.ec2(
        data,
        use1,
        "c5.2xlarge",
        16,
        spot=True,
        app="spark",
        sched_factory=lambda i: autoscale(i, 16, 0.2, 1.0),
    )
    b.ec2(prod, use1, "m7g.large", 6, app="new-svc", start=b.before_end(7))
    # Compute SP candidates.
    b.ec2(
        prod,
        use1,
        "c5.xlarge",
        12,
        app="workers",
        sched_factory=lambda i: autoscale(i, 12, 0.35, 1.0),
    )
    b.fargate(prod, use1, 32, app="jobs")
    b.aws_sp(payer, k.AWS_SP_COMPUTE, 1.5, 12, "no_upfront", ends_in_days=65)
    b.aws_sp(payer, k.AWS_SP_COMPUTE, 0.8, 12, "all_upfront", b.at(150, 10))
    # Migration: 50x m5.xlarge retired on day 120, replaced by m7i.xlarge.
    switch = b.at(120)
    b.ec2(legacy, use1, "m5.xlarge", 50, app="legacy", end=switch)
    b.ec2(legacy, use1, "m7i.xlarge", 50, app="legacy", start=switch)
    b.reservation(
        "aws",
        legacy,
        EC2,
        use1,
        "m5.xlarge",
        50,
        36,
        "no_upfront",
        b.at(-700),
        offering_class="convertible",
    )
    # Databases: SQL Server needs exact-type RIs; Postgres Multi-AZ unreserved.
    b.rds(data, use1, "db.r5.xlarge", "SQL Server SE", "Multi-AZ", n=3, app="mssql")
    b.reservation(
        "aws",
        data,
        RDS,
        use1,
        "db.r5.xlarge",
        2,
        12,
        "all_upfront",
        b.at(-150),
        engine="SQL Server SE",
        deployment="Multi-AZ",
    )
    b.rds(data, use1, "db.r5.xlarge", "PostgreSQL", "Multi-AZ", n=2, app="pg")
    # ElastiCache: 6 nodes, 4 reserved, reservation expiring in 40 days.
    b.cache(prod, use1, "cache.r6g.large", 6, app="redis")
    b.reservation("aws", prod, CACHE, use1, "cache.r6g.large", 4, 12, "no_upfront", ends_in_days=40)

    subs = _azure_subs(b, ["corp-prod", "corp-dev", "data-platform", "sap"])
    acct, profile = "7d3c9a1e-0000-4c1b-8a5e-1a2b3c4d5e6f:abcd1234_2019-05-31", "PF12-ABCD-XY3-ZZZ"
    b.connections.append(
        SyntheticConnection(
            "azure",
            "Azure MCA",
            subs,
            agreement_type="mca",
            billing_scope=(
                f"/providers/Microsoft.Billing/billingAccounts/{acct}/billingProfiles/{profile}"
            ),
            payer_account_id=acct,
        )
    )
    cprod, cdev, dplat, sap = subs
    b.vm(cprod, "eastus2", "Standard_D8s_v3", 10, app="portal")
    b.vm(cprod, "eastus2", "Standard_D4s_v5", 12, app="svc")
    b.vm(cprod, "eastus2", "Standard_D4s_v3", 6, os="Windows", app="dotnet")
    b.vm(dplat, "westeurope", "Standard_E8s_v3", 6, app="spark")
    b.vm(
        dplat,
        "westeurope",
        "Standard_F4s_v2",
        10,
        spot=True,
        app="batch",
        sched_factory=lambda i: autoscale(i, 10, 0.1, 1.0),
    )
    b.vm(sap, "westeurope", "Standard_E8s_v3", 4, app="sap")
    b.vm(cdev, "eastus2", "Standard_D2s_v3", 10, schedule=business_hours, app="dev", env="dev")
    b.reservation(
        "azure", cprod, VM, "eastus2", "Standard_D8s_v3", 8, 36, "all_upfront", b.at(-365)
    )
    b.reservation("azure", sap, VM, "westeurope", "Standard_E8s_v3", 4, 36, "monthly", b.at(-600))
    b.azure_sp(cprod, 1.2, 12, b.at(-100))


def _startup(b: Builder) -> None:
    """21 days of history, no commitments."""
    (aws,) = accts = _aws_accounts(b, ["startup"])
    b.connections.append(SyntheticConnection("aws", "AWS", accts, payer_account_id=aws.id))
    b.ec2(aws, "us-east-1", "t3.medium", 3, app="api")
    b.ec2(aws, "us-east-1", "m6i.large", 2, start=b.at(10), app="api")
    b.rds(aws, "us-east-1", "db.r6g.xlarge", "PostgreSQL", "Multi-AZ", app="pg")
    subs = _azure_subs(b, ["startup-payg"])
    b.connections.append(
        SyntheticConnection(
            "azure",
            "Azure PAYG",
            subs,
            agreement_type="payg",
            billing_scope=f"/subscriptions/{subs[0].id}",
            payer_account_id=subs[0].id,
        )
    )
    b.vm(subs[0], "eastus", "Standard_B2s", 2, app="ml")
    b.vm(subs[0], "eastus", "Standard_D4s_v5", 2, start=b.at(7), app="ml")


def _data(b: Builder) -> None:
    """Databases and serverless/ML compute, no commitments: one of each commitment type the
    engine sizes beyond EC2 (RDS, ElastiCache and DynamoDB reservations, a Database Savings
    Plan for DocumentDB, which has no reservations, Compute SP for Lambda, SageMaker SP)."""
    (aws,) = accts = _aws_accounts(b, ["data"])
    b.connections.append(SyntheticConnection("aws", "AWS", accts, payer_account_id=aws.id))
    b.rds(aws, "us-east-1", "db.m5.large", "MySQL", app="orders")
    b.cache(aws, "us-east-1", "cache.r6g.large", 2, app="sessions")
    b.dynamodb(aws, "us-east-1", rcu=2000, wcu=500, app="events")
    b.docdb(aws, "us-east-1", "db.r6g.large", 2, app="catalog")
    b.lambda_fn(aws, "us-east-1", 200_000, app="ingest")
    b.sagemaker(aws, "us-east-1", "ml.m5.xlarge", 2, app="ranker")


# profile -> (builder, default days of history)
PROFILES: dict[str, tuple[Callable[[Builder], None], int]] = {
    "small": (_small, 90),
    "medium": (_medium, 120),
    "large": (_large, 180),
    "startup": (_startup, 21),
    "data": (_data, 120),
}
DEFAULT_END = date(2026, 10, 1)


def generate(
    profile: str, *, end: date | None = None, days: int | None = None, seed: int = 7
) -> SyntheticTenant:
    """Build one tenant whose history ends the day before `end` (default: 2026-10-01)."""
    fn, default_days = PROFILES[profile]
    days = days or default_days
    end = end or DEFAULT_END
    b = Builder(profile, end - timedelta(days=days), days, seed)
    fn(b)
    return b.build()
