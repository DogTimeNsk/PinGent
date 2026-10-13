import pytest
from argon2 import PasswordHasher

from pingent.web_auth import hash_password


def test_hash_password_uses_argon2id() -> None:
    password_hash = hash_password("a sufficiently long password")

    assert password_hash.startswith("$argon2id$")
    assert PasswordHasher().verify(password_hash, "a sufficiently long password")


def test_hash_password_rejects_short_passwords() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")
