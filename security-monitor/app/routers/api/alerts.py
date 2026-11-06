"""告警管理（按用户门店权限过滤）"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.audit import audit_note
from app.core.database import db
from app.core.scope import alert_scope_filter, site_scope
from app.core.security import require_operator, verify_token

router = APIRouter()


@router.get("/alerts")
async def list_alerts(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    level: Optional[str] = None,
    status: Optional[str] = None,
    site_id: Optional[int] = None,
    source_type: Optional[str] = None,
    keyword: Optional[str] = Query(None, max_length=100),
    payload: dict = Depends(verify_token)
):
    """获取告警列表"""
    cond, params = alert_scope_filter("", await site_scope(payload), [])
    where = [cond]

    def add(sql: str, value):
        params.append(value)
        where.append(sql.replace("?", f"${len(params)}"))

    if level:
        add("level = ?", level)
    if status == "open":
        where.append("status IN ('new', 'acknowledged')")
    elif status:
        add("status = ?", status)
    if source_type:
        add("source_type = ?", source_type)
    if site_id:
        scoped, params = alert_scope_filter("", {int(site_id)}, params)
        where.append(scoped)
    if keyword:
        add("(source_name ILIKE ? OR event_description ILIKE ?)", f"%{keyword}%")  # 两处 ? 复用同一参数
    clause = " AND ".join(where)

    rows = await db.fetch_all(
        f"SELECT * FROM alert_logs WHERE {clause} ORDER BY created_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
        *params, size, (page - 1) * size,
    )
    count_row = await db.fetch_one(f"SELECT COUNT(*) AS count FROM alert_logs WHERE {clause}", *params)

    return {
        "items": [dict(row) for row in rows],
        "total": count_row["count"] if count_row else 0,
        "page": page,
        "size": size
    }


async def _load_alert(payload: dict, alert_id: int) -> dict:
    cond, params = alert_scope_filter("", await site_scope(payload), [alert_id])
    row = await db.fetch_one(f"SELECT * FROM alert_logs WHERE id=$1 AND {cond}", *params)
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    return dict(row)


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: int, payload: dict = Depends(verify_token)):
    """获取告警详情"""
    return await _load_alert(payload, alert_id)


class AlertStatusUpdate(BaseModel):
    status: Literal["new", "acknowledged", "resolved", "ignored"]


@router.patch("/alerts/{alert_id}/status")
async def update_alert_status(alert_id: int, status_data: AlertStatusUpdate, request: Request,
                              payload: dict = Depends(require_operator)):
    """更新告警状态（仅在已解决/忽略时记录 resolved_at）"""
    await _load_alert(payload, alert_id)
    audit_note(request, status=status_data.status)
    row = await db.fetch_one("""
        UPDATE alert_logs
        SET status=$1::varchar,
            resolved_at = CASE WHEN $1::varchar IN ('resolved', 'ignored') THEN NOW() ELSE NULL END
        WHERE id=$2 RETURNING *
    """, status_data.status, alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    return dict(row)
