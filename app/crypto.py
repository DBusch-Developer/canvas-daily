"""Symmetric encryption for access tokens at rest.

The key comes from TOKEN_ENCRYPTION_KEY in the environment (a Fernet key).
Plaintext tokens are never written to the database — only ciphertext — and
the key is never logged.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet


@lru_cache(maxsize=1)
def _fernet():
    key = os.environ["TOKEN_ENCRYPTION_KEY"]
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
