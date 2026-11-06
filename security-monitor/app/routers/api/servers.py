"""服务器管理"""
import io
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.crypto import decrypt, encrypt, new_access_token
from app.core.database import db
from app.core.scope import ensure_row_access, require_site_in_scope, scope_filter, site_scope
from app.core.security import can_manage, require_operator, verify_token
from app.routers.api.sites import clean_site_id

router = APIRouter()


class ServerCreate(BaseModel):
    name: str
    ip: str
    os_type: str
    port: Optional[int] = 22
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, max_length=200)
    collect_mode: Optional[str] = "agent"  # agent | ssh | both
    site_id: Optional[int] = None


_SERVER_PUBLIC = """
    s.id, s.name, s.ip, s.os_type, s.port, s.username, s.status, s.last_heartbeat,
    s.agent_token, s.agent_last_seen, s.collect_mode, s.created_at, s.updated_at,
    s.site_id, s.cpu, s.memory, s.disk, s.last_seen, si.name AS site_name,
    (s.ssh_host_key IS NOT NULL) AS ssh_host_key_known,
    split_part(COALESCE(s.ssh_host_key, ''), ' ', 1) AS ssh_host_key_type
"""


async def _server_public(server_id: int):
    row = await db.fetch_one(
        f"""
        SELECT {_SERVER_PUBLIC}
        FROM servers s
        LEFT JOIN sites si ON si.id = s.site_id
        WHERE s.id = $1
        """,
        server_id,
    )
    return {**dict(row), "agent_token": decrypt(row["agent_token"])} if row else None


@router.get("/servers")
async def list_servers(site_id: Optional[int] = None, payload: dict = Depends(verify_token)):
    """获取服务器列表（含 Agent 信息，不返回 SSH 密码；按用户门店权限过滤）"""
    cond, args = scope_filter("s.site_id", await site_scope(payload), [])
    if site_id:
        args.append(site_id)
        cond += f" AND s.site_id = ${len(args)}"
    rows = await db.fetch_all(
        f"""
        SELECT {_SERVER_PUBLIC}
        FROM servers s
        LEFT JOIN sites si ON si.id = s.site_id
        WHERE {cond}
        ORDER BY s.created_at DESC
        """,
        *args,
    )
    result = []
    for row in rows:
        item = dict(row)
        mode = (item.get("collect_mode") or "agent").lower()
        status = (item.get("status") or "offline").lower()
        # 是否近期有 Agent 心跳（5 分钟内视为 Agent 在线）
        seen = item.get("agent_last_seen")
        agent_online = False
        if seen:
            try:
                delta = datetime.utcnow() - seen.replace(tzinfo=None)
                agent_online = delta.total_seconds() < 300
            except Exception:
                agent_online = False
        item["agent_online"] = agent_online
        ssh_online = status == "online"

        # 当前实际数据通道（给前端展示用）
        if mode == "agent":
            if agent_online:
                item["data_channel"] = "agent"
                item["data_channel_label"] = "Agent 正在推送"
            else:
                item["data_channel"] = "none"
                item["data_channel_label"] = "等待 Agent 上报"
        elif mode == "ssh":
            if ssh_online:
                item["data_channel"] = "ssh"
                item["data_channel_label"] = "SSH 正在拉取"
            else:
                item["data_channel"] = "none"
                item["data_channel_label"] = "SSH 未连通"
        else:  # both
            if agent_online:
                item["data_channel"] = "agent"
                item["data_channel_label"] = "Agent 推送（优先）"
            elif ssh_online:
                item["data_channel"] = "ssh"
                item["data_channel_label"] = "SSH 拉取（Agent离线）"
            else:
                item["data_channel"] = "none"
                item["data_channel_label"] = "无可用数据源"

        item["ssh_online"] = ssh_online
        item["agent_token"] = decrypt(item.get("agent_token")) if can_manage(payload) else None
        result.append(item)
    return result


@router.post("/servers")
async def create_server(server: ServerCreate, payload: dict = Depends(require_operator)):
    """创建服务器并生成 Agent Token"""
    await require_site_in_scope(payload, clean_site_id(server.site_id))
    _, enc, digest = new_access_token()
    mode = server.collect_mode or "agent"
    row = await db.fetch_one("""
        INSERT INTO servers (name, ip, os_type, port, username, password, status, agent_token, agent_token_hash,
                             collect_mode, site_id)
        VALUES ($1, $2, $3, $4, $5, $6, 'offline', $7, $8, $9, $10)
        RETURNING id
    """, server.name, server.ip, server.os_type, server.port, server.username, encrypt(server.password),
        enc, digest, mode, clean_site_id(server.site_id))
    created = await _server_public(row["id"])
    return dict(created)


