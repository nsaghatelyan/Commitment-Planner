import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.compute as pc
import pytest
from sqlalchemy import func, select

from app.collectors.base import Collector
from app.collectors.types import CallStats, NativeRecommendation
from app.config import Settings
from app.models import CloudAccount, CloudConnection, CollectionRun, Commitment, Price, Tenant
from app.models.tables import CommitmentUtilization
from app.pricing.types import PriceRecord
from app.services.collection import bootstrap_window, run_collection, split_container_url
from app.services.pricing import upsert_prices
from app.synthetic import generate
from app.synthetic.seed import seed

TODAY = date(2026, 10, 1)


# ---------------------------------------------------------------- pure helpers


def test_bootstrap_window_without_export():
    assert bootstrap_window(TODAY, 13, set(), None) == (date(2025, 9, 1), TODAY)


def test_bootstrap_window_stops_where_export_starts_and_refreshes_recent_days():
    stored = {date(2025, 9, 1) + timedelta(days=i) for i in range(500)}
    # Everything stored: only the last 3 days before the export start are re-pulled.
    assert bootstrap_window(TODAY, 13, stored, date(2026, 9, 1)) == (
        date(2026, 8, 29),
        date(2026, 9, 1),
    )


def test_split_container_url():
    assert split_container_url("https://acct.blob.core.windows.net/costs/exports/focus") == (
        "https://acct.blob.core.windows.net/costs",
        "exports/focus",
    )


# ---------------------------------------------------------------- run_collection


class FakeCollector(Collector):
    """Serves a synthetic tenant: last 5 days as 'export', the rest via 'bootstrap'."""

    provider = "aws"

    def __init__(self, fail: bool = False) -> None:
        self.tenant = generate("small", end=TODAY, days=20)
        self.stats = CallStats()
        self.fail = fail
        self.bootstrap_calls: list[tuple[date, date]] = []

    def _days(self, start, end):
        days = pc.cast(self.tenant.usage.column("charge_period_start"), "date32")
        mask = pc.and_(pc.greater_equal(days, start), pc.less(days, end))
        return self.tenant.usage.filter(mask)

    def test_connection(self):
        self.stats.record("ce:GetCostAndUsage")
        if self.fail:
            raise PermissionError("AccessDenied: not authorized to perform ce:GetCostAndUsage")

    def list_accounts(self):
        return self.tenant.account_infos("aws")

    def export_usage(self, since):
        self.stats.record("s3:GetObject")
        yield self._days(TODAY - timedelta(days=5), TODAY)

    def bootstrap_usage(self, start, end):
        self.bootstrap_calls.append((start, end))
        self.stats.record("ce:GetCostAndUsage")
        yield self._days(start, end)

    def collect_commitments(self):
        return self.tenant.commitments

    def collect_utilization(self, start, end):
        return [u for u in self.tenant.utilization if start <= u.date < end]

    def collect_native_recommendations(self):
        return [
            NativeRecommendation(
                "aws", "aws_sp_compute", 12, "no_upfront", 30, hourly_commitment=Decimal("0.5")
            )
        ]


@pytest.fixture
def connection(db_session):
    tenant = Tenant(name="Acme", slug="acme")
    db_session.add(tenant)
    db_session.flush()
    conn = CloudConnection(
        tenant_id=tenant.id,
        provider="aws",
        name="AWS org",
        aws_role_arn="arn:aws:iam::111111111111:role/SavingsToolReadOnly",
        aws_external_id="ext-0123456789abcdef",
    )
    db_session.add(conn)
    db_session.commit()
    return conn


@pytest.fixture
def settings(tmp_path):
    return Settings(
        usage_storage_root=str(tmp_path / "data"),
        api_cache_dir=str(tmp_path / "cache"),
        bootstrap_lookback_months=1,
    )


