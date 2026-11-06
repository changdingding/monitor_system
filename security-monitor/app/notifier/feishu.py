"""
飞书告警推送模块（支持自定义模板）
"""
import hashlib
import hmac
import base64
import time
import re
import logging
from typing import Optional, Dict, Any, List
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

# 默认模板（可用 {{变量}}）
DEFAULT_TEMPLATES = {
    "login_title": "🔔 {{action_text}}提醒 - {{server_name}}",
    "login_content": (
        "**告警动作**: {{action_text}}\n"
        "🖥️ **服务器**: {{server_name}} ({{server_host}})\n"
        "👤 **账户**: {{account}}\n"
        "🧾 **操作**: {{action_text}}\n"
        "⏰ **发生时间**: {{login_time}}\n"
        "🌐 **来源 IP**: {{source_ip}}\n"
        "📌 **说明**: 按账号会话状态告警：登录推一次，保持登录不重复推，退出再推一次"
    ),
    "high_risk_title": "{{emoji}} 高危操作告警 - {{server_name}}",
    "high_risk_content": (
        "**告警动作**: 检测到高危操作，已触发飞书推送\n"
        "📡 **数据来源**: {{data_source}}\n"
        "🖥️ **服务器**: {{server_name}} ({{server_host}})\n"
        "⚠️ **风险等级**: {{risk_level}}\n"
        "🔧 **操作类型**: {{operation_type}}\n"
        "👤 **执行账户**: {{account}}\n"
        "⏰ **操作时间**: {{operation_time}}\n"
        "📝 **操作描述**: {{description}}\n"
        "📌 **说明**: 同一高危事件仅推送一次"
    ),
    "network_title": "🌐 网络设备告警 - {{device_name}}",
    "network_content": (
        "**告警动作**: 网络设备健康检查异常\n"
        "🏬 **门店**: {{site_name}}\n"
        "🖥️ **设备**: {{device_name}} ({{device_host}})\n"
        "⚠️ **告警类型**: {{alert_type}}\n"
        "🚨 **严重程度**: {{severity}}\n"
        "📝 **告警内容**: {{message}}"
    ),
}


