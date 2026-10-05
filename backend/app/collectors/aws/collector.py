from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import boto3
import pyarrow as pa
from botocore.exceptions import ClientError

from app.collectors.aws import commitments as cm
from app.collectors.aws.cost_explorer import CostExplorerBootstrap
from app.collectors.aws.export import read_export
from app.collectors.aws.recommendations import (
    reservation_recommendations,
    savings_plan_recommendations,
)
from app.collectors.aws.session import assumed_role_session
from app.collectors.base import Collector
from app.collectors.cache import ApiCache, CountingCaller
from app.collectors.types import (
    AWS_RI,
    AccountInfo,
    CallStats,
    CommitmentRecord,
    NativeRecommendation,
    UtilizationRecord,
)
from app.timeutil import utc_today

ClientFactory = Callable[[str, str | None], Any]


class AwsCollector(Collector):
    """Reads a client's AWS org, either through their read-only role (assumed with the
    ExternalId) or, for local testing, straight from an AWS CLI profile on this machine."""

    provider = "aws"

    def __init__(
        self,
        role_arn: str | None,
        external_id: str | None,
        *,
        profile: str | None = None,
        export_bucket: str | None = None,
        export_prefix: str | None = None,
        region: str = "us-east-1",
        session_name: str = "savings-tool",
        cache: ApiCache | None = None,
        client_factory: ClientFactory | None = None,
        rec_terms: tuple[str, ...] = ("ONE_YEAR", "THREE_YEARS"),
        rec_payments: tuple[str, ...] = ("NO_UPFRONT", "ALL_UPFRONT"),
    ) -> None:
        self.role_arn = role_arn
        self.external_id = external_id
        self.profile = profile
        self.warnings: list[str] = []
        self._has_savings_plans = True
        self._has_reservations = True
        self.export_bucket = export_bucket
        self.export_prefix = export_prefix or ""
        self.region = region
        self.session_name = session_name
        self.stats = CallStats()
        self.call = CountingCaller(self.stats, cache)
        self.rec_terms = list(rec_terms)
        self.rec_payments = list(rec_payments)
        self._client_factory = client_factory
        self._session: boto3.Session | None = None
        self._clients: dict[tuple[str, str | None], Any] = {}
        self._account_id: str | None = None

    def client(self, service: str, region: str | None = None) -> Any:
        key = (service, region)
        if key not in self._clients:
            if self._client_factory:
                self._clients[key] = self._client_factory(service, region)
            else:
                if self._session is None:
                    if self.role_arn:
                        self.call.stats.record("sts:AssumeRole")
                        self._session = assumed_role_session(
                            self.role_arn,
                            self.external_id or "",
                            self.session_name,
                            self.region,
                            base_session=boto3.Session(profile_name=self.profile or None),
                        )
                    else:
                        self._session = boto3.Session(
                            profile_name=self.profile or None, region_name=self.region
                        )
                self._clients[key] = self._session.client(
                    service, region_name=region or self.region
                )
        return self._clients[key]

    @property
    def account_id(self) -> str:
        if self._account_id is None:
            ident = self.call("sts:GetCallerIdentity", self.client("sts").get_caller_identity)
            self._account_id = ident["Account"]
        return self._account_id

    def identity(self) -> dict[str, str]:
        ident = self.call("sts:GetCallerIdentity", self.client("sts").get_caller_identity)
        self._account_id = ident["Account"]
        return {"account": ident["Account"], "arn": ident["Arn"]}

    def test_connection(self) -> None:
        self.identity()
        end = utc_today()
        self.call(
            "ce:GetCostAndUsage",
            self.client("ce").get_cost_and_usage,
            TimePeriod={
                "Start": (end - timedelta(days=1)).isoformat(),
                "End": end.isoformat(),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )

    def list_accounts(self) -> list[AccountInfo]:
        org = self.client("organizations")
        try:
            desc = self.call("organizations:DescribeOrganization", org.describe_organization)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("AWSOrganizationsNotInUseException", "AccessDeniedException"):
                return [AccountInfo(self.account_id, is_payer=True)]
            raise
        payer = desc["Organization"]["MasterAccountId"]
        accounts = []
        token = None
        while True:
            kwargs = {"NextToken": token} if token else {}
            resp = self.call("organizations:ListAccounts", org.list_accounts, **kwargs)
            for acct in resp.get("Accounts", []):
                accounts.append(AccountInfo(acct["Id"], acct.get("Name"), acct["Id"] == payer))
            token = resp.get("NextToken")
            if not token:
                return accounts

    def export_usage(self, since: date | None) -> Iterator[pa.Table]:
        if not self.export_bucket:
            return iter(())
        return read_export(
            self.client("s3"), self.call, self.export_bucket, self.export_prefix, since
        )

    def bootstrap_usage(self, start: date, end: date) -> Iterator[pa.Table]:
        table = CostExplorerBootstrap(self.client("ce"), self.call, self.account_id).collect(
            start, end
        )
        if table.num_rows:
            yield table

    def regions(self) -> list[str]:
        resp = self.call("ec2:DescribeRegions", self.client("ec2").describe_regions)
        return sorted(r["RegionName"] for r in resp.get("Regions", []))

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _try(self, label: str, fn, default):
        from botocore.exceptions import BotoCoreError

        from app.collectors.aws.errors import explain

        try:
            return fn()
        except (ClientError, BotoCoreError) as exc:
            self._warn(f"{label}: {explain(exc)}")
            return default

    def collect_commitments(self) -> list[CommitmentRecord]:
        records = self._try(
            "Savings Plans", lambda: cm.savings_plans(self.client("savingsplans"), self.call), []
        )
        self._has_savings_plans = bool(records)
        regions = self._try("EC2 regions", self.regions, [self.region])
        records += cm.reserved_instances(
            self.client, self.call, regions, self.account_id, warn=self._warn
        )
        # Reservations owned by member accounts (and DynamoDB reserved capacity) are only
        # visible org-wide through Cost Explorer.
        end = utc_today()
        _, ri_attrs = self._try(
            "Reservation utilization",
            lambda: cm.ri_utilization(
                self.client("ce"), self.call, end - timedelta(days=7), end, daily=False
            ),
            ([], {}),
        )
        known = {r.provider_commitment_id for r in records}
        known |= {r.provider_commitment_id.rsplit(":", 1)[-1] for r in records}
        for rid, attrs in ri_attrs.items():
            if rid not in known:
                record = cm.ri_from_ce_attributes(rid, attrs)
                if record:
                    records.append(record)
        self._has_reservations = any(r.kind == AWS_RI for r in records)
        return records

    def collect_utilization(self, start: date, end: date) -> list[UtilizationRecord]:
        ce = self.client("ce")
        ri: list[UtilizationRecord] = []
        if self._has_reservations:
            # One billed call per day; skip it when there is nothing to measure.
            ri, _ = self._try(
                "Reservation utilization",
                lambda: cm.ri_utilization(ce, self.call, start, end),
                ([], {}),
            )
        if not self._has_savings_plans:
            # One billed call per day; skip it when there is nothing to measure.
            return ri
        sp, _ = self._try(
            "Savings Plans utilization",
            lambda: cm.sp_utilization(ce, self.call, start, end),
            ([], {}),
        )
        return ri + sp

    def collect_native_recommendations(self) -> list[NativeRecommendation]:
        ce = self.client("ce")
        sp = self._try(
            "Savings Plans recommendations",
            lambda: savings_plan_recommendations(ce, self.call, self.rec_terms, self.rec_payments),
            [],
        )
        ri = self._try(
            "Reservation recommendations",
            lambda: reservation_recommendations(ce, self.call, self.rec_terms, self.rec_payments),
            [],
        )
        return sp + ri
