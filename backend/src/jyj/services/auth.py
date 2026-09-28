import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jyj.models.user import AuthSession, User
from jyj.services import passwords

SESSION_TTL = timedelta(days=30)
SESSION_REFRESH_INTERVAL = timedelta(hours=1)
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class UserError(ValueError):
    pass


@dataclass(frozen=True)
class IssuedSession:
    token: str
    session: AuthSession


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_user(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == normalize_username(username)))


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_user(db, username)
    if user is None:
        passwords.burn_verify(password)
        return None
    if not passwords.verify_password(user.password_hash, password) or not user.is_active:
        return None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
        db.flush()
    return user


def create_session(db: Session, user: User, user_agent: str | None) -> IssuedSession:
    now = utcnow()
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        token_hash=hash_token(token),
        user=user,
        created_at=now,
        expires_at=now + SESSION_TTL,
        last_seen_at=now,
        user_agent=user_agent[:512] if user_agent else None,
    )
    db.add(session)
    db.flush()
    return IssuedSession(token=token, session=session)


def resolve_session(db: Session, token: str) -> tuple[AuthSession, bool] | None:
    """Look up a live session for a cookie token.

    Returns the session and whether its expiry slid forward (so the cookie should be
    re-issued), or None if the token is unknown, expired or belongs to a disabled user.
    """
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    now = utcnow()
    if session is None or session.expires_at <= now or not session.user.is_active:
        return None
    if now - session.last_seen_at < SESSION_REFRESH_INTERVAL:
        return session, False
    session.last_seen_at = now
    session.expires_at = now + SESSION_TTL
    db.flush()
    return session, True


def revoke_session(db: Session, token: str) -> None:
    db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))


def revoke_user_sessions(db: Session, user: User) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))


def validate_password(password: str) -> None:
    if len(password) < passwords.MIN_PASSWORD_LENGTH:
        raise UserError(f"password must be at least {passwords.MIN_PASSWORD_LENGTH} characters")
    if len(password) > passwords.MAX_PASSWORD_LENGTH:
        raise UserError(f"password must be at most {passwords.MAX_PASSWORD_LENGTH} characters")


def create_user(db: Session, username: str, display_name: str | None, password: str) -> User:
    name = normalize_username(username)
    if not USERNAME_RE.match(name):
        raise UserError("username must be 1-64 chars of a-z, 0-9, '.', '_' or '-'")
    if get_user(db, name) is not None:
        raise UserError(f"user {name!r} already exists")
    validate_password(password)
    user = User(
        username=name,
        display_name=(display_name or username).strip()[:100],
        password_hash=passwords.hash_password(password),
    )
    db.add(user)
    db.flush()
    return user


def require_user(db: Session, username: str) -> User:
    user = get_user(db, username)
    if user is None:
        raise UserError(f"no user named {normalize_username(username)!r}")
    return user


def reset_password(db: Session, username: str, password: str) -> User:
    user = require_user(db, username)
    validate_password(password)
    user.password_hash = passwords.hash_password(password)
    revoke_user_sessions(db, user)
    db.flush()
    return user


def disable_user(db: Session, username: str) -> User:
    user = require_user(db, username)
    if user.disabled_at is None:
        user.disabled_at = utcnow()
    revoke_user_sessions(db, user)
    db.flush()
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.username)))
