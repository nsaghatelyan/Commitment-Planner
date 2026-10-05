"""Create and test cloud connections (no secrets are stored; see CloudConnection)."""

import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.collectors.cache import ApiCache
from app.config import Settings, get_settings
from app.models import CloudConnection
from app.services.collection import build_collector
from app.timeutil import utc_now


def new_external_id() -> str:
    return f"svt-{secrets.token_hex(16)}"


def deploy_command(conn: CloudConnection, tool_account_id: str | None) -> str:
    """The CloudFormation command the client runs in their payer account (role mode)."""
    params = [
        f"ToolAccountId={tool_account_id or '<TOOL_ACCOUNT_ID>'}",
        f"ExternalId={conn.aws_external_id}",
    ]
    if conn.aws_export_bucket:
        params.append(f"ExportBucketName={conn.aws_export_bucket}")
    return (
        "aws cloudformation deploy \\\n"
        "  --template-file infra/client-readonly-role.yaml \\\n"
        "  --stack-name savings-tool-readonly \\\n"
        "  --capabilities CAPABILITY_NAMED_IAM \\\n"
        f"  --parameter-overrides {' '.join(params)}"
    )


def test_connection(
    session: Session,
    conn: CloudConnection,
    settings: Settings | None = None,
    collector_factory=None,
) -> dict[str, Any]:
    """Credentials + one Cost Explorer call (AWS) or one Query API call (Azure)."""
    settings = settings or get_settings()
    collector = (collector_factory or build_collector)(conn, settings, ApiCache(None, 0))
    result: dict[str, Any] = {"ok": False}
    try:
        if conn.provider == "aws":
            result["identity"] = collector.identity()
            collector.test_connection()
            result["accounts"] = [
                {"id": a.external_account_id, "name": a.name, "is_payer": a.is_payer}
                for a in collector.list_accounts()
            ]
        else:
            collector.test_connection()
            result["accounts"] = [
                {"id": a.external_account_id, "name": a.name} for a in collector.list_accounts()
            ]
        result["ok"] = True
        result["message"] = "Connected. Cost data is readable."
        conn.status = "active"
        conn.last_verified_at = utc_now()
        conn.last_error = None
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the user
        result["message"] = explain_error(conn.provider, exc)
        conn.status = "error"
        conn.last_error = result["message"]
    result["api_calls"] = collector.stats.calls
    session.commit()
    return result


def explain_error(provider: str, exc: BaseException) -> str:
    if provider == "aws":
        from app.collectors.aws.errors import explain

        return explain(exc)
    return f"{type(exc).__name__}: {exc}"
