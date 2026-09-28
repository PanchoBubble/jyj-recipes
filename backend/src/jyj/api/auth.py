import logging
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from jyj.config import get_settings
from jyj.db import get_db
from jyj.models.user import AuthSession, User
from jyj.services import auth as auth_service
from jyj.services.passwords import MAX_PASSWORD_LENGTH
from jyj.services.rate_limit import LoginRateLimiter

logger = logging.getLogger(__name__)

SESSION_COOKIE = "jyj_session"
INVALID_CREDENTIALS = "Invalid username or password"

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(auth_service.SESSION_TTL.total_seconds()),
        path="/",
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")


def get_login_limiter(request: Request) -> LoginRateLimiter:
    return request.app.state.login_limiter


def current_session(
    request: Request, response: Response, db: Annotated[Session, Depends(get_db)]
) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized()
    resolved = auth_service.resolve_session(db, token)
    if resolved is None:
        raise _unauthorized()
    session, refreshed = resolved
    if refreshed:
        _set_session_cookie(response, token)
    return session


def current_user(session: Annotated[AuthSession, Depends(current_session)]) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(current_user)]


@router.post("/login", response_model=UserOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    limiter: Annotated[LoginRateLimiter, Depends(get_login_limiter)],
) -> User:
    username = auth_service.normalize_username(body.username)
    client_ip = request.client.host if request.client else "unknown"
    retry_after = limiter.acquire(client_ip, username)
    if retry_after > 0:
        logger.info("login throttled")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts, try again later",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )
    user = auth_service.authenticate(db, username, body.password)
    if user is None:
        limiter.record_failure(username)
        logger.info("login failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    limiter.record_success(username)
    issued = auth_service.create_session(db, user, request.headers.get("user-agent"))
    _set_session_cookie(response, issued.token)
    logger.info("login ok user_id=%s", user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Annotated[Session, Depends(get_db)]) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth_service.revoke_session(db, token)
    _clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
