"""飞书配置"""
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.database import db
from app.core.security import require_admin, verify_token

router = APIRouter()


from app.notifier.feishu import DEFAULT_TEMPLATES


def _default_feishu_config() -> dict:
    return {
        "webhook_url": "",
        "secret": "",
        "enabled": False,
        "notify_levels": ["critical", "high", "medium"],
        "debounce_seconds": 60,
        "notify_login": True,
        "notify_high_risk": True,
        "notify_network": True,
        "append_details": False,
        "template_login_title": DEFAULT_TEMPLATES["login_title"],
        "template_login_content": DEFAULT_TEMPLATES["login_content"],
        "template_high_risk_title": DEFAULT_TEMPLATES["high_risk_title"],
        "template_high_risk_content": DEFAULT_TEMPLATES["high_risk_content"],
        "template_network_title": DEFAULT_TEMPLATES["network_title"],
        "template_network_content": DEFAULT_TEMPLATES["network_content"],
    }


@router.get("/feishu/config")
async def get_feishu_config(payload: dict = Depends(require_admin)):
    """获取飞书配置（含自定义模板）"""
    row = await db.fetch_one("SELECT * FROM feishu_config ORDER BY id LIMIT 1")
    defaults = _default_feishu_config()
    if not row:
        return defaults
    data = dict(row)
    for key, value in defaults.items():
        if data.get(key) in (None, ""):
            data[key] = value
    return data


_FEISHU_WEBHOOK_RE = re.compile(
    r"^https://open\.(feishu\.cn|larksuite\.com)/open-apis/bot/v2/hook/[A-Za-z0-9\-]+$"
)


class FeishuConfigUpdate(BaseModel):
    webhook_url: Optional[str] = ""
    secret: Optional[str] = ""
    enabled: Optional[bool] = False
    notify_levels: Optional[List[Literal["critical", "high", "medium", "low"]]] = None
    debounce_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    notify_login: Optional[bool] = None
    notify_high_risk: Optional[bool] = None
    notify_network: Optional[bool] = None
    append_details: Optional[bool] = None
    template_login_title: Optional[str] = Field(default=None, max_length=500)
    template_login_content: Optional[str] = Field(default=None, max_length=10000)
    template_high_risk_title: Optional[str] = Field(default=None, max_length=500)
    template_high_risk_content: Optional[str] = Field(default=None, max_length=10000)
    template_network_title: Optional[str] = Field(default=None, max_length=500)
    template_network_content: Optional[str] = Field(default=None, max_length=10000)

    @field_validator("webhook_url")
    @classmethod
    def _check_webhook(cls, v):
        v = (v or "").strip()
        if v and not _FEISHU_WEBHOOK_RE.match(v):
            raise ValueError("Webhook 地址必须是飞书/Lark 机器人地址（https://open.feishu.cn/open-apis/bot/v2/hook/...）")
        return v


@router.put("/feishu/config")
async def update_feishu_config(body: FeishuConfigUpdate, payload: dict = Depends(require_admin)):
    """更新飞书配置与告警模板"""
    config = body.model_dump(exclude_none=True)
    existing = await db.fetch_one("SELECT * FROM feishu_config ORDER BY id LIMIT 1")
    notify_levels = config.get("notify_levels") or ["critical", "high", "medium"]
    defaults = _default_feishu_config()

    values = {
        "webhook_url": config.get("webhook_url") or "",
        "secret": config.get("secret") or "",
        "enabled": bool(config.get("enabled")),
        "notify_levels": notify_levels,
        "debounce_seconds": int(config.get("debounce_seconds") or 60),
        "notify_login": bool(config.get("notify_login", True)),
        "notify_high_risk": bool(config.get("notify_high_risk", True)),
        "notify_network": bool(config.get("notify_network", True)),
        "append_details": bool(config.get("append_details", False)),
        "template_login_title": config.get("template_login_title") or defaults["template_login_title"],
        "template_login_content": config.get("template_login_content") or defaults["template_login_content"],
        "template_high_risk_title": config.get("template_high_risk_title") or defaults["template_high_risk_title"],
        "template_high_risk_content": config.get("template_high_risk_content") or defaults["template_high_risk_content"],
        "template_network_title": config.get("template_network_title") or defaults["template_network_title"],
        "template_network_content": config.get("template_network_content") or defaults["template_network_content"],
    }

    if existing:
        row = await db.fetch_one(
            """
            UPDATE feishu_config SET
                webhook_url=$1, secret=$2, enabled=$3, notify_levels=$4, debounce_seconds=$5,
                notify_login=$6, notify_high_risk=$7, notify_network=$8, append_details=$9,
                template_login_title=$10, template_login_content=$11,
                template_high_risk_title=$12, template_high_risk_content=$13,
                template_network_title=$14, template_network_content=$15,
                updated_at=NOW()
            WHERE id=$16
            RETURNING *
            """,
            values["webhook_url"], values["secret"], values["enabled"], values["notify_levels"],
            values["debounce_seconds"], values["notify_login"], values["notify_high_risk"],
            values["notify_network"], values["append_details"],
            values["template_login_title"], values["template_login_content"],
            values["template_high_risk_title"], values["template_high_risk_content"],
            values["template_network_title"], values["template_network_content"],
            existing["id"],
        )
    else:
        row = await db.fetch_one(
            """
            INSERT INTO feishu_config (
                webhook_url, secret, enabled, notify_levels, debounce_seconds,
                notify_login, notify_high_risk, notify_network, append_details,
                template_login_title, template_login_content,
                template_high_risk_title, template_high_risk_content,
                template_network_title, template_network_content
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            RETURNING *
            """,
            values["webhook_url"], values["secret"], values["enabled"], values["notify_levels"],
            values["debounce_seconds"], values["notify_login"], values["notify_high_risk"],
            values["notify_network"], values["append_details"],
            values["template_login_title"], values["template_login_content"],
            values["template_high_risk_title"], values["template_high_risk_content"],
            values["template_network_title"], values["template_network_content"],
        )
    return dict(row)


@router.post("/feishu/test")
async def test_feishu_webhook(payload: dict = Depends(require_admin)):
    """测试飞书 Webhook（使用当前自定义模板）"""
    from app.notifier.feishu import notifier
    try:
        ok = await notifier.send_test_message()
        if not ok:
            raise HTTPException(status_code=500, detail="测试消息发送失败，请检查 Webhook")
        return {"status": "success", "message": "测试消息已发送（已应用当前模板）"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/feishu/template-vars")
async def get_feishu_template_vars(payload: dict = Depends(verify_token)):
    """返回各告警模板可用变量说明"""
    return {
        "login": ["action", "action_text", "server_name", "server_host", "account", "login_time", "source_ip"],
        "high_risk": ["emoji", "server_name", "server_host", "risk_level", "operation_type", "account", "operation_time", "description", "data_source"],
        "network": ["device_name", "device_host", "alert_type", "severity", "message"],
        "syntax": "使用 {{变量名}} 占位，例如 {{action_text}}、{{server_name}}",
        "actions": [
            {"key": "notify_login", "name": "登录/退出告警", "trigger": "检测到账号登录或退出时推送飞书（同一事件仅一次）"},
            {"key": "notify_high_risk", "name": "高危操作告警", "trigger": "检测到 high/critical 高危操作时推送飞书（同一事件仅一次）"},
            {"key": "notify_network", "name": "网络设备告警", "trigger": "网络设备健康检查异常时推送飞书"},
        ],
    }
