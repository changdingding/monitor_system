"""
Agent 接入：客户端主动上报心跳与安全事件
"""
import logging
from datetime import datetime
from typing import Optional, List, Any
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field
from app.core.database import db
from app.core.crypto import token_hash
from app.engine.rule_engine import rule_engine
from app.services.ingest_time import is_fresh, naive, parse_collected_at

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class AgentEvent(BaseModel):
    category: str = Field(..., description="session | high_risk")
    action: Optional[str] = None  # login | logout
    action_text: Optional[str] = None
    account: Optional[str] = None
    event_time: Optional[str] = None
    source_ip: Optional[str] = None
    operation_type: Optional[str] = None
    risk_level: Optional[str] = None
    description: Optional[str] = None
    os_type: Optional[str] = None
    hostname: Optional[str] = None
    target_account: Optional[str] = None
    group_name: Optional[str] = None
    extra: Optional[dict] = None


class AgentEventsRequest(BaseModel):
    events: List[AgentEvent] = Field(default_factory=list)
    collected_at: Optional[str] = None


class AgentHeartbeatRequest(BaseModel):
    hostname: Optional[str] = None
    os_type: Optional[str] = None
    agent_version: Optional[str] = "1.0.0"
    ip: Optional[str] = None
    collected_at: Optional[str] = None


class AgentMetricsRequest(BaseModel):
    cpu: Optional[float] = None
    memory: Optional[float] = None
    disk: Optional[float] = None
    hostname: Optional[str] = None
    collected_at: Optional[str] = None


async def _auth_agent(x_agent_token: str = Header(..., alias="X-Agent-Token")) -> dict:
    if not x_agent_token:
        raise HTTPException(status_code=401, detail="缺少 Agent Token")
    row = await db.fetch_one(
        "SELECT * FROM servers WHERE agent_token_hash = $1",
        token_hash(x_agent_token),
    )
    if not row:
        raise HTTPException(status_code=401, detail="无效的 Agent Token")
    return dict(row)


async def _touch_server(server: dict, collected_at: Optional[str]) -> bool:
    """用采集时间前进心跳。过期补发不把服务器标成当前在线。返回这次样本是否仍新鲜。"""
    when = parse_collected_at(collected_at)
    if when is None:
        return False
    fresh = is_fresh(when)
    await db.execute(
        """
        UPDATE servers
        SET status = CASE
                WHEN $3 AND (last_seen IS NULL OR $2 >= last_seen) THEN 'online'
                ELSE status
            END,
            last_heartbeat = CASE WHEN last_heartbeat IS NULL OR $2 >= last_heartbeat THEN $2 ELSE last_heartbeat END,
            agent_last_seen = CASE WHEN agent_last_seen IS NULL OR $2 >= agent_last_seen THEN $2 ELSE agent_last_seen END,
            last_seen = CASE WHEN last_seen IS NULL OR $2 >= last_seen THEN $2 ELSE last_seen END,
            collect_mode = COALESCE(collect_mode, 'agent'),
            updated_at = NOW()
        WHERE id = $1
        """,
        server["id"],
        when,
        fresh,
    )
    if fresh:
        site_name = ""
        if server.get("site_id"):
            site = await db.fetch_one("SELECT name FROM sites WHERE id=$1", server["site_id"])
            site_name = (site or {}).get("name") or ""
        from app.services.health import sync_condition
        await sync_condition(
            event_key=f"server:{server['id']}:offline",
            active=False,
            site_id=server.get("site_id"),
            source_type="server",
            source_id=server["id"],
            source_name=server["name"],
            host=server.get("ip") or "",
            alert_type="offline",
            severity="critical",
            message="服务器心跳已恢复",
            site_name=site_name,
        )
    return fresh


