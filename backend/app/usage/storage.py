"""Partitioned Parquet store: <root>/usage_hourly/tenant=<id>/provider=<p>/date=YYYY-MM-DD/part-0.parquet."""

import os
from datetime import date

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from app.usage.schema import conform

DATASET = "usage_hourly"


class UsageStore:
    def __init__(self, root: str) -> None:
        if "://" in root:
            self.fs, self.root = pafs.FileSystem.from_uri(root)
        else:
            self.fs, self.root = pafs.LocalFileSystem(), os.path.abspath(root)
        self.root = f"{self.root.rstrip('/')}/{DATASET}"
        self.uri = f"{root.rstrip('/')}/{DATASET}"

    def partition_dir(self, tenant_id: str, provider: str, day: date) -> str:
        return f"{self.root}/tenant={tenant_id}/provider={provider}/date={day.isoformat()}"

    def write(self, tenant_id: str, provider: str, table: pa.Table) -> dict[date, int]:
        """Write rows grouped by day. Each day written fully replaces that day's partition.

        Callers must therefore pass every row for a day in one call.
        Returns rows written per day.
        """
        if table.num_rows == 0:
            return {}
        table = conform(table)
        days = pc.cast(table.column("charge_period_start"), pa.date32())
        written: dict[date, int] = {}
        for day in pc.unique(days).to_pylist():
            part = table.filter(pc.equal(days, pa.scalar(day, pa.date32())))
            path = self.partition_dir(tenant_id, provider, day)
            self.fs.create_dir(path, recursive=True)
            self.fs.delete_dir_contents(path, missing_dir_ok=True)
            pq.write_table(part, f"{path}/part-0.parquet", filesystem=self.fs)
            written[day] = part.num_rows
        return written

    def dates(self, tenant_id: str, provider: str) -> set[date]:
        base = f"{self.root}/tenant={tenant_id}/provider={provider}"
        selector = pafs.FileSelector(base, allow_not_found=True)
        out = set()
        for info in self.fs.get_file_info(selector):
            if info.type == pafs.FileType.Directory and info.base_name.startswith("date="):
                out.add(date.fromisoformat(info.base_name.removeprefix("date=")))
        return out

    def read(self, tenant_id: str, provider: str | None = None) -> pa.Table:
        """Read a tenant's usage back (small data / tests; analysis should use DuckDB)."""
        base = f"{self.root}/tenant={tenant_id}"
        if provider:
            base += f"/provider={provider}"
        if self.fs.get_file_info(base).type == pafs.FileType.NotFound:
            return conform(pa.table({}))
        dataset = pq.ParquetDataset(base, filesystem=self.fs, partitioning=None)
        return conform(dataset.read())
