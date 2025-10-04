from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from src.api.security import (
    JWTCredentials,
    create_access_token,
    decode_access_token,
    is_api_key_configured,
    require_scope,
    validate_api_key,
)
from src.common.logger import get_logger

router = APIRouter()

_logger = get_logger("api.auth")
_WEBSOCKET_SCOPE = "ws:connect"


class TokenRequest(BaseModel):
    api_key: str = Field(..., min_length=1, description="预共享的API密钥，用于颁发JWT")
    subject: str | None = Field(default=None, description="令牌主体，默认值为 'api-client'")
    scopes: list[str] = Field(default_factory=list, description="访问范围列表")
    expires_in_minutes: int | None = Field(default=None, ge=1, description="令牌有效期(分钟)")
    claims: dict[str, Any] = Field(default_factory=dict, description="附加的JWT自定义字段")


@router.post("/auth/token")
async def issue_token(request: TokenRequest) -> dict[str, Any]:
    if not is_api_key_configured():
        _logger.error("拒绝签发令牌，系统未配置任何API密钥")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="未配置API密钥")

    if not validate_api_key(request.api_key):
        _logger.warning("拒绝签发令牌，提供的API密钥无效")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的API密钥")

    subject = request.subject or "api-client"
    expires_delta = timedelta(minutes=request.expires_in_minutes) if request.expires_in_minutes else None
    token, expires_at = create_access_token(
        subject=subject,
        scopes=request.scopes,
        expires_delta=expires_delta,
        extra_claims=request.claims or None,
    )

    scope_text = " ".join(request.scopes) if request.scopes else "<none>"
    _logger.info(f"已签发JWT，subject={subject} scopes={scope_text}")

    issued_at = datetime.now(timezone.utc)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "issued_at": issued_at.isoformat(),
        "scopes": request.scopes,
        "subject": subject,
    }


@router.get("/auth/me")
async def read_current_token(credentials: JWTCredentials = Depends(require_scope("profile:read"))) -> dict[str, Any]:
    return {
        "subject": credentials.subject,
        "scopes": credentials.scopes,
        "issued_at": credentials.issued_at.isoformat(),
        "expires_at": credentials.expires_at.isoformat() if credentials.expires_at else None,
        "payload": credentials.payload,
    }


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket, token: str) -> None:
    try:
        credentials = decode_access_token(token)
    except HTTPException as exc:  # pragma: no cover - runtime behaviour
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc.detail))
        return

    if _WEBSOCKET_SCOPE not in credentials.scopes and "*" not in credentials.scopes:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="缺少WebSocket权限")
        _logger.warning(f"拒绝WebSocket连接，subject={credentials.subject} 缺少scope")
        return

    await websocket.accept()
    _logger.info(f"WebSocket连接已建立，subject={credentials.subject}")

    await websocket.send_json(
        {
            "event": "connected",
            "subject": credentials.subject,
            "scopes": credentials.scopes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    try:
        while True:
            payload = await websocket.receive_text()
            await websocket.send_json(
                {
                    "event": "echo",
                    "subject": credentials.subject,
                    "message": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
    except WebSocketDisconnect:
        _logger.info(f"WebSocket连接已关闭，subject={credentials.subject}")
    except Exception as exc:  # pragma: no cover - runtime behaviour
        _logger.exception(f"WebSocket通信异常: {exc}")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="服务器内部错误")
