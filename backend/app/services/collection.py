"""Run one collection for a cloud connection: usage to Parquet, everything else to Postgres."""

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.collectors.aws.commitments import amortized_hourly
from app.collectors.base import Collector
from app.collectors.cache import ApiCache
from app.collectors.types import CommitmentRecord, NativeRecommendation, UtilizationRecord
from app.config import Settings, get_settings
from app.models import CloudAccount, CloudConnection, CollectionRun, Commitment
from app.models.tables import CommitmentUtilization
from app.timeutil import utc_now, utc_today
from app.usage.storage import UsageStore

log = logging.getLogger(__name__)

# Recent days are re-pulled from the bootstrap API because providers keep finalizing them.
BOOTSTRAP_REFRESH_DAYS = 3
UTILIZATION_LOOKBACK_DAYS = 30

CollectorFactory = Callable[[CloudConnection, Settings, ApiCache], Collector]


def build_collector(conn: CloudConnection, settings: Settings, cache: ApiCache) -> Collector:
    if conn.provider == "aws":
        from app.collectors.aws import AwsCollector

        return AwsCollector(
            conn.aws_role_arn or "",
            conn.aws_external_id or "",
            export_bucket=conn.aws_export_bucket,
            export_prefix=conn.aws_export_prefix,
            region=settings.aws_region,
            session_name=settings.aws_role_session_name,
            cache=cache,
        )
    if conn.provider == "azure":
        from app.collectors.azure import AzureCollector
        from app.collectors.azure.auth import client_credential
        from app.collectors.azure.export import ContainerBlobSource
        from app.collectors.azure.http import AzureHttp

        credential = client_credential(
            conn.azure_tenant_id or "",
            settings.azure_app_client_id,
            settings.azure_app_certificate_path,
        )
        source, prefix = None, ""
        if conn.azure_export_container:
            container_url, prefix = split_container_url(conn.azure_export_container)
            source = ContainerBlobSource(container_url, credential)
        return AzureCollector(
            AzureHttp(credential),
            agreement_type=conn.azure_agreement_type or "",
            billing_scope=conn.azure_billing_scope or "",
            export_source=source,
            export_prefix=prefix,
            cache=cache,
        )
    raise ValueError(f"Unknown provider {conn.provider!r}")


def split_container_url(url: str) -> tuple[str, str]:
    """https://acct.blob.core.windows.net/container/some/prefix -> (container url, prefix)."""
    parsed = urlparse(url)
    container, _, prefix = parsed.path.lstrip("/").partition("/")
    return f"{parsed.scheme}://{parsed.netloc}/{container}", prefix


