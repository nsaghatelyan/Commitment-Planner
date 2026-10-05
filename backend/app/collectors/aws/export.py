"""Read the client's Data Exports (FOCUS 1.0 or CUR 2.0, Parquet) from their S3 bucket.

Data Exports rewrite a whole billing period at a time under .../BILLING_PERIOD=YYYY-MM/, so
each period is read in full and yielded as one table (its day partitions are replaced whole).
"""

import io
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.collectors.cache import CountingCaller
from app.usage.normalize import normalize

_PERIOD = re.compile(r"BILLING_PERIOD=(\d{4}-\d{2})")


def list_export_files(s3: Any, call: CountingCaller, bucket: str, prefix: str) -> dict[str, list]:
    """billing period -> [(key, last_modified)] for Parquet data files."""
    periods: dict[str, list] = {}
    token = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix.lstrip("/")}
        if token:
            kwargs["ContinuationToken"] = token
        resp = call("s3:ListObjectsV2", s3.list_objects_v2, **kwargs)
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            match = _PERIOD.search(key)
            if match and key.endswith(".parquet"):
                periods.setdefault(match.group(1), []).append((key, obj["LastModified"]))
        if not resp.get("IsTruncated"):
            return periods
        token = resp["NextContinuationToken"]


def read_export(
    s3: Any, call: CountingCaller, bucket: str, prefix: str, since: date | None
) -> Iterator[pa.Table]:
    for period, files in sorted(list_export_files(s3, call, bucket, prefix).items()):
        if since and max(modified.date() for _, modified in files) < since:
            continue
        tables = []
        for key, _ in files:
            body = call("s3:GetObject", s3.get_object, Bucket=bucket, Key=key)["Body"].read()
            tables.append(normalize(pq.read_table(io.BytesIO(body)), "aws"))
        if tables:
            yield pa.concat_tables(tables)
