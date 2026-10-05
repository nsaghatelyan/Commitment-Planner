from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+psycopg://savings:savings@localhost:5433/savings"
    redis_url: str = "redis://localhost:6380/0"
    # Root for Parquet usage files (local path now; S3 prefix when hosted).
    usage_data_dir: str = "./data/usage"


@lru_cache
def get_settings() -> Settings:
    return Settings()
