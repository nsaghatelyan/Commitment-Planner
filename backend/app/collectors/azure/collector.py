from collections.abc import Iterator
from datetime import date, timedelta

import pyarrow as pa

from app.collectors.azure import commitments as cm
from app.collectors.azure import recommendations as recs
from app.collectors.azure.export import BlobSource, read_export
from app.collectors.azure.http import AzureHttp
from app.collectors.azure.query import QueryBootstrap
from app.collectors.azure.scopes import scope_level_utilization, validate_scope
from app.collectors.base import Collector
from app.collectors.cache import ApiCache, CountingCaller
from app.collectors.types import (
    AccountInfo,
    CallStats,
    CommitmentRecord,
    NativeRecommendation,
    UtilizationRecord,
)
from app.pricing.azure import MeterCatalog
from app.timeutil import utc_today


class AzureCollector(Collector):
    """Reads a client's Azure billing scope through our multi-tenant Entra app."""

    provider = "azure"

    def __init__(
        self,
        http: AzureHttp,
        *,
        agreement_type: str,
        billing_scope: str,
        export_source: BlobSource | None = None,
        export_prefix: str = "",
        cache: ApiCache | None = None,
        rec_terms: tuple[str, ...] = ("P1Y", "P3Y"),
    ) -> None:
        self.http = http
        self.agreement_type = agreement_type.lower()
        self.scope = validate_scope(self.agreement_type, billing_scope)
        self.export_source = export_source
        self.export_prefix = export_prefix
        self.stats = CallStats()
        self.call = CountingCaller(self.stats, cache)
        self.rec_terms = list(rec_terms)
        self.meters = MeterCatalog(http, self.call)
        self._commitments: list[CommitmentRecord] | None = None

    @property
    def billing_account_id(self) -> str:
        parts = self.scope.split("/")
        if self.agreement_type == "payg":
            return parts[2]
        return parts[4]

    def test_connection(self) -> None:
        day = utc_today() - timedelta(days=1)
        QueryBootstrap(self.http, self.call, self.scope, None).query("ActualCost", day, day, [])

    def list_accounts(self) -> list[AccountInfo]:
        return [
            AccountInfo(sub["subscriptionId"], sub.get("displayName"))
            for sub in self.http.paged(
                "/subscriptions",
                {"api-version": "2022-12-01"},
                self.call,
                "azure:ListSubscriptions",
            )
        ]

    def export_usage(self, since: date | None) -> Iterator[pa.Table]:
        if self.export_source is None:
            return iter(())
        return read_export(self.export_source, self.call, self.export_prefix, since)

    def bootstrap_usage(self, start: date, end: date) -> Iterator[pa.Table]:
        return QueryBootstrap(
            self.http, self.call, self.scope, self.meters, self.billing_account_id
        ).collect(start, end)

    def collect_commitments(self) -> list[CommitmentRecord]:
        if self._commitments is None:
            self._commitments = cm.reservations(self.http, self.call) + cm.savings_plans(
                self.http, self.call
            )
        return self._commitments

    def collect_utilization(self, start: date, end: date) -> list[UtilizationRecord]:
        if scope_level_utilization(self.agreement_type):
            scopes = [self.scope]
        else:
            orders = {c.attributes["orderId"] for c in self.collect_commitments()}
            scopes = sorted(orders)
        records = cm.utilization(self.http, self.call, scopes, start, end)
        # Map bare benefit ids back to the full ARM ids stored on commitments.
        by_key = {
            cm.commitment_key(c.provider_commitment_id): c for c in self.collect_commitments()
        }
        for r in records:
            if r.provider_commitment_id in by_key:
                r.provider_commitment_id = by_key[r.provider_commitment_id].provider_commitment_id
        return records

    def collect_native_recommendations(self) -> list[NativeRecommendation]:
        return recs.savings_plan_recommendations(
            self.http, self.call, self.scope, self.rec_terms
        ) + recs.reservation_recommendations(self.http, self.call, self.scope, self.rec_terms)
