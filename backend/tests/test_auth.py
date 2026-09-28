from collections.abc import Callable, Iterator
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models.user import AuthSession, User
from jyj.services import auth as auth_service
from jyj.services.rate_limit import LoginRateLimiter

PASSWORD = "correct horse battery"
CSRF = {"X-Requested-With": "jyj"}


@pytest.fixture
def session_factory(db_connection: Connection) -> Callable[[], Session]:
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
    app = create_app()

    def test_db() -> Iterator[Session]:
        yield from session_scope(session_factory)

    app.dependency_overrides[get_db] = test_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def alice(db: Session) -> User:
    user = auth_service.create_user(db, "Alice", "Alice A.", PASSWORD)
    db.commit()
    return user


def login(client: TestClient, username: str = "alice", password: str = PASSWORD, **kw):
    headers = kw.pop("headers", CSRF)
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers=headers
    )


def session_rows(db: Session, user: User) -> list[AuthSession]:
    db.expire_all()
    return list(db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)))


def assert_problem(response, status: int) -> None:
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == status
    assert body["type"] == "about:blank"
    assert body["title"]


def test_login_success_sets_secure_cookie_and_me_works(client: TestClient, alice: User) -> None:
    response = login(client, username="ALICE")

    assert response.status_code == 200
    assert response.json() == {"id": alice.id, "username": "alice", "display_name": "Alice A."}
    cookie = SimpleCookie(response.headers["set-cookie"])[SESSION_COOKIE]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == "/"
    assert int(cookie["max-age"]) == 30 * 24 * 3600

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_token_is_hashed_at_rest(client: TestClient, db: Session, alice: User) -> None:
    login(client)
    token = client.cookies[SESSION_COOKIE]

    [row] = session_rows(db, alice)
    assert row.token_hash == auth_service.hash_token(token)
    assert token not in row.token_hash


def test_cookie_secure_flag_can_be_disabled_for_local_http(
    client: TestClient, alice: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jyj.config import get_settings

    monkeypatch.setattr(get_settings(), "session_cookie_secure", False)
    response = login(client)
    cookie = SimpleCookie(response.headers["set-cookie"])[SESSION_COOKIE]
    assert not cookie["secure"]


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "wrong password!"), ("nobody", PASSWORD)],
    ids=["wrong-password", "unknown-user"],
)
def test_bad_credentials_get_the_same_generic_error(
    client: TestClient, alice: User, username: str, password: str
) -> None:
    response = login(client, username=username, password=password)

    assert_problem(response, 401)
    assert response.json()["detail"] == "Invalid username or password"
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_unknown_user_still_runs_a_password_hash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = auth_service.passwords.verify_password

    def spy(password_hash: str, password: str) -> bool:
        calls.append(password_hash)
        return real(password_hash, password)

    monkeypatch.setattr(auth_service.passwords, "verify_password", spy)
    login(client, username="ghost")
    assert len(calls) == 1


def test_disabled_user_cannot_log_in_and_loses_sessions(
    client: TestClient, db: Session, alice: User
) -> None:
    login(client)
    auth_service.disable_user(db, "alice")
    db.commit()

    assert_problem(client.get("/api/v1/auth/me"), 401)
    client.cookies.clear()
    assert_problem(login(client), 401)


def test_disabled_user_with_surviving_session_is_rejected(
    client: TestClient, db: Session, alice: User
) -> None:
    login(client)
    alice_row = db.get(User, alice.id)
    alice_row.disabled_at = auth_service.utcnow()
    db.commit()

    assert_problem(client.get("/api/v1/auth/me"), 401)


