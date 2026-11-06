"""认证相关"""
import asyncio
from datetime import datetime
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.audit import audit_note
from app.core.config import settings
from app.core.database import db
from app.core.security import (
    WEAK_DEFAULT_PASSWORDS,
    decode_token,
    hash_password,
    issue_token,
    validate_password,
    verify_password,
    verify_token,
)

router = APIRouter()


# 用户不存在时也做一次 bcrypt 校验，避免通过响应耗时枚举用户名
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode("utf-8")


def _lock_message(locked_until: datetime) -> str:
    minutes = max(1, int((locked_until - datetime.utcnow()).total_seconds() // 60) + 1)
    return f"登录失败次数过多，账号已锁定，请 {minutes} 分钟后再试或联系管理员解锁"


async def _record_login_failure(user_id: int) -> Optional[datetime]:
    """失败计数 +1；达到阈值时锁定并清零计数。返回锁定截止时间（未锁定为 None）"""
    row = await db.fetch_one(
        """
        UPDATE users SET
            locked_until = CASE WHEN failed_login_count + 1 >= $2
                                THEN (NOW() AT TIME ZONE 'UTC') + make_interval(mins => $3) ELSE locked_until END,
            failed_login_count = CASE WHEN failed_login_count + 1 >= $2 THEN 0 ELSE failed_login_count + 1 END
        WHERE id = $1
        RETURNING failed_login_count, locked_until
        """,
        user_id, settings.LOGIN_MAX_FAILURES, settings.LOGIN_LOCK_MINUTES,
    )
    if row and row["failed_login_count"] == 0 and row["locked_until"]:
        return row["locked_until"]
    return None


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    nickname: str
    email: Optional[str]
    role: str
    status: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request):
    """用户登录"""
    audit_note(http_request, username=request.username[:100])
    # 查询用户
    user = await db.fetch_one(
        "SELECT * FROM users WHERE username = $1 AND status = 'active'",
        request.username
    )
    
    if not user:
        await asyncio.to_thread(verify_password, request.password, _DUMMY_HASH)
        audit_note(http_request, reason="用户不存在或已禁用")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 锁定期内直接拒绝，不再校验密码
    locked_until = user.get("locked_until")
    audit_note(http_request, user_id=user["id"])
    if locked_until and locked_until > datetime.utcnow():
        audit_note(http_request, reason="账号锁定中")
        raise HTTPException(status_code=429, detail=_lock_message(locked_until))

    # 验证密码（bcrypt 较慢，放到线程中避免阻塞事件循环）
    if not await asyncio.to_thread(verify_password, request.password, user["password_hash"]):
        locked_until = await _record_login_failure(user["id"])
        if locked_until:
            audit_note(http_request, reason="密码错误，已触发锁定")
            raise HTTPException(status_code=429, detail=_lock_message(locked_until))
        audit_note(http_request, reason="密码错误")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    must_change = bool(user.get("must_change_password")) or request.password in WEAK_DEFAULT_PASSWORDS
    
    # 生成 token；需改密时签发受限 token（只能调用改密接口，15 分钟有效）
    token = issue_token(user, restricted=must_change)
    
    # 更新最后登录时间；历史 SHA-256 哈希登录成功后自动升级为 bcrypt
    if not str(user["password_hash"]).startswith("$2"):
        await db.execute(
            "UPDATE users SET last_login = NOW(), password_hash = $2, failed_login_count = 0, locked_until = NULL WHERE id = $1",
            user["id"], hash_password(request.password)
        )
    else:
        await db.execute(
            "UPDATE users SET last_login = NOW(), failed_login_count = 0, locked_until = NULL WHERE id = $1",
            user["id"]
        )
    
    return LoginResponse(
        token=token,
        must_change_password=must_change,
        user=UserResponse(
            id=user["id"],
            username=user["username"],
            nickname=user["nickname"],
            email=user["email"],
            role=user["role"],
            status=user["status"]
        )
    )


@router.post("/auth/change-password")
async def change_own_password(body: ChangePasswordRequest, payload: dict = Depends(decode_token)):
    """修改自己的密码（受限 token 也可调用）。其他设备上的登录全部失效，返回当前会话的新 token"""
    user = await db.fetch_one(
        "SELECT id, password_hash FROM users WHERE id = $1 AND status = 'active'",
        payload.get("user_id"),
    )
    if not user or not verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    new_password = validate_password(body.new_password)
    if new_password == body.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    if new_password in WEAK_DEFAULT_PASSWORDS:
        raise HTTPException(status_code=400, detail="不能使用默认密码")
    updated = await db.fetch_one(
        """
        UPDATE users SET password_hash=$1, must_change_password=FALSE,
                         token_version=token_version+1, updated_at=NOW()
        WHERE id=$2 RETURNING id, username, role, token_version
        """,
        hash_password(new_password), user["id"],
    )
    return {"status": "success", "token": issue_token(dict(updated))}


@router.get("/auth/me")
async def current_user(payload: dict = Depends(verify_token)):
    """当前登录用户、数据范围和系统启用状态（前端据此显示菜单与提示）"""
    from app.core.scope import site_scope
    from app.core.system_state import get_state

    user = await db.fetch_one(
        "SELECT id, username, nickname, email, role, status, data_scope, last_login FROM users WHERE id=$1",
        payload.get("user_id"),
    )
    scope = await site_scope(payload)
    sites = []
    if scope is not None and scope:
        sites = await db.fetch_all(
            "SELECT s.id, s.name, s.site_type FROM user_sites us JOIN sites s ON s.id = us.site_id "
            "WHERE us.user_id=$1 ORDER BY s.name",
            payload.get("user_id"),
        )
    state = await get_state()
    return {
        **dict(user),
        "data_scope": "all" if scope is None else "sites",
        "scope_sites": sites,
        "scope_site_count": None if scope is None else len(scope),
        "system_activated": bool(state.get("activated")),
    }
