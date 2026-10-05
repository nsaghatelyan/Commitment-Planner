from datetime import date
from pathlib import Path

from app.collectors.base import Collector


class AwsCollector(Collector):
    """Assumes the client's read-only role (with ExternalId) and reads Cost Explorer / CUR."""

    def __init__(self, role_arn: str, external_id: str) -> None:
        self.role_arn = role_arn
        self.external_id = external_id

    def list_accounts(self) -> list[dict]:
        raise NotImplementedError

    def collect_usage(self, start: date, end: date, out_dir: Path) -> list[Path]:
        raise NotImplementedError

    def collect_commitments(self) -> list[dict]:
        raise NotImplementedError
