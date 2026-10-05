from datetime import date
from pathlib import Path

from app.collectors.base import Collector


class AzureCollector(Collector):
    """Uses a service principal with Reader / Cost Management Reader on the client's scope."""

    def __init__(self, tenant_id: str, client_id: str, scope: str) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.scope = scope

    def list_accounts(self) -> list[dict]:
        raise NotImplementedError

    def collect_usage(self, start: date, end: date, out_dir: Path) -> list[Path]:
        raise NotImplementedError

    def collect_commitments(self) -> list[dict]:
        raise NotImplementedError