def test_me_without_cookie_is_401_problem(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert_problem(response, 401)


def test_expired_session_is_rejected(client: TestClient, db: Session, alice: User) -> None:
    login(client)
    [row] = session_rows(db, alice)
    row.expires_at = auth_service.utcnow() - timedelta(seconds=1)
    db.commit()

    assert_problem(client.get("/api/v1/auth/me"), 401)


def test_revoked_session_is_rejected(client: TestClient, db: Session, alice: User) -> None:
    login(client)
    auth_service.revoke_user_sessions(db, alice)
    db.commit()

    assert_problem(client.get("/api/v1/auth/me"), 401)


def test_logout_revokes_session_and_clears_cookie(
    client: TestClient, db: Session, alice: User
) -> None:
    login(client)
    token = client.cookies[SESSION_COOKIE]

    response = client.post("/api/v1/auth/logout", headers=CSRF)

    assert response.status_code == 204
    assert 'jyj_session=""' in response.headers["set-cookie"]
    assert session_rows(db, alice) == []
    client.cookies.set(SESSION_COOKIE, token)
    assert_problem(client.get("/api/v1/auth/me"), 401)


def test_session_slides_at_most_hourly(client: TestClient, db: Session, alice: User) -> None:
    login(client)
    [row] = session_rows(db, alice)
    first_expiry = row.expires_at

    fresh = client.get("/api/v1/auth/me")
    assert "set-cookie" not in fresh.headers
    assert session_rows(db, alice)[0].expires_at == first_expiry

    row = session_rows(db, alice)[0]
    row.last_seen_at -= timedelta(hours=2)
    row.expires_at -= timedelta(hours=2)
    db.commit()

    stale = client.get("/api/v1/auth/me")
    assert stale.status_code == 200
    assert SESSION_COOKIE in stale.headers["set-cookie"]
    assert session_rows(db, alice)[0].expires_at > first_expiry


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/api/v1/auth/login"), ("post", "/api/v1/auth/logout"), ("delete", "/api/v1/x")],
)
@pytest.mark.parametrize("headers", [{}, {"X-Requested-With": "XMLHttpRequest"}])
def test_mutations_without_csrf_header_are_403(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    response = client.request(method, path, json={}, headers=headers)
    assert_problem(response, 403)


def test_logout_without_csrf_header_keeps_session(
    client: TestClient, db: Session, alice: User
) -> None:
    login(client)
    assert_problem(client.post("/api/v1/auth/logout"), 403)
    assert len(session_rows(db, alice)) == 1


def test_ip_rate_limit_blocks_sixth_attempt(client: TestClient, alice: User) -> None:
    for i in range(5):
        assert login(client, username=f"user{i}", password="x").status_code == 401

    response = login(client)

    assert_problem(response, 429)
    assert int(response.headers["retry-after"]) > 0


def test_validation_errors_never_echo_the_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": "", "password": "hunter2-secret"}, headers=CSRF
    )
    assert_problem(response, 422)
    assert "hunter2-secret" not in response.text


def test_rehashes_when_parameters_change(client: TestClient, db: Session) -> None:
    user = auth_service.create_user(db, "bob", None, PASSWORD)
    user.password_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    db.commit()
    old_hash = user.password_hash

    assert login(client, username="bob").status_code == 200

    db.expire_all()
    new_hash = db.get(User, user.id).password_hash
    assert new_hash != old_hash
    assert not auth_service.passwords.needs_rehash(new_hash)


def test_usernames_are_unique_case_insensitively(db: Session, alice: User) -> None:
    with pytest.raises(auth_service.UserError):
        auth_service.create_user(db, "ALICE", None, PASSWORD)
    assert db.scalar(select(func.count()).select_from(User)) == 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_ip_window_slides() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock)
    for _ in range(5):
        assert limiter.acquire("1.2.3.4", "a") == 0
    assert limiter.acquire("1.2.3.4", "a") > 0
    assert limiter.acquire("5.6.7.8", "a") == 0
    clock.now += 60
    assert limiter.acquire("1.2.3.4", "a") == 0


def test_limiter_username_backoff_is_exponential_and_resets() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(clock=clock, ip_limit=1000)
    for _ in range(3):
        assert limiter.acquire("ip", "alice") == 0
        limiter.record_failure("alice")
    assert limiter.acquire("other-ip", "alice") == pytest.approx(2)
    assert limiter.acquire("ip", "bob") == 0

    clock.now += 2
    assert limiter.acquire("ip", "alice") == 0
    limiter.record_failure("alice")
    assert limiter.acquire("ip", "alice") == pytest.approx(4)

    clock.now += 4
    limiter.record_success("alice")
    assert limiter.acquire("ip", "alice") == 0


def test_limiter_memory_is_bounded() -> None:
    limiter = LoginRateLimiter(clock=FakeClock(), max_keys=100, ip_limit=10**6)
    for i in range(1000):
        limiter.acquire("ip", f"u{i}")
        limiter.record_failure(f"u{i}")
    assert len(limiter._users) <= 100


def test_username_backoff_applies_through_the_api(client: TestClient, alice: User) -> None:
    limiter: LoginRateLimiter = client.app.state.login_limiter
    limiter.ip_limit = 1000
    for _ in range(3):
        assert login(client, password="wrong password!").status_code == 401

    assert_problem(login(client), 429)


def test_insecure_cookie_is_refused_in_production() -> None:
    from pydantic import ValidationError

    from jyj.config import Settings

    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        Settings(
            app_env="production",
            database_url="postgresql+psycopg://u:p@db/jyj",
            session_secret="s" * 40,
            session_cookie_secure=False,
        )
