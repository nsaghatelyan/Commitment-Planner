"""Connections: error messages, profile mode, tolerant collection, API, tiny accounts.
Nothing here talks to a real cloud account."""

import uuid
from datetime import UTC, date, datetime, timedelta

import boto3
import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ProfileNotFound,
)
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import select

from app.collectors.aws import AwsCollector
from app.collectors.aws.errors import explain
from app.collectors.types import AccountInfo, CallStats
from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import CloudConnection, Tenant
from app.services.analysis import run_analysis
from app.usage.schema import usage_table
from app.usage.storage import UsageStore


def _client_error(code: str, message: str, op: str = "GetCostAndUsage") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, op)


# ---------------------------------------------------------------- error messages


def test_error_explanations():
    assert "not found" in explain(ProfileNotFound(profile="nope"))
    assert "No AWS credentials" in explain(NoCredentialsError())
    denied = _client_error(
        "AccessDeniedException",
        "User: arn:aws:iam::1:user/x is not authorized to perform: ce:GetCostAndUsage on resource",
    )
    assert "ce:GetCostAndUsage" in explain(denied)
    assert "Cost Explorer isn't enabled" in explain(
        _client_error("AccessDeniedException", "User not enabled for cost explorer access")
    )
    assert "expired" in explain(_client_error("ExpiredTokenException", "x"))


# ---------------------------------------------------------------- collector


def test_profile_mode_uses_local_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        c = AwsCollector(None, None, profile=None)
        ident = c.identity()
        assert ident["account"] == "123456789012"
        assert "sts:AssumeRole" not in c.stats.calls


def test_unavailable_services_become_warnings():
    """A service missing in a region, or denied by IAM, must not fail the collection."""
    clients: dict = {}

    def factory(service, region):
        key = (service, region)
        if key not in clients:
            client = boto3.client(
                service,
                region_name=region or "us-east-1",
                aws_access_key_id="x",
                aws_secret_access_key="x",
            )
            stub = Stubber(client)
            stub.activate()
            clients[key] = (client, stub)
        return clients[key][0]

    c = AwsCollector(None, None, client_factory=factory)
    factory("savingsplans", None)
    clients[("savingsplans", None)][1].add_response("describe_savings_plans", {"savingsPlans": []})
    factory("ec2", None)
    clients[("ec2", None)][1].add_response(
        "describe_regions", {"Regions": [{"RegionName": "ap-southeast-4"}]}
    )
    region = "ap-southeast-4"
    factory("ec2", region)
    clients[("ec2", region)][1].add_response(
        "describe_reserved_instances", {"ReservedInstances": []}
    )
    factory("rds", region)
    clients[("rds", region)][1].add_client_error(
        "describe_reserved_db_instances",
        "AccessDenied",
        "not authorized to perform: rds:DescribeReservedDBInstances",
    )
    for svc, op, key in (
        ("elasticache", "describe_reserved_cache_nodes", "ReservedCacheNodes"),
        ("redshift", "describe_reserved_nodes", "ReservedNodes"),
    ):
        factory(svc, region)
        clients[(svc, region)][1].add_response(op, {key: []})
    # OpenSearch and MemoryDB aren't offered in this region
    for svc in ("opensearch", "memorydb"):
        factory(svc, region)
    for svc, op in (
        ("opensearch", "describe_reserved_instances"),
        ("memorydb", "describe_reserved_nodes"),
    ):
        client = clients[(svc, region)][0]

        def boom(*_, _svc=svc, **__):
            raise EndpointConnectionError(endpoint_url=f"https://{_svc}.{region}.amazonaws.com")

        setattr(client, op, boom)
    factory("ce", None)
    clients[("ce", None)][1].add_client_error(
        "get_reservation_utilization", "DataUnavailableException", "no data"
    )
    factory("sts", None)
    clients[("sts", None)][1].add_response(
        "get_caller_identity",
        {"Account": "111111111111", "Arn": "arn:aws:iam::111111111111:user/x", "UserId": "AIDA"},
    )
    assert c.collect_commitments() == []
    text = " ".join(c.warnings)
    assert "rds:DescribeReservedDBInstances" in text
    assert "opensearch" in text and "memorydb" in text
    # no savings plans: the 30 billed per-day utilization calls are skipped
    assert c.collect_utilization(date(2026, 9, 1), date(2026, 10, 1)) == []
    assert "ce:GetSavingsPlansUtilizationDetails" not in c.stats.calls


# ---------------------------------------------------------------- API (database)


