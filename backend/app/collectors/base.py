from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date

import pyarrow as pa

from app.collectors.types import (
    AccountInfo,
    CallStats,
    CommitmentRecord,
    NativeRecommendation,
    UtilizationRecord,
)


class Collector(ABC):
    """Pulls usage and commitments for one cloud connection.

    Usage is yielded as normalized tables (app.usage.USAGE_SCHEMA). Each yielded table holds
    every row for the days it covers, so the store can replace those day partitions whole.
    """

    provider: str
    stats: CallStats

    @abstractmethod
    def test_connection(self) -> None:
        """Raise if credentials or permissions are not usable."""

    @abstractmethod
    def list_accounts(self) -> list[AccountInfo]:
        """AWS accounts or Azure subscriptions visible through this connection."""

    @abstractmethod
    def export_usage(self, since: date | None) -> Iterator[pa.Table]:
        """Normalized usage from the client's billing export (primary source).

        `since`: only export periods updated after this date; None reads everything.
        """

    @abstractmethod
    def bootstrap_usage(self, start: date, end: date) -> Iterator[pa.Table]:
        """Daily usage from the provider's query API, for days the export doesn't cover yet.

        `end` is exclusive.
        """

    @abstractmethod
    def collect_commitments(self) -> list[CommitmentRecord]:
        """Existing savings plans and reservations."""

    @abstractmethod
    def collect_utilization(self, start: date, end: date) -> list[UtilizationRecord]:
        """Daily utilization of existing commitments; `end` is exclusive."""

    @abstractmethod
    def collect_native_recommendations(self) -> list[NativeRecommendation]:
        """The provider's own SP/RI purchase recommendations."""