def format_event_time(value: Any) -> str:
    """规范化时间，兼容 Windows /Date(ms)/ 格式"""
    if value is None or value == "":
        return "未知"
    text = str(value).strip()
    m = re.search(r"/Date\((-?\d+)([+-]\d{4})?\)/", text)
    if m:
        ms = int(m.group(1))
        from datetime import datetime, timezone, timedelta
        tz = timezone.utc
        if m.group(2):
            sign = 1 if m.group(2)[0] == "+" else -1
            hh = int(m.group(2)[1:3])
            mm = int(m.group(2)[3:5])
            tz = timezone(sign * timedelta(hours=hh, minutes=mm))
        dt = datetime.fromtimestamp(ms / 1000, tz=tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    # ISO / 常见格式裁剪
    if "T" in text:
        return text.replace("T", " ").replace("Z", "").split(".")[0]
    return text


def render_template(template: str, variables: Dict[str, Any]) -> str:
    """渲染 {{var}} 模板，未知变量保留原样"""
    if not template:
        return ""
    safe = {k: ("" if v is None else str(v)) for k, v in variables.items()}

    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        return safe.get(key, match.group(0))

    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", repl, template)


class FeishuNotifier:
    """飞书通知器"""

    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL or ""
        self.secret = settings.FEISHU_SECRET or ""
        self.enabled = bool(self.webhook_url)
        self.notify_levels: List[str] = ["critical", "high", "medium"]
        self.debounce_seconds = 60
        self.notify_login = True
        self.notify_high_risk = True
        self.notify_network = True
        self.append_details = False
        self.templates = dict(DEFAULT_TEMPLATES)

    async def refresh_config(self):
        """从数据库加载飞书配置"""
        try:
            from app.core.database import db
            row = await db.fetch_one("SELECT * FROM feishu_config ORDER BY id LIMIT 1")
            if not row:
                return
            self.webhook_url = row.get("webhook_url") or self.webhook_url
            self.secret = row.get("secret") or ""
            self.enabled = bool(row.get("enabled")) and bool(self.webhook_url)
            levels = row.get("notify_levels") or ["critical", "high", "medium"]
            if isinstance(levels, str):
                import json
                levels = json.loads(levels)
            self.notify_levels = list(levels)
            self.debounce_seconds = int(row.get("debounce_seconds") or 60)
            self.notify_login = bool(row.get("notify_login", True))
            self.notify_high_risk = bool(row.get("notify_high_risk", True))
            self.notify_network = bool(row.get("notify_network", True))
            self.append_details = bool(row.get("append_details", False))
            self.templates = {
                "login_title": row.get("template_login_title") or DEFAULT_TEMPLATES["login_title"],
                "login_content": row.get("template_login_content") or DEFAULT_TEMPLATES["login_content"],
                "high_risk_title": row.get("template_high_risk_title") or DEFAULT_TEMPLATES["high_risk_title"],
                "high_risk_content": row.get("template_high_risk_content") or DEFAULT_TEMPLATES["high_risk_content"],
                "network_title": row.get("template_network_title") or DEFAULT_TEMPLATES["network_title"],
                "network_content": row.get("template_network_content") or DEFAULT_TEMPLATES["network_content"],
            }
        except Exception as e:
            logger.warning(f"加载飞书配置失败，使用默认值：{e}")

    def _generate_sign(self, timestamp: str) -> str:
        if not self.secret:
            return ""
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    async def send_card_message(
        self,
        title: str,
        content: str,
        severity: str = "info",
        details: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> bool:
        await self.refresh_config()

        if not force:
            if not self.enabled:
                logger.warning("飞书告警未启用或未配置 Webhook")
                return False
            if severity not in ("info",) and severity not in self.notify_levels:
                logger.info(f"告警级别 {severity} 不在推送列表中，跳过")
                return False

        if not self.webhook_url:
            logger.warning("飞书 Webhook URL 未配置")
            return False

        color_map = {
            "critical": "red",
            "high": "orange",
            "medium": "yellow",
            "low": "blue",
            "info": "green",
        }
        color = color_map.get(severity, "blue")
        # 飞书标题长度限制
        title = (title or "安全监控告警")[:80]

        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color,
                },
                "elements": [{"tag": "markdown", "content": content}],
            },
        }

        if self.append_details and details:
            detail_text = "\n".join([f"**{k}**: {v}" for k, v in details.items()])
            card["card"]["elements"].append({
                "tag": "markdown",
                "content": f"\n---\n{detail_text}",
            })

        # 明确标注「飞书告警动作」页脚
        card["card"]["elements"].append({
            "tag": "note",
            "elements": [{
                "tag": "plain_text",
                "content": "来源：安全监控系统 · 动作：飞书 Webhook 告警推送",
            }],
        })

        try:
            if self.secret:
                timestamp = str(int(time.time()))
                card["timestamp"] = timestamp
                card["sign"] = self._generate_sign(timestamp)

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.webhook_url,
                    json=card,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                result = response.json()
                if result.get("code") == 0 or result.get("StatusCode") == 0:
                    logger.info(f"飞书消息发送成功：{title}")
                    return True
                logger.error(f"飞书消息发送失败：{result}")
                return False
        except Exception as e:
            logger.error(f"飞书消息发送异常：{e}")
            return False

    async def send_test_message(self) -> bool:
        await self.refresh_config()
        vars_map = {
            "server_name": "测试服务器",
            "server_host": "127.0.0.1",
            "account": "admin",
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_ip": "10.0.0.1",
            "action": "login",
            "action_text": "账号登录",
        }
        title = render_template(self.templates["login_title"], vars_map)
        content = render_template(self.templates["login_content"], vars_map)
        content = f"**[测试消息]** 若看到本条，说明 Webhook 与模板配置正常。\n\n{content}"
        return await self.send_card_message(title=title, content=content, severity="info", force=True)

    async def send_login_alert(
        self,
        server_name: str,
        server_host: str,
        account: str,
        login_time: str,
        source_ip: str,
        action: str = "login",
        action_text: str = "账号登录",
    ) -> bool:
        await self.refresh_config()
        if not self.notify_login:
            logger.info("登录告警推送已关闭，跳过飞书")
            return False

        login_time = format_event_time(login_time)
        if source_ip in ("-", "::1", "127.0.0.1", ""):
            source_ip = source_ip or "未知"
        vars_map = {
            "server_name": server_name,
            "server_host": server_host,
            "account": account,
            "login_time": login_time,
            "source_ip": source_ip or "未知",
            "action": action,
            "action_text": action_text or ("账号登录" if action == "login" else "账号退出"),
        }
        title = render_template(self.templates["login_title"], vars_map)
        content = render_template(self.templates["login_content"], vars_map)
        return await self.send_card_message(
            title=title,
            content=content,
            severity="medium",
            details={
                "告警动作": f"{vars_map['action_text']} → 飞书推送（仅一次）",
                "服务器": server_name,
                "主机": server_host,
                "账户": account,
                "操作": vars_map["action_text"],
                "时间": login_time,
                "IP": source_ip or "未知",
            },
        )

    async def send_high_risk_alert(
        self,
        server_name: str,
        server_host: str,
        operation_type: str,
        risk_level: str,
        account: str,
        operation_time: str,
        description: str,
        data_source: str = "未知",
    ) -> bool:
        await self.refresh_config()
        if not self.notify_high_risk:
            logger.info("高危操作告警推送已关闭，跳过飞书")
            return False

        emoji_map = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        op_label_map = {
            "user_create": "创建用户账户",
            "user_change": "修改用户账户",
            "service_install": "安装系统服务",
            "scheduled_task": "创建计划任务",
            "group_member_change": "修改特权组成员",
            "log_clear": "清空安全日志",
            "sudo_command": "执行 sudo 命令",
            "unknown": "未知高危操作",
        }
        operation_time = format_event_time(operation_time)
        op_label = op_label_map.get(operation_type, operation_type or "未知操作")
        # 兼容旧 Agent：原始事件描述乱码（大量 ?）时生成可读文案
        desc = (description or "").strip()
        if not desc or desc.count("?") >= 5 or "BinaryLength" in desc or "AccountDomainSid" in desc:
            who = account or "未知账户"
            if who.startswith("{") or "BinaryLength" in who:
                who = "未知账户"
            if operation_type == "user_create":
                desc = f"检测到创建用户账户（执行账户：{who}）"
            else:
                desc = f"检测到{op_label}（相关账户：{who}）"
        # 去掉事件原文里无意义的换行噪音
        desc = " ".join(desc.split())
        if isinstance(account, dict) or str(account).startswith("{"):
            account = "未知账户"
        source_label = data_source or "未知"
        if source_label in ("ssh", "SSH"):
            source_label = "SSH 服务端拉取"
        elif source_label in ("agent", "Agent"):
            source_label = "Agent 客户端上报"

        vars_map = {
            "emoji": emoji_map.get(risk_level, "⚪"),
            "server_name": server_name,
            "server_host": server_host,
            "operation_type": op_label,
            "operation_type_raw": operation_type,
            "risk_level": (risk_level or "").upper(),
            "account": account or "未知",
            "operation_time": operation_time,
            "description": desc,
            "data_source": source_label,
        }
        title = render_template(self.templates["high_risk_title"], vars_map)
        content = render_template(self.templates["high_risk_content"], vars_map)
        return await self.send_card_message(
            title=title,
            content=content,
            severity=risk_level,
            details={
                "告警动作": "高危操作检测 → 飞书推送",
                "数据来源": source_label,
                "服务器": server_name,
                "主机": server_host,
                "风险等级": (risk_level or "").upper(),
                "操作类型": op_label,
                "执行账户": account or "未知",
                "时间": operation_time,
            },
        )

    async def send_network_alert(
        self,
        device_name: str,
        device_host: str,
        alert_type: str,
        severity: str,
        message: str,
        site_name: str = "",
        source_name: str = "",
        host: str = "",
    ) -> bool:
        await self.refresh_config()
        if not self.notify_network:
            logger.info("网络告警推送已关闭，跳过飞书")
            return False

        device_name = device_name or source_name or "未知设备"
        device_host = device_host or host or ""
        site_label = site_name or "未分组"
        vars_map = {
            "device_name": device_name,
            "device_host": device_host,
            "alert_type": alert_type,
            "severity": (severity or "").upper(),
            "message": message,
            "site_name": site_label,
        }
        title = render_template(self.templates["network_title"], vars_map)
        content = render_template(self.templates["network_content"], vars_map)
        template = self.templates.get("network_content") or ""
        if site_name and "site_name" not in template and site_name not in content:
            content = f"🏬 **门店**: {site_label}\n{content}"
        return await self.send_card_message(
            title=title,
            content=content,
            severity=severity,
            details={
                "告警动作": "网络设备监控 → 飞书推送",
                "设备": device_name,
                "主机": device_host,
                "类型": alert_type,
                "级别": severity,
            },
        )


notifier = FeishuNotifier()
