from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024

hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return hasher.check_needs_rehash(password_hash)


@lru_cache
def _dummy_hash() -> str:
    return hasher.hash("dummy password for unknown users")


def burn_verify(password: str) -> None:
    """Spend the same time as a real check so unknown usernames are not distinguishable."""
    verify_password(_dummy_hash(), password)
