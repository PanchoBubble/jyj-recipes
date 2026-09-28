import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from testdb import (
    admin_engine,
    base_url,
    create_database,
    drop_database,
    reuse_requested,
    run_database_name,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent

BASE_URL = base_url()
REUSE = reuse_requested()
RUN_URL = BASE_URL if REUSE else BASE_URL.set(database=run_database_name(BASE_URL.database or ""))
TEST_DATABASE_URL = RUN_URL.render_as_string(hide_password=False)

# Point app settings at this run's database before anything builds an engine, so a test
# can never touch the dev database (or another run's database) by accident.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def _clear_app_caches() -> None:
    from jyj.config import get_settings
    from jyj.db import get_engine, get_sessionmaker

    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def test_database() -> Iterator[str]:
    """Create this run's database on the test server and drop it afterwards."""
    admin = admin_engine(BASE_URL)
    try:
        with admin.connect():
            pass
    except OperationalError:
        admin.dispose()
        safe_url = BASE_URL.render_as_string(hide_password=True)
        pytest.skip(
            f"test database unreachable at {safe_url}; "
            "start it with `docker compose --profile test up -d db-test` "
            "or set TEST_DATABASE_URL"
        )
    name = RUN_URL.database or ""
    if not REUSE:
        create_database(admin, name)
    _clear_app_caches()
    try:
        yield name
    finally:
        _clear_app_caches()
        if not REUSE:
            drop_database(admin, name)
        admin.dispose()


@pytest.fixture(scope="session")
def db_engine(test_database: str, alembic_config: Config) -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 3})
    try:
        command.upgrade(alembic_config, "head")
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    """A session whose commits only release savepoints; everything rolls back after the test."""
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
