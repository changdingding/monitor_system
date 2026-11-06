"""
认证与权限公共组件：密码哈希、JWT 解码、角色校验依赖
"""
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException

from app.core.config import settings
from app.core.database import db

# JWT 配置（密钥来自环境变量 JWT_SECRET，启动时在 main.py 校验）
JWT_ALGORITHM = "HS256"

ROLES = ("admin", "operator", "viewer")

# 出厂默认 / 公开的弱密码：用它登录时强制修改
WEAK_DEFAULT_PASSWORDS = {"admin123"}


def hash_password(password: str) -> str:
    """bcrypt 加盐哈希"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored: str) -> bool:
    """校验密码，兼容历史无盐 SHA-256 哈希"""
    if not stored:
        return False
    if stored.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except ValueError:
            return False
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored)


def validate_password(password: Optional[str]) -> str:
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少 8 位")
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="密码过长（最多 72 字节）")
    return password


def issue_token(user: dict, restricted: bool = False) -> str:
    """签发 JWT。tv = users.token_version，改密 / 重置密码后递增，旧 token 随即失效。
    restricted：需改密时签发的受限 token，只能调用改密接口，15 分钟有效"""
    claims = {
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "tv": int(user.get("token_version") or 0),
        "exp": datetime.utcnow() + (timedelta(minutes=15) if restricted
                                    else timedelta(hours=settings.JWT_EXPIRE_HOURS)),
    }
    if restricted:
        claims["pwd_change"] = True
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


async def decode_token(authorization: str = Header(...)) -> dict:
    """
    解码 JWT 并到数据库核对账号（不检查是否需要改密，仅供改密接口直接使用）。
    账号被删除 / 停用、或改密后 token_version 变化时立即失效；角色以数据库为准，降级即时生效。
    """
    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await db.fetch_one(
        "SELECT id, username, role, status, token_version FROM users WHERE id = $1",
        payload.get("user_id"),
    )
    if (not user or user["status"] != "active"
            or int(user["token_version"] or 0) != int(payload.get("tv") or 0)):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    payload["role"] = user["role"]
    payload["username"] = user["username"]
    return payload


def verify_token(payload: dict = Depends(decode_token)) -> dict:
    """验证 JWT token；需改密的账号只能调用改密接口"""
    if payload.get("pwd_change"):
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    return payload


def require_roles(*roles: str):
    """角色校验依赖：token 中的 role 必须在允许列表内"""
    def _checker(payload: dict = Depends(verify_token)) -> dict:
        if payload.get("role") not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return payload
    return _checker


require_admin = require_roles("admin")
require_operator = require_roles("admin", "operator")


def can_manage(payload: dict) -> bool:
    """能否查看接入凭据（Agent / 采集器 Token）：只读用户不可见"""
    return payload.get("role") in ("admin", "operator")
