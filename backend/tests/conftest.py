import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://jyj:jyj@127.0.0.1:55432/jyj_test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
BACKEND_DIR = Path(__file__).resolve().parent.parent

# Point app settings at the test database before anything builds an engine, so a test
# can never touch the dev database by accident.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def db_engine(alembic_config: Config) -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 3})
    try:
        with engine.connect():
            pass
    except OperationalError:
        engine.dispose()
        safe_url = make_url(TEST_DATABASE_URL).render_as_string(hide_password=True)
        pytest.skip(
            f"test database unreachable at {safe_url}; "
            "start it with `docker compose --profile test up -d db-test` "
            "or set TEST_DATABASE_URL"
        )
    command.upgrade(alembic_config, "head")
    yield engine
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
