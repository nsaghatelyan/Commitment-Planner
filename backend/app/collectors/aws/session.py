"""Assume a client's read-only role (with ExternalId) using auto-refreshing credentials."""

from datetime import UTC, datetime

import boto3
from botocore.credentials import RefreshableCredentials
from botocore.session import get_session


def assumed_role_session(
    role_arn: str,
    external_id: str,
    session_name: str,
    region: str,
    base_session: boto3.Session | None = None,
) -> boto3.Session:
    base = base_session or boto3.Session()
    sts = base.client("sts", region_name=region)

    def refresh() -> dict[str, str]:
        creds = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            ExternalId=external_id,
            DurationSeconds=3600,
        )["Credentials"]
        expiry = creds["Expiration"]
        if isinstance(expiry, datetime) and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["SessionToken"],
            "expiry_time": expiry.isoformat(),
        }

    credentials = RefreshableCredentials.create_from_metadata(
        metadata=refresh(), refresh_using=refresh, method="sts-assume-role"
    )
    botocore_session = get_session()
    botocore_session._credentials = credentials
    botocore_session.set_config_variable("region", region)
    return boto3.Session(botocore_session=botocore_session)