class FakeCollector:
    def __init__(self, fail=None):
        self.stats = CallStats()
        self.fail = fail

    def identity(self):
        self.stats.record("sts:GetCallerIdentity")
        return {"account": "111111111111", "arn": "arn:aws:iam::111111111111:user/narek"}

    def test_connection(self):
        self.stats.record("ce:GetCostAndUsage")
        if self.fail:
            raise self.fail

    def list_accounts(self):
        return [AccountInfo("111111111111", "narek", True)]


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.db
def test_connection_lifecycle(client, db_session, monkeypatch):
    tenant = client.post("/tenants", json={"name": "Narek AWS"}).json()
    assert tenant["slug"] == "narek-aws"
    assert client.post("/tenants", json={"name": "Narek AWS"}).json()["slug"] == "narek-aws-2"
    base = f"/tenants/{tenant['id']}/connections"

    bad = client.post(
        base,
        json={
            "provider": "aws",
            "name": "x",
            "aws_auth_mode": "role",
            "aws_role_arn": "not-an-arn",
        },
    )
    assert bad.status_code == 422
    role = client.post(base, json={"provider": "aws", "name": "prod org", "aws_auth_mode": "role"})
    role = role.json()
    assert role["aws_external_id"].startswith("svt-") and "deploy_command" in role
    assert role["aws_external_id"] in role["deploy_command"]
    assert client.post(f"/connections/{role['id']}/test").status_code == 422  # no ARN yet
    patched = client.patch(
        f"/connections/{role['id']}",
        json={"aws_role_arn": "arn:aws:iam::222222222222:role/SavingsToolReadOnly"},
    )
    assert patched.json()["aws_role_arn"].endswith("SavingsToolReadOnly")

    prof = client.post(
        base,
        json={
            "provider": "aws",
            "name": "my account",
            "aws_auth_mode": "profile",
            "aws_profile": " default ",
        },
    ).json()
    assert prof["aws_profile"] == "default" and "deploy_command" not in prof
    chain = client.post(
        base,
        json={
            "provider": "aws",
            "name": "default chain",
            "aws_auth_mode": "profile",
            "aws_profile": "  ",
        },
    ).json()
    assert chain["aws_profile"] is None  # empty -> boto3 default credential chain
    from app.config import get_settings
    from app.services.collection import build_collector

    conn = db_session.get(CloudConnection, uuid.UUID(chain["id"]))
    collector = build_collector(conn, get_settings(), None)
    assert collector.profile is None and collector.role_arn is None

    monkeypatch.setattr("app.services.connections.build_collector", lambda *a: FakeCollector())
    ok = client.post(f"/connections/{prof['id']}/test").json()
    assert ok["ok"] and ok["identity"]["account"] == "111111111111"
    assert ok["accounts"][0]["is_payer"]
    denied = _client_error(
        "AccessDeniedException", "is not authorized to perform: ce:GetCostAndUsage"
    )
    monkeypatch.setattr(
        "app.services.connections.build_collector", lambda *a: FakeCollector(fail=denied)
    )
    fail = client.post(f"/connections/{prof['id']}/test").json()
    assert not fail["ok"] and "ce:GetCostAndUsage" in fail["message"]
    listed = client.get(base).json()
    assert {c["status"] for c in listed} == {"pending", "error"}

    azure_bad = client.post(
        base,
        json={
            "provider": "azure",
            "name": "az",
            "azure_tenant_id": "t",
            "azure_agreement_type": "mca",
            "azure_billing_scope": "/subscriptions/x",
        },
    )
    assert azure_bad.status_code == 422 and "billingProfiles" in azure_bad.json()["detail"]

    assert client.get("/infra/client-readonly-role.yaml").text.startswith(
        "AWSTemplateFormatVersion"
    )


@pytest.mark.db
def test_collect_is_queued(client, db_session, monkeypatch):
    tenant = client.post("/tenants", json={"name": "Queue test"}).json()
    conn = client.post(
        f"/tenants/{tenant['id']}/connections",
        json={"provider": "aws", "name": "a", "aws_profile": "default"},
    ).json()
    jobs = []

    class FakeRedis:
        async def enqueue_job(self, *args):
            jobs.append(args)

        async def aclose(self):
            pass

    async def fake_pool(*_):
        return FakeRedis()

    monkeypatch.setattr("arq.create_pool", fake_pool)
    run = client.post(f"/connections/{conn['id']}/collect", json={}).json()
    assert run["status"] == "queued"
    assert jobs == [("collect_usage", conn["id"], run["id"], True)]
    assert client.post(f"/connections/{conn['id']}/collect", json={}).status_code == 409
    assert client.get(f"/connections/{conn['id']}/runs").json()[0]["status"] == "queued"


@pytest.mark.db
def test_tiny_account_with_daily_cost_explorer_history(db_session, tmp_path):
    """One t3.micro, 13 months of daily Cost Explorer rows, no export, no commitments."""
    tenant = Tenant(name="Tiny", slug=f"tiny-{uuid.uuid4().hex[:6]}")
    db_session.add(tenant)
    db_session.flush()
    db_session.add(
        CloudConnection(
            tenant_id=tenant.id,
            provider="aws",
            name="mine",
            aws_auth_mode="profile",
            status="active",
        )
    )
    db_session.commit()
    end = date(2026, 10, 1)
    rows = []
    for i in range(395):
        day = datetime.combine(end - timedelta(days=395 - i), datetime.min.time(), UTC)
        rows.append(
            {
                "charge_period_start": day,
                "charge_period_end": day + timedelta(days=1),
                "provider": "aws",
                "billing_account_id": "111111111111",
                "service_name": "Amazon Elastic Compute Cloud - Compute",
                "service_category": "Compute",
                "region": "us-east-1",
                "instance_type": "t3.micro",
                "instance_family": "t3",
                "operating_system": "Linux",
                "tenancy": "Shared",
                "charge_category": "Usage",
                "pricing_category": "On-Demand",
                "usage_quantity": 24.0,
                "usage_unit": "Hrs",
                "normalized_units": 12.0,
                "billed_cost": 0.2496,
                "effective_cost": 0.2496,
                "list_cost": 0.2496,
                "on_demand_equiv_cost": 0.2496,
            }
        )
    store = UsageStore(str(tmp_path / "data"))
    store.write(str(tenant.id), "aws", usage_table(rows))
    run = run_analysis(
        db_session,
        tenant.id,
        store=store,
        settings=Settings(usage_storage_root=str(tmp_path / "data")),
    )
    assert run.status == "succeeded", run.error
    s = run.summary
    assert s["purchase_plan"] == []
    assert s["history_days"] >= 390
    reasons = {p["reason"] for p in s["skipped_pools"]}
    assert any("price" in r or "less than" in r for r in reasons)
    assert s["on_demand_spend_monthly"] == pytest.approx(0.2496 * 30.4, rel=0.01)
    assert s["backtest"]["available"] and s["backtest"]["recommendations"] == []
    assert db_session.scalars(select(Tenant).where(Tenant.id == tenant.id)).one()
