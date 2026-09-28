from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session, sessionmaker

from jyj import cli
from jyj.models.user import AuthSession, User
from jyj.services import auth as auth_service

PASSWORD = "correct horse battery"


@pytest.fixture
def factory(db_connection: Connection) -> Callable[[], Session]:
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db(factory: Callable[[], Session]) -> Iterator[Session]:
    with factory() as session:
        yield session


@pytest.fixture
def typed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    answers: list[str] = []
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": answers.pop(0))
    return answers


def run(factory: Callable[[], Session], *argv: str) -> int:
    return cli.main(list(argv), session_factory=factory)


def test_create_and_list(factory, db: Session, typed: list[str], capsys) -> None:
    typed += [PASSWORD, PASSWORD]

    assert run(factory, "users", "create", "Carol", "--display-name", "Carol C") == 0
    assert run(factory, "users", "list") == 0

    out = capsys.readouterr().out
    assert "carol\tCarol C\tactive" in out
    assert PASSWORD not in out
    user = auth_service.get_user(db, "carol")
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")


def test_password_is_not_accepted_as_argument(factory) -> None:
    with pytest.raises(SystemExit):
        run(factory, "users", "create", "dave", "--password", PASSWORD)


def test_create_rejects_mismatch_and_short_passwords(factory, db: Session, typed, capsys) -> None:
    typed += [PASSWORD, PASSWORD + "x", "short", "short"]

    assert run(factory, "users", "create", "erin") == 1
    assert run(factory, "users", "create", "erin") == 1
    assert auth_service.get_user(db, "erin") is None
    assert "do not match" in capsys.readouterr().err


def test_reset_password_revokes_sessions(factory, db: Session, typed) -> None:
    user = auth_service.create_user(db, "frank", None, PASSWORD)
    auth_service.create_session(db, user, None)
    db.commit()
    typed += ["another long password"] * 2

    assert run(factory, "users", "reset-password", "Frank") == 0

    db.expire_all()
    assert auth_service.authenticate(db, "frank", "another long password") is not None
    assert db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)).all() == []


def test_reset_password_for_unknown_user_does_not_prompt(factory, typed, capsys) -> None:
    assert run(factory, "users", "reset-password", "nobody") == 1
    assert "no user named" in capsys.readouterr().err


def test_disable(factory, db: Session) -> None:
    user = auth_service.create_user(db, "gina", None, PASSWORD)
    auth_service.create_session(db, user, None)
    db.commit()

    assert run(factory, "users", "disable", "gina") == 0

    db.expire_all()
    assert db.get(User, user.id).disabled_at is not None
    assert auth_service.authenticate(db, "gina", PASSWORD) is None