@router.put("/servers/{server_id}")
async def update_server(server_id: int, server: ServerCreate, payload: dict = Depends(require_operator)):
    """更新服务器"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    mode = server.collect_mode or "agent"
    site_id = clean_site_id(server.site_id)
    await require_site_in_scope(payload, site_id)
    # 地址或端口变更后原主机密钥不再适用，下次连接重新记录
    await db.execute(
        "UPDATE servers SET ssh_host_key=NULL WHERE id=$1 AND (ip IS DISTINCT FROM $2 OR port IS DISTINCT FROM $3)",
        server_id, server.ip, server.port,
    )
    if server.password:
        row = await db.fetch_one("""
            UPDATE servers SET name=$1, ip=$2, os_type=$3, port=$4, username=$5, password=$6,
                   collect_mode=$7, site_id=$8, updated_at=NOW()
            WHERE id=$9
            RETURNING id
        """, server.name, server.ip, server.os_type, server.port, server.username, encrypt(server.password), mode, site_id, server_id)
    else:
        row = await db.fetch_one("""
            UPDATE servers SET name=$1, ip=$2, os_type=$3, port=$4, username=$5,
                   collect_mode=$6, site_id=$7, updated_at=NOW()
            WHERE id=$8
            RETURNING id
        """, server.name, server.ip, server.os_type, server.port, server.username, mode, site_id, server_id)
    if not row:
        raise HTTPException(status_code=404, detail="服务器不存在")
    updated = await _server_public(server_id)
    return dict(updated)


@router.post("/servers/{server_id}/reset-host-key")
async def reset_server_host_key(server_id: int, payload: dict = Depends(require_operator)):
    """清除已记录的 SSH 主机密钥（服务器重装系统后使用），下次连接重新记录"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    row = await db.fetch_one(
        "UPDATE servers SET ssh_host_key=NULL, updated_at=NOW() WHERE id=$1 RETURNING id", server_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="服务器不存在")
    return {"status": "success"}


@router.post("/servers/{server_id}/reset-agent-token")
async def reset_agent_token(server_id: int, payload: dict = Depends(require_operator)):
    """重置 Agent Token"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    plain, enc, digest = new_access_token()
    row = await db.fetch_one(
        """
        UPDATE servers SET agent_token=$1, agent_token_hash=$2, updated_at=NOW()
        WHERE id=$3
        RETURNING id, name, collect_mode
        """,
        enc,
        digest,
        server_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="服务器不存在")
    return {**dict(row), "agent_token": plain}


async def _ensure_server_agent_token(server_id: int) -> dict:
    row = await db.fetch_one(
        "SELECT id, name, ip, os_type, agent_token, collect_mode FROM servers WHERE id=$1",
        server_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="服务器不存在")
    token = decrypt(row.get("agent_token"))
    if not token:
        # 尚未生成，或密文无法解密（CREDENTIAL_KEY 被更换）：重新生成
        token, enc, digest = new_access_token()
        row = await db.fetch_one(
            """
            UPDATE servers SET agent_token=$1, agent_token_hash=$2,
                   collect_mode=COALESCE(collect_mode,'agent'), updated_at=NOW()
            WHERE id=$3
            RETURNING id, name, ip, os_type, collect_mode
            """,
            enc,
            digest,
            server_id,
        )
    return {**dict(row), "agent_token": token}


@router.get("/servers/{server_id}/agent-install")
async def get_agent_install_info(server_id: int, payload: dict = Depends(require_operator)):
    """返回 Agent 安装说明与配置"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    row = await _ensure_server_agent_token(server_id)
    return {
        "server_id": row["id"],
        "server_name": row["name"],
        "os_type": row["os_type"],
        "agent_token": row["agent_token"],
        "api_base_hint": "/api/v1",
        "endpoints": {
            "heartbeat": "/api/v1/agent/heartbeat",
            "events": "/api/v1/agent/events",
            "config": "/api/v1/agent/config",
        },
        "windows_install": "下载 Windows 安装包 → 拷到目标机解压 → 双击「安装Agent.bat」→ 点「是」授权 → 下一步安装",
        "linux_install": "下载 Linux 安装包 → 拷到目标机解压 → sudo bash install.sh",
    }


