"""用户管理"""
from typing import List, Literal, Optional

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
    # 数据范围：all 全部站点；sites 仅 site_ids 中的站点及其下级（管理员始终为全部）
    data_scope: Literal["all", "sites"] = "all"
    site_ids: List[int] = Field(default_factory=list, max_length=500)


async def _check_scope(role: str, data_scope: str, site_ids: List[int]) -> tuple:
    if role == "admin":
        data_scope = "all"
    ids = sorted({int(i) for i in site_ids or []})
    if data_scope == "sites":
        if not ids:
            raise HTTPException(status_code=400, detail="请至少选择一个可访问的门店 / 分支")
        found = await db.fetch_all("SELECT id FROM sites WHERE id = ANY($1::int[])", ids)
        if len(found) != len(ids):
            raise HTTPException(status_code=400, detail="选择的站点不存在")
    else:
        ids = []
    return data_scope, ids


async def _write_scope(user_id: int, scope: tuple) -> None:
    data_scope, ids = scope
    await db.execute("UPDATE users SET data_scope=$1 WHERE id=$2", data_scope, user_id)
    await db.execute("DELETE FROM user_sites WHERE user_id=$1", user_id)
    if ids:
        await db.execute(
            "INSERT INTO user_sites (user_id, site_id) SELECT $1, unnest($2::int[])", user_id, ids
        )


async def _with_scope(rows: list) -> list:
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    links = await db.fetch_all(
        """
        SELECT us.user_id, us.site_id, s.name FROM user_sites us JOIN sites s ON s.id = us.site_id
        WHERE us.user_id = ANY($1::int[]) ORDER BY s.name
        """,
        ids,
    )
    by_user: dict = {}
    for link in links:
        by_user.setdefault(link["user_id"], []).append(link)
    result = []
    for row in rows:
        item = dict(row)
        mine = by_user.get(item["id"], [])
        item["site_ids"] = [link["site_id"] for link in mine]
        item["site_names"] = [link["name"] for link in mine]
        result.append(item)
    return result


@router.get("/users")
async def list_users(payload: dict = Depends(require_admin)):
    """获取用户列表"""
    rows = await db.fetch_all("""
        SELECT id, username, nickname, email, role, status, last_login, created_at, updated_at, data_scope,
               CASE WHEN locked_until > (NOW() AT TIME ZONE 'UTC') THEN locked_until END AS locked_until
        FROM users ORDER BY created_at DESC
    """)
    return await _with_scope(rows)


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
    scope = await _check_scope(user.role, user.data_scope, user.site_ids)
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
    await _write_scope(row["id"], scope)
    return {**(await _with_scope([row]))[0], "data_scope": scope[0]}


class UserUpdate(BaseModel):
    nickname: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=255)
    role: Literal["admin", "operator", "viewer"]
    status: Literal["active", "disabled"] = "active"
    data_scope: Optional[Literal["all", "sites"]] = None  # 不传表示不修改
    site_ids: Optional[List[int]] = Field(default=None, max_length=500)


class PasswordReset(BaseModel):
    new_password: str


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, payload: dict = Depends(require_admin)):
    """更新用户"""
    user = body.model_dump()
    if user_id == payload.get("user_id") and (user.get("role") != "admin" or user.get("status") != "active"):
        raise HTTPException(status_code=400, detail="不能降级或停用当前登录的管理员账号")
    scope = None
    if body.data_scope is not None or body.role == "admin":
        scope = await _check_scope(body.role, body.data_scope or "all", body.site_ids or [])
    row = await db.fetch_one("""
        UPDATE users SET nickname=$1, email=$2, role=$3, status=$4, updated_at=NOW()
        WHERE id=$5 RETURNING id, username, nickname, email, role, status
    """, user.get("nickname"), user.get("email"), user.get("role"), user.get("status"), user_id)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if scope is not None:
        await _write_scope(user_id, scope)
    return (await _with_scope([row]))[0]


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
