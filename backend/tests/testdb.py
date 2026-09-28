"""Per-run throwaway databases on the shared test Postgres server.

Each pytest run gets its own `<base>_<unix ts>_<pid>_<hex>` database so concurrent runs
never see each other's schema. Run as a script to prune databases left by killed runs:

    uv run python tests/testdb.py prune [--older-than-hours 3]
"""

import argparse
import os
import re
import secrets
import sys
import time

from sqlalchemy import URL, Engine, create_engine, make_url, text

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://jyj:jyj@127.0.0.1:55432/jyj_test"
PRUNE_AFTER_HOURS = 3.0


def base_url() -> URL:
    return make_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))


def reuse_requested() -> bool:
    return os.environ.get("TEST_DATABASE_REUSE", "").strip().lower() in {"1", "true", "yes"}


def run_database_name(base: str) -> str:
    return f"{base}_{int(time.time())}_{os.getpid()}_{secrets.token_hex(3)}"


def _run_name_pattern(base: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(base)}_(\d+)_\d+_[0-9a-f]+$")


def admin_engine(url: URL) -> Engine:
    return create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})


def _quote(engine: Engine, name: str) -> str:
    return engine.dialect.identifier_preparer.quote(name)


def create_database(admin: Engine, name: str) -> None:
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {_quote(admin, name)}"))


def drop_database(admin: Engine, name: str) -> None:
    with admin.connect() as connection:
        version = connection.execute(text("SHOW server_version_num")).scalar_one()
        force = " WITH (FORCE)" if int(version) >= 130000 else ""
        connection.execute(text(f"DROP DATABASE IF EXISTS {_quote(admin, name)}{force}"))


def prune(url: URL, older_than_hours: float = PRUNE_AFTER_HOURS) -> list[str]:
    """Drop per-run databases for this URL's base name created more than N hours ago."""
    pattern = _run_name_pattern(url.database or "")
    cutoff = time.time() - older_than_hours * 3600
    admin = admin_engine(url)
    try:
        with admin.connect() as connection:
            names = connection.execute(text("SELECT datname FROM pg_database")).scalars().all()
        stale = [
            name
            for name in names
            if (match := pattern.match(name)) and int(match.group(1)) < cutoff
        ]
        for name in stale:
            drop_database(admin, name)
        return stale
    finally:
        admin.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    prune_parser = sub.add_parser("prune", help="drop leftover per-run test databases")
    prune_parser.add_argument("--older-than-hours", type=float, default=PRUNE_AFTER_HOURS)
    args = parser.parse_args(argv)

    url = base_url()
    dropped = prune(url, args.older_than_hours)
    safe_url = url.render_as_string(hide_password=True)
    for name in dropped:
        print(f"dropped {name}")
    print(f"pruned {len(dropped)} database(s) on {safe_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
