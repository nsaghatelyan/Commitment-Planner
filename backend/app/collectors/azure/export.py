"""Read Cost Management FOCUS exports (Parquet or gzipped CSV) from the client's container.

Layout: <directory>/<export name>/<YYYYMMDD-YYYYMMDD>/<run id>/part_*.{parquet,csv.gz}.
Every run rewrites the whole date range, so for each range only the newest run is read.
"""

import gzip
import io
import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Protocol

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from app.collectors.cache import CountingCaller
from app.usage.normalize import normalize

_RANGE = re.compile(r"/(\d{8}-\d{8})/([^/]+)/[^/]+$")


class BlobSource(Protocol):
    def list(self, prefix: str) -> list[tuple[str, datetime]]: ...

    def read(self, name: str) -> bytes: ...


class ContainerBlobSource:
    """azure-storage-blob implementation. container_url: https://<acct>.blob.core.windows.net/<c>"""

    def __init__(self, container_url: str, credential) -> None:
        from azure.storage.blob import ContainerClient

        self.container = ContainerClient.from_container_url(container_url, credential=credential)

    def list(self, prefix: str) -> list[tuple[str, datetime]]:
        return [
            (b.name, b.last_modified)
            for b in self.container.list_blobs(name_starts_with=prefix or None)
        ]

    def read(self, name: str) -> bytes:
        return self.container.download_blob(name).readall()


def latest_runs(blobs: list[tuple[str, datetime]]) -> dict[str, list[tuple[str, datetime]]]:
    """date range -> data files of the newest run for that range."""
    runs: dict[str, dict[str, list[tuple[str, datetime]]]] = {}
    for name, modified in blobs:
        if not name.endswith((".parquet", ".csv.gz", ".csv")):
            continue
        m = _RANGE.search("/" + name)
        if not m:
            continue
        runs.setdefault(m.group(1), {}).setdefault(m.group(2), []).append((name, modified))
    out = {}
    for date_range, by_run in runs.items():
        newest = max(by_run.values(), key=lambda files: max(t for _, t in files))
        out[date_range] = newest
    return out


def read_file(name: str, data: bytes) -> pa.Table:
    if name.endswith(".parquet"):
        return pq.read_table(io.BytesIO(data))
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    return pacsv.read_csv(io.BytesIO(data))


def read_export(
    source: BlobSource, call: CountingCaller, prefix: str, since: date | None
) -> Iterator[pa.Table]:
    blobs = call("azure:ListBlobs", source.list, prefix=prefix)
    for _, files in sorted(latest_runs(blobs).items()):
        if since and max(t for _, t in files).date() < since:
            continue
        tables = [
            normalize(read_file(name, call("azure:GetBlob", source.read, name=name)), "azure")
            for name, _ in files
        ]
        if tables:
            yield pa.concat_tables(tables)
