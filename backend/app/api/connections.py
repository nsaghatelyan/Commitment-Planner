import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import CloudConnection, CollectionRun, Tenant
from app.services.connections import deploy_command, new_external_id, test_connection

router = APIRouter(tags=["connections"])
SessionDep = Annotated[Session, Depends(get_session)]
TEMPLATE = Path(__file__).resolve().parents[3] / "infra" / "client-readonly-role.yaml"
ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
CE_COST_PER_CALL = 0.01


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    risk_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"


class ConnectionIn(BaseModel):
    provider: Literal["aws", "azure"]
    name: str = Field(min_length=1, max_length=200)
    aws_auth_mode: Literal["profile", "role"] = "profile"
    aws_profile: str | None = None
    aws_role_arn: str | None = None
    aws_export_bucket: str | None = None
    aws_export_prefix: str | None = None
    azure_tenant_id: str | None = None
    azure_agreement_type: Literal["ea", "mca", "payg", "csp"] | None = None
    azure_billing_scope: str | None = None
    azure_export_container: str | None = None
    azure_client_id: str | None = None
    azure_credential_ref: str | None = None

    @field_validator("aws_role_arn")
    @classmethod
    def _arn(cls, v: str | None) -> str | None:
        v = (v or "").strip() or None
        if v and not ROLE_ARN.match(v):
            raise ValueError("expected arn:aws:iam::<12-digit account>:role/<name>")
        return v

    @field_validator(
        "aws_profile",
        "aws_export_bucket",
        "aws_export_prefix",
        "azure_tenant_id",
        "azure_billing_scope",
        "azure_export_container",
        "azure_client_id",
        "azure_credential_ref",
    )
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class ConnectionPatch(BaseModel):
    name: str | None = None
    aws_profile: str | None = None
    aws_role_arn: str | None = None
    aws_export_bucket: str | None = None
    aws_export_prefix: str | None = None
    azure_billing_scope: str | None = None
    azure_export_container: str | None = None
    azure_client_id: str | None = None
    azure_credential_ref: str | None = None

    _arn = field_validator("aws_role_arn")(ConnectionIn._arn.__func__)


class CollectIn(BaseModel):
    analyze: bool = True


def _run_out(r: CollectionRun) -> dict[str, Any]:
    calls = (r.details or {}).get("api_calls", {})
    return {
        "id": str(r.id),
        "status": r.status,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": r.error,
        "rows_ingested": r.rows_ingested,
        "api_calls": r.api_calls,
        "cost_explorer_calls": sum(n for k, n in calls.items() if k.startswith("ce:")),
        "cost_explorer_usd": (r.details or {}).get("cost_explorer_usd"),
        "period_start": r.period_start,
        "period_end": r.period_end,
        "sources": (r.details or {}).get("sources", []),
        "warnings": (r.details or {}).get("warnings", []),
        "prices_synced": (r.details or {}).get("prices_synced"),
    }


def _conn_out(session: Session, c: CloudConnection) -> dict[str, Any]:
    runs = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.cloud_connection_id == c.id)
        .order_by(CollectionRun.created_at.desc())
        .limit(10)
    ).all()
    out = {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "provider": c.provider,
        "name": c.name,
        "status": c.status,
        "last_verified_at": c.last_verified_at,
        "last_success_at": c.last_success_at,
        "last_error": c.last_error,
        "aws_auth_mode": c.aws_auth_mode,
        "aws_profile": c.aws_profile,
        "aws_role_arn": c.aws_role_arn,
        "aws_external_id": c.aws_external_id,
        "aws_export_bucket": c.aws_export_bucket,
        "aws_export_prefix": c.aws_export_prefix,
        "azure_tenant_id": c.azure_tenant_id,
        "azure_agreement_type": c.azure_agreement_type,
        "azure_billing_scope": c.azure_billing_scope,
        "azure_export_container": c.azure_export_container,
        "azure_client_id": c.azure_client_id,
        "azure_credential_ref": c.azure_credential_ref,
        "runs": [_run_out(r) for r in runs],
    }
    if c.provider == "aws" and c.aws_auth_mode == "role" and c.status != "synthetic":
        out["deploy_command"] = deploy_command(c, get_settings().tool_aws_account_id or None)
    return out


def _connection(session: Session, connection_id: uuid.UUID) -> CloudConnection:
    c = session.get(CloudConnection, connection_id)
    if c is None or c.status == "synthetic":
        raise HTTPException(404, "connection not found")
    return c


