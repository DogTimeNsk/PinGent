"""Password handling for optional local Web SPA accounts."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

MINIMUM_PASSWORD_LENGTH = 12
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MINIMUM_PASSWORD_LENGTH} characters")
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerificationError:
        return False


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
