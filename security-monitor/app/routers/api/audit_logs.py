"""操作审计"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.database import db
from app.core.security import require_admin

router = APIRouter()


@router.get("/audit-logs")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    username: Optional[str] = Query(None, max_length=100),
    action: Optional[str] = Query(None, max_length=100),
    success: Optional[bool] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    payload: dict = Depends(require_admin),
):
    """操作审计日志（仅管理员）"""
    where, params = [], []

    def add(cond: str, value):
        params.append(value)
        where.append(cond.replace("?", f"${len(params)}"))

    if username:
        add("username ILIKE ?", f"%{username}%")
    if action:
        add("action = ?", action)
    if success is not None:
        add("success = ?", success)
    if start:
        add("created_at >= ?", start.replace(tzinfo=None))
    if end:
        add("created_at <= ?", end.replace(tzinfo=None))
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = await db.fetch_one(f"SELECT COUNT(*) AS n FROM audit_logs{clause}", *params)
    rows = await db.fetch_all(
        f"SELECT * FROM audit_logs{clause} ORDER BY id DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
        *params, size, (page - 1) * size,
    )
    return {"items": [dict(r) for r in rows], "total": total["n"] if total else 0, "page": page, "size": size}


@router.get("/audit-logs/actions")
async def list_audit_actions(payload: dict = Depends(require_admin)):
    """已出现过的动作名（筛选下拉用）"""
    rows = await db.fetch_all("SELECT DISTINCT action FROM audit_logs ORDER BY action")
    return [r["action"] for r in rows]
