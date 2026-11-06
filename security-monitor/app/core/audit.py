"""
操作审计：ASGI 中间件自动记录 /api/v1 下的所有写操作（POST/PUT/PATCH/DELETE）。

- 不记录请求体，避免密码、团体字等敏感字段进入审计表
- 目标名称：带 id 的路由在执行前查库得到（删除后也能看到删的是谁）；
  创建类路由从响应 JSON 中只提取 id / name / username
- 端点可以通过 audit_note(request, ...) 追加少量说明（如登录尝试的用户名）
- Agent / 采集器上报属于机器流量，量大且不是人为操作，不记录
"""
import json
import logging
from typing import Any, Dict, Optional

import jwt
from starlette.requests import Request

from app.core.config import settings
from app.core.database import db

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PREFIXES = (f"{API_PREFIX}/agent/", f"{API_PREFIX}/probe/")
_SCOPE_KEY = "audit_note"
_MAX_CAPTURE = 64 * 1024

# (方法, 路由模板去掉前缀) -> 中文动作名
ACTIONS: Dict[tuple, str] = {
    ("POST", "/auth/login"): "登录",
    ("POST", "/auth/change-password"): "修改本人密码",
    ("POST", "/sites"): "新增站点",
    ("PUT", "/sites/{site_id}"): "修改站点",
    ("DELETE", "/sites/{site_id}"): "删除站点",
    ("POST", "/sites/{site_id}/reset-probe-token"): "重置采集器 Token",
    ("POST", "/servers"): "新增服务器",
    ("PUT", "/servers/{server_id}"): "修改服务器",
    ("DELETE", "/servers/{server_id}"): "删除服务器",
    ("POST", "/servers/{server_id}/reset-host-key"): "重置 SSH 主机密钥",
    ("POST", "/servers/{server_id}/reset-agent-token"): "重置 Agent Token",
    ("POST", "/servers/{server_id}/test"): "SSH 连通性测试",
    ("POST", "/servers/{server_id}/collect"): "SSH 手动拉取",
    ("POST", "/network-devices"): "新增网络设备",
    ("PUT", "/network-devices/{device_id}"): "修改网络设备",
    ("DELETE", "/network-devices/{device_id}"): "删除网络设备",
    ("PATCH", "/alerts/{alert_id}/status"): "处理告警",
    ("PUT", "/feishu/config"): "修改飞书配置",
    ("POST", "/feishu/test"): "飞书测试推送",
    ("POST", "/test/feishu"): "飞书测试推送",
    ("POST", "/users"): "新增用户",
    ("PUT", "/users/{user_id}"): "修改用户",
    ("DELETE", "/users/{user_id}"): "删除用户",
    ("POST", "/users/{user_id}/reset-password"): "重置用户密码",
    ("POST", "/users/{user_id}/unlock"): "解锁用户",
    ("PUT", "/settings"): "修改系统设置",
    ("PUT", "/ai/config"): "修改 AI 配置",
    ("POST", "/ai/test"): "AI 连通性测试",
    ("POST", "/log-analysis"): "AI 日志分析",
    ("POST", "/log-analysis/fetch"): "拉取服务器日志",
    ("DELETE", "/log-analysis/{analysis_id}"): "删除日志分析记录",
}

# 路由第一段 -> (目标类型, 表, 名称列)
TARGETS = {
    "sites": ("site", "sites", "name"),
    "servers": ("server", "servers", "name"),
    "network-devices": ("network_device", "network_devices", "name"),
    "users": ("user", "users", "username"),
    "alerts": ("alert", "alert_logs", "source_name"),
    "feishu": ("feishu", None, None),
    "settings": ("settings", None, None),
    "auth": ("auth", None, None),
    "ai": ("ai", None, None),
    "log-analysis": ("log_analysis", "log_analyses", "server_name"),
}


def audit_note(request: Request, **fields: Any) -> None:
    """端点追加审计说明（不要放入密码等敏感信息）"""
    note = request.scope.get(_SCOPE_KEY)
    if note is not None:
        note.update(fields)


