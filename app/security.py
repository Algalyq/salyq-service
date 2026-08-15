import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.config import settings


def generate_challenge() -> str:
    return secrets.token_hex(32)


def create_access_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    to_encode = {**data, "exp": expire, "type": "access"}
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    to_encode = {**data, "exp": expire, "type": "refresh"}
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
