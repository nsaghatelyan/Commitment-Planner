import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from app.usage.storage import UsageStore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text())


def fixture_parquet(name: str):
    return pq.read_table(FIXTURES / name)


@pytest.fixture
def store(tmp_path) -> UsageStore:
    return UsageStore(str(tmp_path / "data"))


TEST_DB = "savings_test"


@pytest.fixture(scope="session")
def db_url():
    """A migrated scratch database on the docker-compose Postgres; skips if unreachable."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    from app.config import get_settings

    base = make_url(get_settings().database_url)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    except OperationalError as exc:  # pragma: no cover - depends on local docker
        pytest.skip(f"Postgres not reachable ({type(exc).__name__}); run docker compose up -d")
    finally:
        admin.dispose()
    url = base.set(database=TEST_DB).render_as_string(hide_password=False)
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "migrations"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def db_session(db_url):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    engine = create_engine(db_url)
    with Session(engine) as session:
        yield session
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tenant, price CASCADE"))
    engine.dispose()
