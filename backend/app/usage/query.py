"""DuckDB access to the normalized usage store."""

import duckdb

from app.usage.storage import UsageStore


def connect(store: UsageStore, tenant_id: str) -> duckdb.DuckDBPyConnection:
    """Open DuckDB with a `usage` view over one tenant's Parquet (all providers and dates)."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    if store.uri.startswith("s3://"):
        con.execute(
            "INSTALL httpfs; LOAD httpfs; CREATE SECRET (TYPE s3, PROVIDER credential_chain)"
        )
        base = f"{store.uri}/tenant={tenant_id}"
    else:
        base = f"{store.root}/tenant={tenant_id}"
    glob = f"{base}/provider=*/date=*/*.parquet".replace("'", "''")
    # The `provider` column is also stored in the files; the partition value is identical.
    con.execute(
        f"CREATE VIEW usage AS SELECT * FROM read_parquet('{glob}', "
        "hive_partitioning = true, union_by_name = true)"
    )
    return con
