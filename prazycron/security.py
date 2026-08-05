from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any


def make_password_record(password: str, iterations: int = 240_000) -> dict[str, Any]:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "algorithm": "pbkdf2-sha256",
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
    }


def verify_password(password: str, record: dict[str, Any] | None) -> bool:
    if not record:
        return True
    try:
        iterations = int(record["iterations"])
        salt = base64.b64decode(str(record["salt"]))
        expected = base64.b64decode(str(record["digest"]))
    except (KeyError, ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
