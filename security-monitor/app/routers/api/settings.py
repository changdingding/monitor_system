"""系统设置"""
import json
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.database import db
from app.core.security import require_admin, verify_token

router = APIRouter()


def _flatten_settings(row: Optional[dict]) -> dict:
    data = dict(row or {})
    raw = data.get("health_thresholds") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    data["cpu_threshold"] = int(raw.get("cpu", 90))
    data["memory_threshold"] = int(raw.get("memory", 90))
    data["disk_threshold"] = int(raw.get("disk", 85))
    data["offline_seconds"] = int(raw.get("offline_seconds", 180))
    if not data.get("log_retention_days"):
        data.setdefault("log_retention_days", 30)
        data.setdefault("alert_debounce_seconds", 60)
        data.setdefault("collector_interval_seconds", 300)
        data.setdefault("monitor_interval_seconds", 60)
    return data


def _threshold_payload(settings_data: dict, existing: Optional[dict]) -> dict:
    previous = {}
    if existing and existing.get("health_thresholds"):
        previous = existing["health_thresholds"]
        if isinstance(previous, str):
            try:
                previous = json.loads(previous)
            except Exception:
                previous = {}
    def pick(name, key, default):
        value = settings_data.get(name, previous.get(key, default))
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return {
        "cpu": pick("cpu_threshold", "cpu", 90),
        "memory": pick("memory_threshold", "memory", 90),
        "disk": pick("disk_threshold", "disk", 85),
        "offline_seconds": pick("offline_seconds", "offline_seconds", 180),
    }


@router.get("/settings")
async def get_settings(payload: dict = Depends(verify_token)):
    """获取系统设置"""
    row = await db.fetch_one("SELECT * FROM system_settings ORDER BY id LIMIT 1")
    if not row:
        return _flatten_settings(None)
    return _flatten_settings(dict(row))


class SettingsUpdate(BaseModel):
    log_retention_days: int = Field(default=30, ge=1, le=3650)
    alert_debounce_seconds: int = Field(default=60, ge=0, le=86400)
    collector_interval_seconds: int = Field(default=300, ge=30, le=86400)
    monitor_interval_seconds: int = Field(default=60, ge=10, le=86400)
    cpu_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    memory_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    disk_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    offline_seconds: Optional[int] = Field(default=None, ge=30, le=86400)


@router.put("/settings")
async def update_settings(body: SettingsUpdate, payload: dict = Depends(require_admin)):
    """更新系统设置"""
    settings_data = body.model_dump(exclude_none=True)
    existing = await db.fetch_one("SELECT * FROM system_settings ORDER BY id LIMIT 1")
    thresholds = _threshold_payload(settings_data, dict(existing) if existing else None)
    retention = settings_data.get("log_retention_days", 30)
    debounce = settings_data.get("alert_debounce_seconds", 60)
    collector = settings_data.get("collector_interval_seconds", 300)
    monitor = settings_data.get("monitor_interval_seconds", 60)

    if existing:
        row = await db.fetch_one("""
            UPDATE system_settings
            SET log_retention_days=$1, alert_debounce_seconds=$2, collector_interval_seconds=$3,
                monitor_interval_seconds=$4, health_thresholds=$5, updated_at=NOW()
            WHERE id=$6 RETURNING *
        """, retention, debounce, collector, monitor, thresholds, existing["id"])
    else:
        row = await db.fetch_one("""
            INSERT INTO system_settings
                (log_retention_days, alert_debounce_seconds, collector_interval_seconds, monitor_interval_seconds, health_thresholds)
            VALUES ($1, $2, $3, $4, $5) RETURNING *
        """, retention, debounce, collector, monitor, thresholds)

    # 采集 / 巡检间隔立即生效
    from app.scheduler import scheduler
    scheduler.apply_intervals(collector, monitor)

    return _flatten_settings(dict(row))
