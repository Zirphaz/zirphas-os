"""Argon2 password hashing and short-lived, versioned access tokens."""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings
from app.models.enums import Role

ALGORITHM = "HS256"
TOKEN_ISSUER = "zirphas-os"
TOKEN_AUDIENCE = "zirphas-os-api"
password_hasher = PasswordHash.recommended()
# Run the same password verification even when an account does not exist.
DUMMY_PASSWORD_HASH = password_hasher.hash("unused-login-timing-placeholder")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except (ValueError, UnknownHashError):
        return False


def create_access_token(user_id: int, role: Role, token_version: int = 0) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "ver": token_version,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        get_settings().secret_key,
        algorithms=[ALGORITHM],
        issuer=TOKEN_ISSUER,
        audience=TOKEN_AUDIENCE,
        options={"require": ["sub", "exp", "iat", "iss", "aud", "ver", "type"]},
    )
    if (
        payload["type"] != "access"
        or not isinstance(payload["sub"], str)
        or not payload["sub"].isdigit()
        or int(payload["sub"]) <= 0
        or type(payload["ver"]) is not int
        or payload["ver"] < 0
    ):
        raise jwt.InvalidTokenError("Invalid access token claims")
    return payload
