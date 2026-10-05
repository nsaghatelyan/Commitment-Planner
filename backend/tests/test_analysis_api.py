"""run_analysis end to end through the database and the API."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import AnalysisRun, Recommendation, Tenant
from app.services.analysis import run_analysis
from app.synthetic import generate
from app.synthetic.seed import seed
from app.usage.storage import UsageStore

pytestmark = pytest.mark.db


@pytest.fixture
def seeded(db_session, tmp_path, monkeypatch):
    store = UsageStore(str(tmp_path / "data"))
    tenant = generate("small", days=45)
    seed(db_session, tenant, store)
    settings = Settings(usage_storage_root=str(tmp_path / "data"))
    monkeypatch.setattr("app.services.analysis.get_settings", lambda: settings)
    return tenant, store


def test_run_analysis_writes_engine_and_native_rows(db_session, seeded):
    tenant, store = seeded
    run = run_analysis(db_session, uuid.UUID(tenant.tenant_id), store=store)
    assert run.status == "succeeded", run.error
    assert run.risk_profile == "balanced" and run.engine_version
    rows = db_session.scalars(
        select(Recommendation).where(Recommendation.analysis_run_id == run.id)
    ).all()
    engine = [r for r in rows if r.source == "engine"]
    native = [r for r in rows if r.source == "native"]
    assert engine and native
    sp = next(r for r in engine if r.kind == "aws_sp_compute")
    assert sp.action == "renew" and sp.urgent and sp.status == "open"
    assert all(r.rationale for r in engine)
    assert sorted(r.plan_rank for r in engine if r.plan_rank) == list(
        range(1, len([r for r in engine if r.plan_rank]) + 1)
    )
    assert run.summary["projected_annual_savings"] > 0
    assert run.summary["native_comparison"]["by_kind"]


def test_unknown_risk_profile_is_rejected(db_session, seeded):
    tenant, store = seeded
    with pytest.raises(ValueError, match="risk_profile"):
        run_analysis(db_session, uuid.UUID(tenant.tenant_id), store=store, risk_profile="yolo")


def test_api(db_session, seeded):
    tenant, _ = seeded
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        client = TestClient(app)
        base = f"/tenants/{tenant.tenant_id}/analysis-runs"
        assert client.get(f"{base}/latest").status_code == 404
        resp = client.post(base, json={"risk_profile": "conservative", "wait": True})
        assert resp.status_code == 202
        run = resp.json()
        assert run["status"] == "succeeded" and run["risk_profile"] == "conservative"
        assert run["summary"]["purchase_plan"]
        assert client.get(f"{base}/latest").json()["id"] == run["id"]
        engine = client.get(f"{base}/{run['id']}/recommendations?source=engine").json()
        assert engine[0]["plan_rank"] == 1 and engine[0]["source"] == "engine"
        assert all(r["expected_utilization_pct"] >= 98 for r in engine if r["action"] != "flag")
        native = client.get(f"{base}/{run['id']}/recommendations?source=native").json()
        assert native and {r["source"] for r in native} == {"native"}
        assert client.get(f"/tenants/{uuid.uuid4()}/analysis-runs/latest").status_code == 404
        other = client.get(f"{base}/{uuid.uuid4()}")
        assert other.status_code == 404
    finally:
        app.dependency_overrides.clear()
    assert db_session.scalar(select(func.count()).select_from(AnalysisRun)) == 1
    assert db_session.get(Tenant, uuid.UUID(tenant.tenant_id)).risk_profile == "balanced"