@pytest.mark.db
def test_run_collection_end_to_end(db_session, connection, settings):
    fake = FakeCollector()
    run = run_collection(
        db_session, connection.id, settings=settings, collector_factory=lambda *a: fake, today=TODAY
    )
    assert run.status == "succeeded", run.error
    # Export covers the last 5 days; the bootstrap fills from the window start up to there.
    assert fake.bootstrap_calls == [(date(2026, 9, 1), date(2026, 9, 26))]
    assert run.rows_ingested == fake.tenant.usage.num_rows
    assert run.api_calls == 3
    assert run.details["cost_explorer_usd"] == 0.02
    assert run.details["sources"] == ["export", "api_bootstrap"]
    assert run.details["export_first_date"] == "2026-09-26"
    assert (run.period_start, run.period_end) == (date(2026, 9, 11), date(2026, 9, 30))

    db_session.refresh(connection)
    assert connection.status == "active" and connection.last_success_at is not None
    assert connection.last_error is None
    accounts = db_session.scalars(select(CloudAccount)).all()
    assert [(a.external_account_id, a.is_payer) for a in accounts] == [
        (fake.tenant.connections[0].accounts[0].id, True)
    ]
    (sp,) = db_session.scalars(select(Commitment)).all()
    assert sp.kind == "aws_sp_compute" and sp.amortized_hourly_cost == Decimal("0.3")
    assert sp.cloud_account_id == accounts[0].id
    util = db_session.scalar(select(func.count()).select_from(CommitmentUtilization))
    assert util == 20

    recs = Path(run.details["native_recommendations"])
    assert json.loads(recs.read_text())[0]["hourly_commitment"] == "0.5"

    # Second run: already-stored days are skipped except the last few before the export.
    fake2 = FakeCollector()
    run2 = run_collection(
        db_session,
        connection.id,
        settings=settings,
        collector_factory=lambda *a: fake2,
        today=TODAY,
    )
    assert run2.status == "succeeded"
    assert fake2.bootstrap_calls == [(date(2026, 9, 23), date(2026, 9, 26))]
    assert db_session.scalar(select(func.count()).select_from(Commitment)) == 1


@pytest.mark.db
def test_run_collection_records_failures(db_session, connection, settings):
    run = run_collection(
        db_session,
        connection.id,
        settings=settings,
        collector_factory=lambda *a: FakeCollector(fail=True),
        today=TODAY,
    )
    assert run.status == "failed"
    assert "AccessDenied" in run.error
    assert run.api_calls == 1
    db_session.refresh(connection)
    assert connection.status == "error" and "AccessDenied" in connection.last_error
    assert db_session.scalar(select(func.count()).select_from(CollectionRun)) == 1


# ---------------------------------------------------------------- prices / seeding


@pytest.mark.db
def test_price_upsert_is_idempotent(db_session):
    rec = PriceRecord(
        "aws",
        "ec2",
        "ec2|m5.large|Linux|Shared",
        "us-east-1",
        "on_demand",
        "Hrs",
        Decimal("0.096"),
        TODAY,
    )
    assert upsert_prices(db_session, [rec, rec]) == 1
    rec.price_per_unit = Decimal("0.1")
    upsert_prices(db_session, [rec])
    # term_months / payment_option are NULL; the constraint treats NULLs as equal.
    (price,) = db_session.scalars(select(Price)).all()
    assert price.price_per_unit == Decimal("0.1")


@pytest.mark.db
@pytest.mark.parametrize("profile", ["medium", "startup"])
def test_seed_synthetic_tenant(db_session, tmp_path, profile):
    from app.usage.storage import UsageStore

    tenant = generate(profile, end=TODAY, days=3 if profile != "startup" else None)
    store = UsageStore(str(tmp_path / "data"))
    counts = seed(db_session, tenant, store)
    assert counts["usage_rows"] == tenant.usage.num_rows
    assert counts["commitments"] == len(tenant.commitments)
    conns = db_session.scalars(select(CloudConnection)).all()
    assert {c.status for c in conns} == {"synthetic"}
    if profile == "medium":
        assert conns[0].azure_agreement_type == "ea"
        stored = db_session.scalars(select(Commitment)).all()
        assert all(c.amortized_hourly_cost for c in stored)
    # re-seeding is idempotent
    seed(db_session, tenant, store)
    assert db_session.scalar(select(func.count()).select_from(Commitment)) == len(
        tenant.commitments
    )
