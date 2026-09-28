from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from jyj.db import get_db, session_scope
from jyj.main import create_app

UNREACHABLE_URL = "postgresql+psycopg://jyj:hunter2@127.0.0.1:1/nope"


def test_readyz_hides_database_errors() -> None:
    engine = create_engine(UNREACHABLE_URL, connect_args={"connect_timeout": 1})
    factory = sessionmaker(bind=engine)

    def broken_db() -> Iterator[Session]:
        yield from session_scope(factory)

    app = create_app()
    app.dependency_overrides[get_db] = broken_db

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    engine.dispose()


def test_healthz_does_not_touch_database() -> None:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(AssertionError("no db"))

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
