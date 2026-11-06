"""AI 配置（仅管理员）"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.crypto import encrypt
from app.core.database import db
from app.core.security import require_admin, verify_token
from app.services.ai_analyzer import AIError, load_config, test_connection

router = APIRouter()

# 常用接口预设，前端下拉选择后自动填入
PRESETS = [
    {"key": "claude", "label": "Claude（Anthropic 官方）", "provider": "anthropic", "base_url": "", "model": "claude-opus-5-5"},
    {"key": "deepseek", "label": "DeepSeek", "provider": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"key": "qwen", "label": "通义千问（阿里云百炼）", "provider": "openai",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"key": "moonshot", "label": "Kimi（月之暗面）", "provider": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-32k"},
    {"key": "zhipu", "label": "智谱 GLM", "provider": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus"},
    {"key": "ollama", "label": "本地 Ollama（日志不出内网）", "provider": "openai",
     "base_url": "http://host.docker.internal:11434/v1", "model": "qwen2.5:14b"},
]


def _public(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k != "api_key"}


@router.get("/ai/config")
async def get_ai_config(payload: dict = Depends(require_admin)):
    """获取 AI 配置（不返回 API Key 明文，只返回是否已设置）"""
    return {**_public(await load_config()), "presets": PRESETS}


@router.get("/ai/status")
async def get_ai_status(payload: dict = Depends(verify_token)):
    """普通用户可见：AI 是否可用及当前模型"""
    cfg = await load_config()
    return {"enabled": bool(cfg["enabled"]), "provider": cfg["provider"], "model": cfg["model"]}


class AIConfigUpdate(BaseModel):
    enabled: bool = False
    provider: Literal["anthropic", "openai"] = "anthropic"
    base_url: Optional[str] = Field(default="", max_length=500)
    # 留空表示不修改已保存的 Key；clear_api_key=true 时清除
    api_key: Optional[str] = Field(default=None, max_length=500)
    clear_api_key: bool = False
    model: str = Field(default="claude-opus-5-5", min_length=1, max_length=100)
    max_input_chars: int = Field(default=60000, ge=2000, le=400000)
    timeout_seconds: int = Field(default=180, ge=10, le=900)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v):
        v = (v or "").strip().rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        return v

    @field_validator("model")
    @classmethod
    def _check_model(cls, v):
        return v.strip()


@router.put("/ai/config")
async def update_ai_config(body: AIConfigUpdate, payload: dict = Depends(require_admin)):
    """更新 AI 配置"""
    if body.provider == "openai" and not body.base_url:
        raise HTTPException(status_code=400, detail="OpenAI 兼容接口需要填写接口地址")
    existing = await db.fetch_one("SELECT id, api_key FROM ai_config ORDER BY id LIMIT 1")
    if body.clear_api_key:
        api_key = None
    elif body.api_key and body.api_key.strip():
        api_key = encrypt(body.api_key.strip())
    else:
        api_key = existing["api_key"] if existing else None

    values = (body.enabled, body.provider, body.base_url, api_key, body.model,
              body.max_input_chars, body.timeout_seconds)
    if existing:
        await db.execute(
            """
            UPDATE ai_config SET enabled=$1, provider=$2, base_url=$3, api_key=$4, model=$5,
                   max_input_chars=$6, timeout_seconds=$7, updated_at=NOW()
            WHERE id=$8
            """,
            *values, existing["id"],
        )
    else:
        await db.execute(
            """
            INSERT INTO ai_config (enabled, provider, base_url, api_key, model, max_input_chars, timeout_seconds)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            *values,
        )
    return {**_public(await load_config()), "presets": PRESETS}


@router.post("/ai/test")
async def test_ai(payload: dict = Depends(require_admin)):
    """用一段示例日志测试 AI 配置（按已保存的配置调用，未启用也可测试）"""
    cfg = await load_config(with_secret=True)
    cfg["enabled"] = True
    try:
        return await test_connection(cfg)
    except AIError as e:
        raise HTTPException(status_code=400, detail=str(e))
