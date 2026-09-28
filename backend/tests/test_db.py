from collections.abc import Iterator
from datetime import datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, MetaData, String, func, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

import jyj.models  # noqa: F401
from jyj.db import NAMING_CONVENTION, Base, TimestampMixin, get_db, session_scope
from jyj.main import create_app


class ProbeBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Probe(TimestampMixin, ProbeBase):
    __tablename__ = "test_probe"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


@pytest.fixture(scope="module", autouse=True)
def probe_table(db_engine: Engine) -> Iterator[None]:
    ProbeBase.metadata.create_all(db_engine)
    yield
    ProbeBase.metadata.drop_all(db_engine)


def count_probes(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Probe)) or 0


@pytest.mark.parametrize("run", [1, 2])
def test_each_test_starts_with_a_clean_database(db_session: Session, run: int) -> None:
    assert count_probes(db_session) == 0

    db_session.add(Probe(name=f"run-{run}"))
    db_session.commit()

    assert count_probes(db_session) == 1


def test_timestamps_are_server_set_and_timezone_aware(db_session: Session) -> None:
    probe = Probe(name="stamped")
    db_session.add(probe)
    db_session.flush()
    db_session.refresh(probe)

    assert isinstance(probe.created_at, datetime)
    assert probe.created_at.tzinfo is not None
    assert probe.updated_at.tzinfo is not None


def test_naming_convention_is_applied(db_session: Session) -> None:
    constraints = inspect(db_session.connection()).get_unique_constraints("test_probe")

    assert [c["name"] for c in constraints] == ["uq_test_probe_name"]


def test_session_scope_commits_on_success(db_connection: Connection) -> None:
    factory = sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint")

    for session in session_scope(factory):
        session.add(Probe(name="kept"))

    with factory() as check:
        assert count_probes(check) == 1


def test_session_scope_rolls_back_on_error(db_connection: Connection) -> None:
    factory = sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint")
    scope = session_scope(factory)
    session = next(scope)
    session.add(Probe(name="discarded"))
    session.flush()

    with pytest.raises(RuntimeError):
        scope.throw(RuntimeError("boom"))

    with factory() as check:
        assert count_probes(check) == 0


def test_migrations_round_trip(db_engine: Engine, alembic_config: Config) -> None:
    config = alembic_config
    head = ScriptDirectory.from_config(config).get_current_head()

    command.downgrade(config, "base")
    with db_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() is None

    command.upgrade(config, "head")
    with db_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == head


def test_models_match_migrations(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "include_name": lambda name, type_, _: name != Probe.__tablename__,
            },
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_readyz_ok_with_database(db_session: Session) -> None:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
