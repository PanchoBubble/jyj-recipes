import pytest
from pydantic import ValidationError

from jyj.config import DEV_DATABASE_URL, Settings

PROD_URL = "postgresql+psycopg://jyj:secret@db:5432/jyj"
STRONG_SECRET = "x" * 40


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "SESSION_SECRET", "DB_ECHO"):
        monkeypatch.delenv(name, raising=False)


def make_settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_development_defaults() -> None:
    settings = make_settings()

    assert settings.app_env == "development"
    assert settings.database_url == DEV_DATABASE_URL
    assert settings.db_echo is False
    assert not settings.is_production


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", PROD_URL)
    monkeypatch.setenv("SESSION_SECRET", STRONG_SECRET)
    monkeypatch.setenv("CODEX_TIMEOUT_SECONDS", "30")

    settings = make_settings()

    assert settings.is_production
    assert settings.database_url == PROD_URL
    assert settings.session_secret.get_secret_value() == STRONG_SECRET
    assert settings.codex_timeout_seconds == 30


def test_secrets_are_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PROD_URL)
    monkeypatch.setenv("SESSION_SECRET", STRONG_SECRET)

    rendered = repr(make_settings())

    assert "secret@" not in rendered
    assert STRONG_SECRET not in rendered


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"SESSION_SECRET": STRONG_SECRET}, "DATABASE_URL must be set"),
        ({"DATABASE_URL": PROD_URL}, "SESSION_SECRET must be set"),
        ({"DATABASE_URL": PROD_URL, "SESSION_SECRET": "change-me"}, "placeholder"),
        (
            {
                "DATABASE_URL": "postgresql+psycopg://jyj:change-me@db:5432/jyj",
                "SESSION_SECRET": STRONG_SECRET,
            },
            "placeholder POSTGRES_PASSWORD",
        ),
        ({"DATABASE_URL": PROD_URL, "SESSION_SECRET": "short"}, "at least 32"),
    ],
)
def test_production_fails_fast(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=message):
        make_settings()


def test_rejects_unknown_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        make_settings()


def test_photo_search_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PHOTO_SEARCH_PROVIDER", "PHOTO_SEARCH_TIMEOUT_SECONDS", "PEXELS_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert make_settings().photo_search_provider == "auto"

    monkeypatch.setenv("PHOTO_SEARCH_PROVIDER", "openverse")
    monkeypatch.setenv("PEXELS_TIMEOUT_SECONDS", "7")
    settings = make_settings()
    assert settings.photo_search_provider == "openverse"
    assert settings.photo_search_timeout_seconds == 7

    monkeypatch.setenv("PHOTO_SEARCH_PROVIDER", "unsplash")
    with pytest.raises(ValidationError):
        make_settings()