def bootstrap_window(
    today: date,
    lookback_months: int,
    stored: set[date],
    export_first_date: date | None,
) -> tuple[date, date] | None:
    """Days to pull from the bootstrap API: the lookback window up to where the export
    starts, skipping days already stored except the last few (which are refreshed)."""
    start = date(today.year - (lookback_months // 12), today.month, 1)
    months = lookback_months % 12
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)
    end = min(today, export_first_date) if export_first_date else today
    refresh_from = end - timedelta(days=BOOTSTRAP_REFRESH_DAYS)
    needed = [
        start + timedelta(days=i)
        for i in range((end - start).days)
        if (start + timedelta(days=i)) not in stored or start + timedelta(days=i) >= refresh_from
    ]
    if not needed:
        return None
    return needed[0], needed[-1] + timedelta(days=1)


def run_collection(
    session: Session,
    connection_id: Any,
    *,
    store: UsageStore | None = None,
    settings: Settings | None = None,
    collector_factory: CollectorFactory = build_collector,
    today: date | None = None,
) -> CollectionRun:
    settings = settings or get_settings()
    store = store or UsageStore(settings.usage_storage_root)
    today = today or utc_today()
    conn = session.get(CloudConnection, connection_id)
    if conn is None:
        raise ValueError(f"cloud_connection {connection_id} not found")

    previous = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.cloud_connection_id == conn.id, CollectionRun.status == "succeeded")
        .order_by(CollectionRun.finished_at.desc())
        .limit(1)
    ).first()
    run = CollectionRun(
        tenant_id=conn.tenant_id,
        cloud_connection_id=conn.id,
        status="running",
        period_start=today,
        period_end=today,
        usage_location=store.uri,
        started_at=utc_now(),
        details={},
    )
    session.add(run)
    session.commit()

    cache = ApiCache(settings.api_cache_dir, settings.api_cache_ttl_hours * 3600)
    collector = collector_factory(conn, settings, cache)
    tenant = str(conn.tenant_id)
    rows = 0
    written: set[date] = set()
    details: dict[str, Any] = {"sources": []}
    try:
        collector.test_connection()
        conn.last_verified_at = utc_now()
        accounts = sync_accounts(session, conn, collector)

        # 1. Billing export: the primary source.
        since = previous.started_at.date() - timedelta(days=1) if previous else None
        for table in collector.export_usage(since):
            counts = store.write(tenant, conn.provider, table)
            rows += sum(counts.values())
            written |= set(counts)
        export_first = min(written) if written else None
        if previous and previous.details.get("export_first_date"):
            earlier = date.fromisoformat(previous.details["export_first_date"])
            export_first = min(export_first, earlier) if export_first else earlier
        if written:
            details["sources"].append("export")
        details["export_first_date"] = export_first.isoformat() if export_first else None

        # 2. API bootstrap for days the export doesn't cover yet. Days already pulled count as
        #    stored even when they had no usage, so they are not re-queried (and re-billed).
        covered = _covered_days(previous)
        window = bootstrap_window(
            today,
            settings.bootstrap_lookback_months,
            store.dates(tenant, conn.provider) | covered,
            export_first,
        )
        if window:
            for table in collector.bootstrap_usage(*window):
                counts = store.write(tenant, conn.provider, table)
                rows += sum(counts.values())
                written |= set(counts)
            details["sources"].append("api_bootstrap")
            details["bootstrap_window"] = [d.isoformat() for d in window]
            covered |= _days(*window)
        if covered:
            details["bootstrap_covered"] = [min(covered).isoformat(), max(covered).isoformat()]

        # 3. Existing commitments, their utilization, and native recommendations.
        commitments = collector.collect_commitments()
        ids = upsert_commitments(session, conn, accounts, commitments)
        util = collector.collect_utilization(
            today - timedelta(days=UTILIZATION_LOOKBACK_DAYS), today
        )
        upsert_utilization(session, ids, commitments, util)
        native = collector.collect_native_recommendations()
        details["native_recommendations"] = save_native_recommendations(
            store, tenant, conn.provider, today, native
        )
        details["commitments"] = len(commitments)

        run.status = "succeeded"
        conn.status = "active"
        conn.last_success_at = utc_now()
        conn.last_error = None
    except Exception as exc:
        log.exception("collection failed for connection %s", conn.id)
        session.rollback()
        run = session.merge(run)
        conn = session.merge(conn)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        conn.last_error = run.error
        if conn.status == "pending":
            conn.status = "error"
    finally:
        run.finished_at = utc_now()
        run.rows_ingested = rows
        run.api_calls = collector.stats.total
        details["api_calls"] = collector.stats.calls
        details["cache_hits"] = collector.stats.cache_hits
        details["cost_explorer_usd"] = round(
            0.01 * sum(n for api, n in collector.stats.calls.items() if api.startswith("ce:")), 2
        )
        if written:
            run.period_start, run.period_end = min(written), max(written)
        run.details = details
        session.commit()
    return run


def _days(start: date, end: date) -> set[date]:
    return {start + timedelta(days=i) for i in range((end - start).days)}


def _covered_days(previous: CollectionRun | None) -> set[date]:
    """Days a previous successful run already pulled through the bootstrap API."""
    span = (previous.details or {}).get("bootstrap_covered") if previous else None
    if not span:
        return set()
    first, last = (date.fromisoformat(d) for d in span)
    return _days(first, last + timedelta(days=1))


