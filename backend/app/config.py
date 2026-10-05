from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+psycopg://savings:savings@localhost:5433/savings"
    redis_url: str = "redis://localhost:6380/0"

    # Root for normalized Parquet usage: a local path in dev, s3://bucket/prefix in prod.
    usage_storage_root: str = "./data/usage"
    # Cache for paid / rate-limited API responses (Cost Explorer, Cost Management Query).
    api_cache_dir: str = "./data/api-cache"
    api_cache_ttl_hours: int = 24

    # AWS: our own account. Clients' roles are assumed from these credentials.
    aws_region: str = "us-east-1"
    aws_role_session_name: str = "savings-tool"
    # The tool's AWS account ID, shown in the client's CloudFormation command (role mode).
    tool_aws_account_id: str = ""

    # Azure: our multi-tenant Entra app, authenticating with a certificate.
    azure_app_client_id: str = ""
    azure_app_certificate_path: str = ""

    # How far back the API bootstrap goes while the client's billing export fills.
    bootstrap_lookback_months: int = 13


@lru_cache
def get_settings() -> Settings:
    return Settings()