@router.post("/heartbeat")
async def agent_heartbeat(body: AgentHeartbeatRequest, server: dict = Depends(_auth_agent)):
    """Agent 心跳：标记服务器在线"""
    await _touch_server(server, body.collected_at)
    return {
        "status": "ok",
        "server_id": server["id"],
        "server_name": server["name"],
        "time": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/events")
async def agent_report_events(body: AgentEventsRequest, server: dict = Depends(_auth_agent)):
    """Agent 上报安全事件 → 规则引擎 → 飞书"""
    await _touch_server(server, body.collected_at)

    accepted = 0
    alerted = 0
    skipped = 0

    for ev in body.events:
        accepted += 1
        category = (ev.category or "").lower()
        os_type = (ev.os_type or server.get("os_type") or "linux").lower()

        if category in ("session", "login", "logout"):
            action = (ev.action or category or "login").lower()
            if action not in ("login", "logout"):
                action = "login"
            payload = {
                "server_id": server["id"],
                "server_name": server["name"],
                "server_host": server.get("ip") or ev.hostname or "",
                "os_type": os_type,
                "action": action,
                "action_text": ev.action_text or ("账号登录" if action == "login" else "账号退出"),
                "account": ev.account or "",
                "login_time": ev.event_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_ip": ev.source_ip or "",
            }
            try:
                if await rule_engine.process_login_log(payload):
                    alerted += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"处理会话事件失败：{e}")
                skipped += 1

        elif category in ("high_risk", "highrisk", "risk"):
            payload = {
                "server_id": server["id"],
                "server_name": server["name"],
                "server_host": server.get("ip") or ev.hostname or "",
                "os_type": os_type,
                "operation_type": ev.operation_type or "unknown",
                "risk_level": (ev.risk_level or "high").lower(),
                "account": ev.account or "",
                "target_account": ev.target_account or "",
                "group_name": ev.group_name or "",
                "operation_time": ev.event_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "description": ev.description or "",
                "collect_source": "agent",
                "hostname": ev.hostname or "",
            }
            try:
                if await rule_engine.process_high_risk_op(payload):
                    alerted += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"处理高危事件失败：{e}")
                skipped += 1
        else:
            skipped += 1

    return {
        "status": "ok",
        "accepted": accepted,
        "alerted": alerted,
        "skipped": skipped,
    }


def _pct(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 100:
        return None
    return round(number, 1)


@router.post("/metrics")
async def agent_metrics(body: AgentMetricsRequest, server: dict = Depends(_auth_agent)):
    """Agent 上报 CPU / 内存 / 磁盘使用率。旧样本只进曲线，不覆盖当前快照。"""
    when = parse_collected_at(body.collected_at)
    if when is None:
        return {"status": "ignored", "reason": "too_old"}
    cpu = _pct(body.cpu)
    memory = _pct(body.memory)
    disk = _pct(body.disk)
    previous = naive(server.get("last_seen"))
    latest = previous is None or when >= previous
    await db.execute(
        """
        INSERT INTO host_metrics (server_id, cpu, memory, disk, collected_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        server["id"],
        cpu,
        memory,
        disk,
        when,
    )
    if not latest:
        return {"status": "ok", "server_id": server["id"], "applied": False}
    await db.execute(
        """
        UPDATE servers
        SET cpu = COALESCE($2, cpu),
            memory = COALESCE($3, memory),
            disk = COALESCE($4, disk),
            updated_at = NOW()
        WHERE id = $1 AND (last_seen IS NULL OR $5 >= last_seen)
        """,
        server["id"],
        cpu,
        memory,
        disk,
        when,
    )
    fresh = await _touch_server(server, body.collected_at)
    if fresh:
        site_name = ""
        if server.get("site_id"):
            site = await db.fetch_one("SELECT name FROM sites WHERE id=$1", server["site_id"])
            site_name = (site or {}).get("name") or ""
        from app.services.health import check_resource_thresholds
        await check_resource_thresholds(
            source_type="server",
            source_id=server["id"],
            source_name=server["name"],
            host=server.get("ip") or body.hostname or "",
            site_id=server.get("site_id"),
            site_name=site_name,
            cpu=cpu,
            memory=memory,
            disk=disk,
            key_prefix=f"server:{server['id']}",
        )
    return {"status": "ok", "server_id": server["id"], "applied": True}


@router.get("/config")
async def agent_get_config(server: dict = Depends(_auth_agent)):
    """Agent 拉取运行配置"""
    return {
        "server_id": server["id"],
        "server_name": server["name"],
        "os_type": server.get("os_type"),
        "poll_interval_seconds": 30,
        "heartbeat_interval_seconds": 60,
    }