def _actor(headers: Dict[bytes, bytes]) -> Dict[str, Any]:
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if not auth.startswith("Bearer "):
        return {}
    try:
        p = jwt.decode(auth[7:], settings.JWT_SECRET, algorithms=["HS256"])
        return {"user_id": p.get("user_id"), "username": p.get("username")}
    except Exception:
        return {}


async def _target_name(segment: str, target_id: Optional[int]) -> Optional[str]:
    _, table, col = TARGETS.get(segment, (None, None, None))
    if not table or target_id is None:
        return None
    try:
        row = await db.fetch_one(f"SELECT {col} AS n FROM {table} WHERE id=$1", target_id)
        return row["n"] if row else None
    except Exception:
        return None


def _from_response(body: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(body)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    if isinstance(data.get("id"), int):
        out["id"] = data["id"]
    name = data.get("name") or data.get("username")
    if isinstance(name, str):
        out["name"] = name[:255]
    return out


class AuditMiddleware:
    """纯 ASGI 中间件（不使用 BaseHTTPMiddleware，避免流式响应与上下文问题）"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] not in WRITE_METHODS
                or not scope["path"].startswith(API_PREFIX + "/")
                or scope["path"].startswith(SKIP_PREFIXES)):
            await self.app(scope, receive, send)
            return

        scope[_SCOPE_KEY] = {}
        state = {"status": 500, "body": b"", "capture": True}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                ctype = dict(message.get("headers") or []).get(b"content-type", b"")
                state["capture"] = ctype.startswith(b"application/json")
            elif message["type"] == "http.response.body" and state["capture"]:
                if len(state["body"]) < _MAX_CAPTURE:
                    state["body"] += message.get("body", b"")
            await send(message)

        # 执行前解析目标名称（删除后就查不到了）
        rel = scope["path"][len(API_PREFIX):]
        segment = rel.strip("/").split("/")[0]
        parts = rel.strip("/").split("/")
        pre_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        pre_name = await _target_name(segment, pre_id)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                await self._write(scope, state, rel, segment, pre_id, pre_name)
            except Exception as e:  # 审计失败不能影响业务
                logger.error(f"写入审计日志失败：{e}")

    async def _write(self, scope, state, rel, segment, target_id, target_name):
        route = scope.get("route")
        template = getattr(route, "path_format", None) or getattr(route, "path", None) or scope["path"]
        if template.startswith(API_PREFIX):
            template = template[len(API_PREFIX):]
        method = scope["method"]
        action = ACTIONS.get((method, template), f"{method} {template}")

        headers = dict(scope.get("headers") or [])
        actor = _actor(headers)
        note = dict(scope.get(_SCOPE_KEY) or {})
        status = state["status"]

        if target_id is None and 200 <= status < 300 and state["body"]:
            created = _from_response(state["body"])
            target_id = created.get("id")
            target_name = target_name or created.get("name")

        username = actor.get("username") or note.pop("username", None)
        user_id = actor.get("user_id") or note.pop("user_id", None)
        note.pop("username", None)
        note.pop("user_id", None)
        client = scope.get("client")
        ip = client[0] if client else None
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1")[:255] or None

        await db.execute(
            """
            INSERT INTO audit_logs (user_id, username, action, method, path, target_type, target_id,
                                    target_name, status_code, success, ip, forwarded_for, detail)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """,
            user_id, (username or None) and str(username)[:100], action, method,
            rel[:255], TARGETS.get(segment, (segment,))[0], target_id,
            target_name and str(target_name)[:255], status, 200 <= status < 300,
            ip, forwarded, note or None,
        )


async def purge_old_audit_logs() -> None:
    days = max(1, int(settings.AUDIT_RETENTION_DAYS))
    await db.execute(
        "DELETE FROM audit_logs WHERE created_at < NOW() - ($1::int * INTERVAL '1 day')", days
    )
    logger.info(f"已清理超过 {days} 天的审计日志")
