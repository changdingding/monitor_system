"""
系统启用与实施期数据清理。

- 未启用（实施模式）：管理员可按类别清理实施 / 测试期间产生的数据，方便正式上线前归零
- 启用：一次性、不可撤销；启用后清理接口永久关闭，AI 分析记录等也不能再删除
- 启用、清理都会写入操作审计（含每张表清理的行数）；操作审计本身任何时候都不会被清理
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.audit import audit_note
from app.core.database import db
from app.core.security import require_admin, verify_token
from app.core.system_state import forbid_after_activation, get_state

router = APIRouter()

# 类别 -> (说明, [(表, 中文名)])；按顺序删除，先删引用方
PURGE_CATEGORIES = {
    "monitoring": ("监控数据：告警、登录 / 高危日志、资源指标、端口状态、AI 分析记录", [
        ("alert_logs", "告警记录"),
        ("health_alert_state", "健康告警状态"),
        ("alert_records", "推送记录"),
        ("login_logs", "登录日志"),
        ("high_risk_operations", "高危操作"),
        ("syslog_logs", "Syslog"),
        ("server_account_sessions", "账号会话"),
        ("host_metrics", "服务器指标"),
        ("snmp_metrics", "设备指标"),
        ("interface_status", "端口状态"),
        ("log_analyses", "AI 分析记录"),
    ]),
    "assets": ("资产：服务器、网络设备（连同其监控数据）", [
        ("network_devices", "网络设备"),
        ("servers", "服务器"),
    ]),
    "sites": ("站点：总部 / 分支 / 门店 / 云端，以及用户的门店授权", [
        ("user_sites", "用户门店授权"),
        ("sites", "站点"),
    ]),
    "integrations": ("对接配置：飞书机器人、AI 模型配置", [
        ("feishu_config", "飞书配置"),
        ("ai_config", "AI 配置"),
    ]),
    "users": ("用户：除当前登录管理员外的全部账号", [
        ("users", "用户"),
    ]),
}


async def _count(table: str, exclude_user: Optional[int] = None) -> int:
    try:
        if table == "users" and exclude_user is not None:
            row = await db.fetch_one("SELECT COUNT(*) AS n FROM users WHERE id <> $1", exclude_user)
        else:
            row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row else 0
    except Exception:
        return 0


@router.get("/system/state")
async def system_state(payload: dict = Depends(verify_token)):
    state = await get_state()
    audit = await db.fetch_one("SELECT COUNT(*) AS n FROM audit_logs")
    return {**state, "audit_log_count": int(audit["n"]) if audit else 0}


@router.get("/system/purge-preview")
async def purge_preview(payload: dict = Depends(require_admin)):
    """各类别可清理的数据量"""
    state = await get_state()
    categories = []
    for key, (label, tables) in PURGE_CATEGORIES.items():
        items = [{"table": t, "label": name, "count": await _count(t, payload.get("user_id"))} for t, name in tables]
        categories.append({"key": key, "label": label, "tables": items, "total": sum(i["count"] for i in items)})
    return {"activated": bool(state.get("activated")), "categories": categories}


class PurgeRequest(BaseModel):
    categories: List[Literal["monitoring", "assets", "sites", "integrations", "users"]] = Field(..., min_length=1)
    confirm: str


@router.post("/system/purge")
async def purge_data(body: PurgeRequest, request: Request, payload: dict = Depends(require_admin)):
    """清理实施期数据（仅未启用时可用）"""
    await forbid_after_activation()
    if body.confirm.strip() != "确认清理":
        raise HTTPException(status_code=400, detail="请输入「确认清理」以确认操作")
    chosen = set(body.categories)
    # 删除资产时其监控数据必然一起消失，统一按 监控 → 资产 → 站点 → 对接 → 用户 的顺序执行
    order = [key for key in PURGE_CATEGORIES if key in chosen]
    me = payload.get("user_id")
    deleted = {}
    pool = await db._ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 并发保护：事务内再次确认未启用
            state = await conn.fetchrow("SELECT activated FROM system_state WHERE id=1 FOR UPDATE")
            if state and state["activated"]:
                raise HTTPException(status_code=403, detail="系统已正式启用，数据不可清除")
            for key in order:
                for table, name in PURGE_CATEGORIES[key][1]:
                    if table == "users":
                        result = await conn.execute("DELETE FROM users WHERE id <> $1", me)
                    else:
                        result = await conn.execute(f"DELETE FROM {table}")
                    deleted[name] = int(result.split()[-1]) if result else 0
            if "monitoring" in chosen and "assets" not in chosen:
                # 保留资产时把状态归零，等待重新上报（不会因为旧心跳误报离线）
                await conn.execute(
                    "UPDATE servers SET status='offline', cpu=NULL, memory=NULL, disk=NULL, last_seen=NULL, "
                    "last_heartbeat=NULL, agent_last_seen=NULL, updated_at=NOW()"
                )
                await conn.execute(
                    "UPDATE network_devices SET status='offline', cpu=NULL, memory=NULL, last_seen=NULL, updated_at=NOW()"
                )
            if "monitoring" in chosen and "sites" not in chosen:
                await conn.execute("UPDATE sites SET status='offline', last_seen=NULL, updated_at=NOW()")
    audit_note(request, categories=order, deleted=deleted)
    return {"status": "success", "deleted": deleted}


class ActivateRequest(BaseModel):
    confirm: str
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/system/activate")
async def activate_system(body: ActivateRequest, request: Request, payload: dict = Depends(require_admin)):
    """正式启用系统（不可撤销）"""
    if body.confirm.strip() != "确认启用":
        raise HTTPException(status_code=400, detail="请输入「确认启用」以确认操作")
    row = await db.fetch_one(
        """
        UPDATE system_state SET activated=TRUE, activated_at=NOW(), activated_by=$1, note=$2
        WHERE id=1 AND NOT activated
        RETURNING activated, activated_at, activated_by, note
        """,
        payload.get("username"), (body.note or "").strip() or None,
    )
    if not row:
        raise HTTPException(status_code=400, detail="系统已经启用")
    audit_note(request, note=(body.note or "")[:200])
    return dict(row)
