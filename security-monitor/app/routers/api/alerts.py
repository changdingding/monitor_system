"""告警管理"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.audit import audit_note
from app.core.database import db
from app.core.security import require_operator, verify_token

router = APIRouter()


@router.get("/alerts")
async def list_alerts(
    page: int = 1,
    size: int = 20,
    level: Optional[str] = None,
    status: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    """获取告警列表"""
    query = "SELECT * FROM alert_logs WHERE 1=1"
    params = []
    param_idx = 1
    
    if level:
        query += f" AND level = ${param_idx}"
        params.append(level)
        param_idx += 1
    
    if status:
        query += f" AND status = ${param_idx}"
        params.append(status)
        param_idx += 1
    
    query += f" ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([size, (page - 1) * size])
    
    rows = await db.fetch_all(query, *params)
    
    # 获取总数
    count_query = "SELECT COUNT(*) FROM alert_logs WHERE 1=1"
    count_params = []
    count_idx = 1
    
    if level:
        count_query += f" AND level = ${count_idx}"
        count_params.append(level)
        count_idx += 1
    
    if status:
        count_query += f" AND status = ${count_idx}"
        count_params.append(status)
        count_idx += 1
    
    count_row = await db.fetch_one(count_query, *count_params)
    
    return {
        "items": [dict(row) for row in rows],
        "total": count_row["count"] if count_row else 0,
        "page": page,
        "size": size
    }


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: int, payload: dict = Depends(verify_token)):
    """获取告警详情"""
    row = await db.fetch_one("SELECT * FROM alert_logs WHERE id=$1", alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    return dict(row)


class AlertStatusUpdate(BaseModel):
    status: Literal["new", "acknowledged", "resolved", "ignored"]


@router.patch("/alerts/{alert_id}/status")
async def update_alert_status(alert_id: int, status_data: AlertStatusUpdate, request: Request,
                              payload: dict = Depends(require_operator)):
    """更新告警状态（仅在已解决/忽略时记录 resolved_at）"""
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