@router.post("/tenants", status_code=201)
def create_tenant(body: TenantIn, session: SessionDep) -> dict[str, Any]:
    base = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "client"
    slug, n = base, 1
    while session.scalars(select(Tenant).where(Tenant.slug == slug)).first():
        n += 1
        slug = f"{base}-{n}"
    t = Tenant(name=body.name.strip(), slug=slug, risk_profile=body.risk_profile)
    session.add(t)
    session.commit()
    return {"id": str(t.id), "name": t.name, "slug": t.slug, "risk_profile": t.risk_profile}


@router.delete("/tenants/{tenant_id}", status_code=204)
def delete_tenant(tenant_id: uuid.UUID, session: SessionDep) -> None:
    """Deletes the client and its database rows (usage Parquet files stay on disk)."""
    t = session.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(404, "tenant not found")
    session.delete(t)
    session.commit()


@router.get("/tenants/{tenant_id}/connections")
def list_connections(tenant_id: uuid.UUID, session: SessionDep) -> list[dict[str, Any]]:
    if session.get(Tenant, tenant_id) is None:
        raise HTTPException(404, "tenant not found")
    conns = session.scalars(
        select(CloudConnection)
        .where(CloudConnection.tenant_id == tenant_id)
        .order_by(CloudConnection.created_at)
    ).all()
    return [_conn_out(session, c) for c in conns]


@router.post("/tenants/{tenant_id}/connections", status_code=201)
def create_connection(tenant_id: uuid.UUID, body: ConnectionIn, session: SessionDep):
    if session.get(Tenant, tenant_id) is None:
        raise HTTPException(404, "tenant not found")
    if body.provider == "azure":
        from app.collectors.azure.scopes import validate_scope

        if not (body.azure_tenant_id and body.azure_agreement_type and body.azure_billing_scope):
            raise HTTPException(422, "Azure needs a tenant id, agreement type and billing scope")
        try:
            validate_scope(body.azure_agreement_type, body.azure_billing_scope)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    c = CloudConnection(tenant_id=tenant_id, status="pending", **body.model_dump())
    if body.provider == "aws" and body.aws_auth_mode == "role":
        c.aws_external_id = new_external_id()
    session.add(c)
    session.commit()
    return _conn_out(session, c)


@router.patch("/connections/{connection_id}")
def update_connection(connection_id: uuid.UUID, body: ConnectionPatch, session: SessionDep):
    c = _connection(session, connection_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, (v or "").strip() or None if isinstance(v, str) or v is None else v)
    c.status = "pending"
    session.commit()
    return _conn_out(session, c)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: uuid.UUID, session: SessionDep) -> None:
    session.delete(_connection(session, connection_id))
    session.commit()


@router.post("/connections/{connection_id}/test")
def test(connection_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    c = _connection(session, connection_id)
    if c.provider == "aws" and c.aws_auth_mode == "role" and not c.aws_role_arn:
        raise HTTPException(422, "Deploy the CloudFormation stack, then add the role ARN.")
    return test_connection(session, c)


@router.post("/connections/{connection_id}/collect", status_code=202)
async def collect(connection_id: uuid.UUID, body: CollectIn, session: SessionDep):
    c = _connection(session, connection_id)
    if c.provider == "aws" and c.aws_auth_mode == "role" and not c.aws_role_arn:
        raise HTTPException(422, "Add the role ARN first.")
    active = session.scalars(
        select(CollectionRun).where(
            CollectionRun.cloud_connection_id == c.id,
            CollectionRun.status.in_(["queued", "running"]),
        )
    ).first()
    if active:
        raise HTTPException(409, "A collection is already queued or running.")
    from app.timeutil import utc_today

    run = CollectionRun(
        tenant_id=c.tenant_id,
        cloud_connection_id=c.id,
        status="queued",
        period_start=utc_today(),
        period_end=utc_today(),
        details={},
    )
    session.add(run)
    session.commit()
    from arq import create_pool
    from arq.connections import RedisSettings

    try:
        redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    except Exception as exc:
        run.status, run.error = "failed", f"Queue unavailable: {exc}"
        session.commit()
        raise HTTPException(503, "Redis is not reachable; start docker compose") from exc
    try:
        await redis.enqueue_job("collect_usage", str(c.id), str(run.id), body.analyze)
    finally:
        await redis.aclose()
    return _run_out(run)


@router.get("/connections/{connection_id}/runs")
def runs(connection_id: uuid.UUID, session: SessionDep) -> list[dict[str, Any]]:
    return _conn_out(session, _connection(session, connection_id))["runs"]


@router.get("/infra/client-readonly-role.yaml")
def role_template() -> FileResponse:
    return FileResponse(
        TEMPLATE, media_type="application/x-yaml", filename="client-readonly-role.yaml"
    )


__all__ = ["CE_COST_PER_CALL", "router"]
