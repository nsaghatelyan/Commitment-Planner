"""Worker entrypoint: `arq app.workers.main.WorkerSettings`."""

from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.tasks import collect_usage, refresh_prices, run_analysis


class WorkerSettings:
    functions = (collect_usage, run_analysis, refresh_prices)
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
