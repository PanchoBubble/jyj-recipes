"""Admin commands: `jyj users create|reset-password|disable|list` and `jyj chat purge`.

Passwords are only ever read with getpass, never from argv, so they stay out of shell
history and process listings.
"""

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from contextlib import contextmanager

from sqlalchemy.orm import Session

from jyj.chat import conversations
from jyj.db import get_sessionmaker, session_scope
from jyj.services import auth as auth_service


def _prompt_new_password() -> str:
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat password: "):
        raise auth_service.UserError("passwords do not match")
    auth_service.validate_password(password)
    return password


def _create(db: Session, args: argparse.Namespace) -> str:
    password = _prompt_new_password()
    user = auth_service.create_user(db, args.username, args.display_name, password)
    return f"created user {user.username!r}"


def _reset_password(db: Session, args: argparse.Namespace) -> str:
    auth_service.require_user(db, args.username)
    password = _prompt_new_password()
    user = auth_service.reset_password(db, args.username, password)
    return f"password reset for {user.username!r}; existing sessions revoked"


def _disable(db: Session, args: argparse.Namespace) -> str:
    user = auth_service.disable_user(db, args.username)
    return f"disabled {user.username!r}; existing sessions revoked"


def _list(db: Session, _: argparse.Namespace) -> str:
    rows = [
        f"{u.username}\t{u.display_name}\t{'disabled' if u.disabled_at else 'active'}"
        for u in auth_service.list_users(db)
    ]
    return "\n".join(rows) if rows else "no users"


def _purge_chat(db: Session, args: argparse.Namespace) -> str:
    result = conversations.purge(db, days=args.days)
    return (
        f"purged {result.conversations} conversations and {result.messages} messages "
        f"older than {args.days} days; closed {result.expired_proposals} pending proposals"
    )


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jyj")
    groups = parser.add_subparsers(dest="group", required=True)
    users = groups.add_parser("users", help="manage household accounts").add_subparsers(
        dest="command", required=True
    )

    create = users.add_parser("create", help="create an account (prompts for a password)")
    create.add_argument("username")
    create.add_argument("--display-name")
    create.set_defaults(handler=_create)

    reset = users.add_parser("reset-password", help="set a new password and revoke sessions")
    reset.add_argument("username")
    reset.set_defaults(handler=_reset_password)

    disable = users.add_parser("disable", help="disable an account and revoke sessions")
    disable.add_argument("username")
    disable.set_defaults(handler=_disable)

    users.add_parser("list", help="list accounts").set_defaults(handler=_list)

    chat = groups.add_parser("chat", help="chat assistant maintenance").add_subparsers(
        dest="command", required=True
    )
    purge = chat.add_parser("purge", help="delete chat text past the retention window")
    purge.add_argument(
        "--days", type=_positive_int, default=conversations.RETENTION_DAYS, help="default: 90"
    )
    purge.set_defaults(handler=_purge_chat)
    return parser


def main(
    argv: Sequence[str] | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        with contextmanager(session_scope)(session_factory or get_sessionmaker()) as db:
            message = args.handler(db, args)
        print(message)
    except auth_service.UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("aborted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