@router.get("/servers/{server_id}/agent-package")
async def download_agent_package(
    server_id: int,
    os: str = Query(..., description="windows 或 linux"),
    server_url: str = Query(..., description="目标机能访问的管理端地址，如 http://192.168.1.10:8000"),
    payload: dict = Depends(require_operator),
):
    """下载预配置安装包（ZIP），配置已写入，小白可直接下一步安装"""
    from app.services.agent_package import build_agent_package

    await ensure_row_access(payload, "servers", server_id, "服务器")
    row = await _ensure_server_agent_token(server_id)
    url = (server_url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="server_url 必须以 http:// 或 https:// 开头")
    if "YOUR_SERVER" in url:
        raise HTTPException(status_code=400, detail="请填写真实的管理端地址")

    try:
        content, filename = build_agent_package(
            os_type=os,
            server_name=row["name"],
            server_url=url,
            agent_token=row["agent_token"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }
    return StreamingResponse(io.BytesIO(content), media_type="application/zip", headers=headers)


@router.delete("/servers/{server_id}")
async def delete_server(server_id: int, payload: dict = Depends(require_operator)):
    """删除服务器"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    await db.execute("DELETE FROM host_metrics WHERE server_id=$1", server_id)
    await db.execute(
        "DELETE FROM health_alert_state WHERE source_type='server' AND source_id=$1",
        server_id,
    )
    await db.execute("DELETE FROM servers WHERE id=$1", server_id)
    return {"status": "success"}


@router.post("/servers/{server_id}/test")
async def test_server_connection(server_id: int, payload: dict = Depends(require_operator)):
    """测试服务器 SSH 连通性，并更新在线状态"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    from app.collector.log_collector import collector

    server = await db.fetch_one("SELECT * FROM servers WHERE id=$1", server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    server = dict(server)
    server["password"] = decrypt(server.get("password"))
    if not server.get("password"):
        raise HTTPException(status_code=400, detail="请先配置服务器登录密码")

    os_type = (server.get("os_type") or "linux").lower()
    username = server.get("username") or ("Administrator" if os_type == "windows" else "root")
    result = await collector.test_connection(
        host=server["ip"],
        port=int(server.get("port") or 22),
        username=username,
        password=server.get("password"),
        server_id=server_id,
    )
    status = "online" if result["success"] else "error"
    if status == "online":
        await db.execute(
            "UPDATE servers SET status=$1, last_heartbeat=NOW(), updated_at=NOW() WHERE id=$2",
            status,
            server_id,
        )
    else:
        await db.execute(
            "UPDATE servers SET status=$1, updated_at=NOW() WHERE id=$2",
            status,
            server_id,
        )
    return result


@router.post("/servers/{server_id}/collect")
async def collect_server_now(server_id: int, payload: dict = Depends(require_operator)):
    """立即采集该服务器日志并触发告警处理"""
    await ensure_row_access(payload, "servers", server_id, "服务器")
    from app.collector.log_collector import collector
    from app.engine.rule_engine import rule_engine

    server = await db.fetch_one("SELECT * FROM servers WHERE id=$1", server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    server = dict(server)
    server["password"] = decrypt(server.get("password"))
    if not server.get("password"):
        raise HTTPException(status_code=400, detail="请先配置服务器登录密码")

    os_type = (server.get("os_type") or "linux").lower()
    username = server.get("username") or ("Administrator" if os_type == "windows" else "root")
    logs = await collector.collect_logs(
        server_id=server_id,
        host=server["ip"],
        os_type=os_type,
        port=int(server.get("port") or 22),
        username=username,
        password=server.get("password"),
    )

    if logs.get("success"):
        await db.execute(
            "UPDATE servers SET status='online', last_heartbeat=NOW(), updated_at=NOW() WHERE id=$1",
            server_id,
        )
    else:
        await db.execute(
            "UPDATE servers SET status='error', updated_at=NOW() WHERE id=$1",
            server_id,
        )
        raise HTTPException(status_code=500, detail=logs.get("error") or "采集失败")

    alerted = 0
    for log in logs.get("login_logs", []):
        log.update({
            "server_id": server_id,
            "server_name": server["name"],
            "server_host": server["ip"],
            "os_type": os_type,
        })
        if await rule_engine.process_login_log(log):
            alerted += 1

    for op in logs.get("high_risk_ops", []):
        op.update({
            "server_id": server_id,
            "server_name": server["name"],
            "server_host": server["ip"],
            "os_type": os_type,
            "collect_source": op.get("collect_source") or "ssh",
        })
        if await rule_engine.process_high_risk_op(op):
            alerted += 1

    return {
        "status": "success",
        "login_logs": len(logs.get("login_logs", [])),
        "high_risk_ops": len(logs.get("high_risk_ops", [])),
        "alerted": alerted,
    }
