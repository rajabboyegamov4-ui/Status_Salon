"""Password hashing and session tokens (clients, staff, platform admin)."""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt

from app.config import settings
from app.services.availability import now_utc

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        salt, digest = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    candidate = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return hmac.compare_digest(candidate, digest)


# A token says who you are (kind + id) and, for clients and staff, which salon you belong to.
def issue_token(kind: str, subject_id: int, salon_id: int | None, days: int | None = None, version: int = 0) -> str:
    payload = {
        "kind": kind,
        "sub": str(subject_id),
        "salon": salon_id,
        "ver": version,
        "exp": now_utc() + timedelta(days=days or settings.jwt_ttl_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
