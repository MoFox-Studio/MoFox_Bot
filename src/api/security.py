from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.common.logger import get_logger
from src.config.config import global_config


@dataclass(frozen=True)
class JWTCredentials:
    """已验证的JWT凭据"""

    subject: str
    scopes: list[str]
    issued_at: datetime
    expires_at: datetime | None
    token: str
    payload: Dict[str, Any]


_logger = get_logger("api.security")
_http_bearer = HTTPBearer(auto_error=False)


def _load_allowed_keys() -> set[str]:
    keys: set[str] = set()

    env_keys = os.getenv("API_ALLOWED_KEYS")
    if env_keys:
        keys.update(key.strip() for key in env_keys.split(",") if key.strip())

    try:
        config_tokens = getattr(global_config.maim_message, "auth_token", []) or []
    except AttributeError:
        config_tokens = []

    keys.update(str(token) for token in config_tokens if str(token).strip())
    return keys


ALLOWED_API_KEYS: set[str] = _load_allowed_keys()


def _load_secret() -> str:
    env_secret = os.getenv("API_JWT_SECRET")
    if env_secret:
        return env_secret

    try:
        debug_secret = getattr(global_config.debug, "api_jwt_secret", None)
    except AttributeError:
        debug_secret = None

    if debug_secret:
        return str(debug_secret)

    if ALLOWED_API_KEYS:
        return next(iter(ALLOWED_API_KEYS))

    return "change-me"


def _parse_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


JWT_SECRET = _load_secret()
JWT_ALGORITHM = os.getenv("API_JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.getenv("API_JWT_ISSUER", "mofox-bot")
JWT_AUDIENCE = os.getenv("API_JWT_AUDIENCE")
ACCESS_TOKEN_EXPIRE_MINUTES = _parse_int(os.getenv("API_JWT_EXPIRE_MINUTES", "60"), 60)

if JWT_SECRET == "change-me":  # pragma: no cover - runtime warning
    _logger.warning("JWT秘钥未配置，使用默认值，请尽快更换以确保安全")


def is_api_key_configured() -> bool:
    return bool(ALLOWED_API_KEYS)


def validate_api_key(api_key: str) -> bool:
    if not ALLOWED_API_KEYS:
        return False
    return api_key in ALLOWED_API_KEYS


def _build_payload(
    subject: str,
    scopes: Iterable[str] | None,
    expires_delta: Optional[timedelta],
    extra_claims: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], datetime]:
    now = datetime.now(timezone.utc)
    expires = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    scope_list = list(scopes or [])
    payload: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": JWT_ISSUER,
    }

    if JWT_AUDIENCE:
        payload["aud"] = JWT_AUDIENCE

    if scope_list:
        payload["scope"] = " ".join(scope_list)

    if extra_claims:
        payload.update(extra_claims)

    return payload, expires


def create_access_token(
    subject: str,
    scopes: Iterable[str] | None = None,
    expires_delta: Optional[timedelta] = None,
    *,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> tuple[str, datetime]:
    payload, expires = _build_payload(subject, scopes, expires_delta, extra_claims)
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires


def decode_access_token(token: str) -> JWTCredentials:
    options = {"verify_aud": bool(JWT_AUDIENCE)}
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options=options,
        )
    except jwt.InvalidTokenError as exc:
        _logger.warning(f"JWT验证失败: {exc}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的访问令牌") from exc

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌缺少subject")

    scope_value = payload.get("scope") or payload.get("scopes") or []
    if isinstance(scope_value, str):
        scopes = [scope for scope in scope_value.split() if scope]
    elif isinstance(scope_value, (list, tuple, set)):
        scopes = [str(scope) for scope in scope_value]
    else:
        scopes = []

    issued_at_raw = payload.get("iat")
    issued_at = datetime.fromtimestamp(issued_at_raw, tz=timezone.utc) if issued_at_raw else datetime.now(timezone.utc)
    expires_raw = payload.get("exp")
    expires_at = datetime.fromtimestamp(expires_raw, tz=timezone.utc) if expires_raw else None

    return JWTCredentials(
        subject=str(subject),
        scopes=scopes,
        issued_at=issued_at,
        expires_at=expires_at,
        token=token,
        payload=payload,
    )


async def get_current_credentials(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> JWTCredentials:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证信息")

    return decode_access_token(credentials.credentials)


def require_scope(scope: str):
    async def _dependency(token: JWTCredentials = Depends(get_current_credentials)) -> JWTCredentials:
        if "*" in token.scopes or scope in token.scopes:
            return token
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少访问权限")

    return _dependency


__all__ = [
    "ALLOWED_API_KEYS",
    "JWTCredentials",
    "create_access_token",
    "decode_access_token",
    "get_current_credentials",
    "is_api_key_configured",
    "require_scope",
    "validate_api_key",
]
