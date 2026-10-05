from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path


class Collector(ABC):
    """Pulls usage and existing commitments for one cloud connection."""

    @abstractmethod
    def list_accounts(self) -> list[dict]:
        """Accounts/subscriptions reachable through this connection."""

    @abstractmethod
    def collect_usage(self, start: date, end: date, out_dir: Path) -> list[Path]:
        """Write normalized usage rows as Parquet under out_dir; return the files written."""

    @abstractmethod
    def collect_commitments(self) -> list[dict]:
        """Existing savings plans / reservations with their utilization."""
