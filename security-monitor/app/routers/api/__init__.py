"""
管理端 API（/api/v1 下除 agent / probe 外的全部接口），按业务拆分为子模块。
"""
from fastapi import APIRouter

from app.core.security import require_admin, require_operator, verify_token  # noqa: F401  兼容旧导入

from . import (
    ai,
    alerts,
    audit_logs,
    auth,
    dashboard,
    feishu,
    log_analysis,
    network_devices,
    servers,
    settings,
    sites,
    system,
    users,
)

router = APIRouter()
for _module in (auth, dashboard, sites, servers, network_devices, alerts, feishu, users, audit_logs, settings,
                ai, log_analysis, system):
    router.include_router(_module.router)
