"""用户管理"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import db
from app.core.security import ROLES, hash_password, require_admin, validate_password

router = APIRouter()


class UserCreate(BaseModel):
    username: str
    password: str
    nickname: str
    email: Optional[str] = None
    role: str
    status: str = "active"


@router.get("/users")
async def list_users(payload: dict = Depends(require_admin)):
    """获取用户列表"""
    rows = await db.fetch_all("""
        SELECT id, username, nickname, email, role, status, last_login, created_at, updated_at,
               CASE WHEN locked_until > (NOW() AT TIME ZONE 'UTC') THEN locked_until END AS locked_until
        FROM users ORDER BY created_at DESC
    """)
    return [dict(row) for row in rows]


@router.post("/users")
async def create_user(user: UserCreate, payload: dict = Depends(require_admin)):
    """创建用户"""
    existing = await db.fetch_one(
        "SELECT id FROM users WHERE username = $1",
        user.username,
    )
    if existing:
        raise HTTPException(status_code=400, detail=f"用户名 '{user.username}' 已存在")

    if user.role not in ROLES:
        raise HTTPException(status_code=400, detail="无效的角色")
    password_hash = hash_password(validate_password(user.password))
    try:
        row = await db.fetch_one("""
            INSERT INTO users (username, password_hash, nickname, email, role, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, username, nickname, email, role, status
        """, user.username, password_hash, user.nickname, user.email, user.role, user.status)
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(status_code=400, detail=f"用户名 '{user.username}' 已存在")
        raise
    return dict(row)


class UserUpdate(BaseModel):
    nickname: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=255)
    role: Literal["admin", "operator", "viewer"]
    status: Literal["active", "disabled"] = "active"


class PasswordReset(BaseModel):
    new_password: str


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, payload: dict = Depends(require_admin)):
    """更新用户"""
    user = body.model_dump()
    if user_id == payload.get("user_id") and (user.get("role") != "admin" or user.get("status") != "active"):
        raise HTTPException(status_code=400, detail="不能降级或停用当前登录的管理员账号")
    row = await db.fetch_one("""
        UPDATE users SET nickname=$1, email=$2, role=$3, status=$4, updated_at=NOW()
        WHERE id=$5 RETURNING id, username, nickname, email, role, status
    """, user.get("nickname"), user.get("email"), user.get("role"), user.get("status"), user_id)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return dict(row)


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, payload: dict = Depends(require_admin)):
    """删除用户"""
    if user_id == payload.get("user_id"):
        raise HTTPException(status_code=400, detail="不能删除当前登录的账号")
    await db.execute("DELETE FROM users WHERE id=$1", user_id)
    return {"status": "success"}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(user_id: int, data: PasswordReset, payload: dict = Depends(require_admin)):
    """重置用户密码"""
    password_hash = hash_password(validate_password(data.new_password))
    # 管理员重置他人密码后：对方已登录的会话全部失效，下次登录须自行修改
    # （重置自己的密码时保留当前会话，否则管理员会把自己踢下线）
    await db.execute("""
        UPDATE users SET password_hash=$1, must_change_password=($2 <> $3),
                         token_version=token_version + CASE WHEN $2 <> $3 THEN 1 ELSE 0 END,
                         failed_login_count=0, locked_until=NULL, updated_at=NOW() WHERE id=$2
    """, password_hash, user_id, payload.get("user_id"))
    return {"status": "success"}


@router.post("/users/{user_id}/unlock")
async def unlock_user(user_id: int, payload: dict = Depends(require_admin)):
    """解除登录失败锁定"""
    row = await db.fetch_one(
        "UPDATE users SET failed_login_count=0, locked_until=NULL, updated_at=NOW() WHERE id=$1 RETURNING id",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "success"}