def sync_accounts(session: Session, conn: CloudConnection, collector: Collector) -> dict[str, Any]:
    """Upsert accounts/subscriptions; returns external id -> cloud_account.id."""
    for acct in collector.list_accounts():
        stmt = insert(CloudAccount).values(
            tenant_id=conn.tenant_id,
            cloud_connection_id=conn.id,
            provider=conn.provider,
            external_account_id=acct.external_account_id,
            name=acct.name,
            is_payer=acct.is_payer,
        )
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_cloud_account_cloud_connection_id",
                set_={"name": stmt.excluded.name, "is_payer": stmt.excluded.is_payer},
            )
        )
    session.flush()
    rows = session.execute(
        select(CloudAccount.external_account_id, CloudAccount.id).where(
            CloudAccount.cloud_connection_id == conn.id
        )
    )
    return {ext: id_ for ext, id_ in rows}


def upsert_commitments(
    session: Session,
    conn: CloudConnection,
    accounts: dict[str, Any],
    records: list[CommitmentRecord],
) -> dict[str, Any]:
    """Returns provider_commitment_id -> commitment.id."""
    for r in records:
        values = {
            "tenant_id": conn.tenant_id,
            "cloud_connection_id": conn.id,
            "cloud_account_id": accounts.get(r.owner_account_id or ""),
            "provider": conn.provider,
            "kind": r.kind,
            "provider_commitment_id": r.provider_commitment_id,
            "scope": r.scope,
            "service": r.service,
            "region": r.region,
            "instance_family": r.instance_family,
            "instance_type": r.instance_type,
            "quantity": r.quantity,
            "hourly_commitment": r.hourly_commitment,
            "term_months": r.term_months,
            "payment_option": r.payment_option,
            "start_at": r.start_at,
            "end_at": r.end_at,
            "upfront_cost": r.upfront_cost,
            "recurring_hourly_cost": r.recurring_hourly_cost,
            "amortized_hourly_cost": amortized_hourly(r),
            "state": r.state,
            "attributes": json.loads(json.dumps(r.attributes, default=str)),
        }
        stmt = insert(Commitment).values(**values)
        update = {k: stmt.excluded[k] for k in values if k not in ("tenant_id",)}
        session.execute(
            stmt.on_conflict_do_update(constraint="uq_commitment_cloud_connection_id", set_=update)
        )
    session.flush()
    rows = session.execute(
        select(Commitment.provider_commitment_id, Commitment.id).where(
            Commitment.cloud_connection_id == conn.id
        )
    )
    return {pid: id_ for pid, id_ in rows}


def upsert_utilization(
    session: Session,
    ids: dict[str, Any],
    commitments: list[CommitmentRecord],
    records: list[UtilizationRecord],
) -> int:
    amortized = {c.provider_commitment_id: amortized_hourly(c) for c in commitments}
    count = 0
    for r in records:
        commitment_id = ids.get(r.provider_commitment_id)
        if commitment_id is None:
            continue
        unused = r.unused_cost
        hourly = amortized.get(r.provider_commitment_id)
        if unused is None and hourly is not None:
            unused = hourly * 24 * (Decimal(100) - r.utilization_pct) / Decimal(100)
        values = {
            "commitment_id": commitment_id,
            "date": r.date,
            "utilization_pct": r.utilization_pct,
            "unused_cost": unused,
            "used_amount": r.used_amount,
            "net_savings": r.net_savings,
        }
        stmt = insert(CommitmentUtilization).values(**values)
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_commitment_utilization_commitment_id",
                set_={k: stmt.excluded[k] for k in values if k not in ("commitment_id", "date")},
            )
        )
        count += 1
    return count


def save_native_recommendations(
    store: UsageStore, tenant: str, provider: str, day: date, recs: list[NativeRecommendation]
) -> str:
    """Kept as JSON next to the usage data; the engine reads them as one input."""
    base = store.root.rsplit("/", 1)[0]
    path = f"{base}/native_recommendations/tenant={tenant}/provider={provider}"
    store.fs.create_dir(path, recursive=True)
    target = f"{path}/{day.isoformat()}.json"
    with store.fs.open_output_stream(target) as out:
        out.write(json.dumps([asdict(r) for r in recs], default=str, indent=1).encode())
    return target
