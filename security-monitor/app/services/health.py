"""
设备健康告警：离线、阈值、端口。
同一问题只推一次，恢复时再推一条。
"""
import logging
import time
from typing import Any, Optional

from app.core.config import settings
from app.core.database import db
from app.notifier.feishu import notifier

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "cpu": 90,
    "memory": 90,
    "disk": 85,
    "offline_seconds": 180,
}

_METRIC_LABELS = {
    "cpu": "CPU 使用率",
    "memory": "内存使用率",
    "disk": "磁盘使用率",
}


async def get_thresholds() -> dict:
    try:
        row = await db.fetch_one(
            "SELECT health_thresholds FROM system_settings ORDER BY id LIMIT 1"
        )
    except Exception as e:
        logger.warning(f"读取健康阈值失败，使用默认值：{e}")
        return dict(DEFAULT_THRESHOLDS)
    raw = (row or {}).get("health_thresholds") or {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    merged = dict(DEFAULT_THRESHOLDS)
    for key in DEFAULT_THRESHOLDS:
        if raw.get(key) is not None:
            try:
                merged[key] = int(float(raw[key]))
            except (TypeError, ValueError):
                pass
    return merged


async def sync_condition(
    *,
    event_key: str,
    active: bool,
    site_id: Optional[int],
    source_type: str,
    source_id: int,
    source_name: str,
    host: str,
    alert_type: str,
    severity: str,
    message: str,
    site_name: str = "",
) -> None:
    """active=True 时若尚未告警则推送；active=False 时若告警仍开着则推恢复。"""
    try:
        state = await db.fetch_one(
            "SELECT * FROM health_alert_state WHERE event_key=$1",
            event_key,
        )
        if active:
            if state and state.get("active"):
                return
            notified = await _notify(
                source_name=source_name,
                host=host,
                alert_type=alert_type,
                severity=severity,
                message=message,
                site_name=site_name,
            )
            alert_id = await _insert_alert(
                source_type=source_type,
                source_id=source_id,
                source_name=source_name,
                level=severity,
                event_type=alert_type,
                message=message,
                site_id=site_id,
                notified=notified,
                event_key=f"{event_key}:open:{int(time.time() * 1000)}",
                resolved=False,
            )
            await _upsert_state(
                event_key=event_key,
                site_id=site_id,
                source_type=source_type,
                source_id=source_id,
                alert_type=alert_type,
                active=True,
                severity=severity,
                message=message,
                alert_log_id=alert_id,
            )
            return

        if not state or not state.get("active"):
            return
        prev = state.get("message") or message
        recovery = f"已恢复：{prev}"
        notified = await _notify(
            source_name=source_name or "健康告警",
            host=host,
            alert_type="恢复",
            severity="info",
            message=recovery,
            site_name=site_name,
        )
        if state.get("alert_log_id"):
            await db.execute(
                """
                UPDATE alert_logs
                SET status='resolved', resolved_at=NOW()
                WHERE id=$1 AND status IN ('new', 'acknowledged')
                """,
                state["alert_log_id"],
            )
        await _insert_alert(
            source_type=state.get("source_type") or source_type,
            source_id=int(state.get("source_id") or source_id),
            source_name=source_name,
            level="low",
            event_type=f"{alert_type}_recovered",
            message=recovery,
            site_id=state.get("site_id") if state.get("site_id") is not None else site_id,
            notified=notified,
            event_key=f"{event_key}:recover:{int(time.time() * 1000)}",
            resolved=True,
        )
        await db.execute(
            """
            UPDATE health_alert_state
            SET active=FALSE, resolved_at=NOW(), updated_at=NOW(), message=$2
            WHERE event_key=$1
            """,
            event_key,
            recovery,
        )
    except Exception as e:
        logger.error(f"健康告警处理失败 {event_key}: {e}")


async def check_resource_thresholds(
    *,
    source_type: str,
    source_id: int,
    source_name: str,
    host: str,
    site_id: Optional[int],
    site_name: str,
    cpu: Optional[float],
    memory: Optional[float],
    disk: Optional[float],
    key_prefix: str,
) -> None:
    limits = await get_thresholds()
    values = {"cpu": cpu, "memory": memory, "disk": disk}
    for metric, value in values.items():
        if value is None:
            continue
        limit = limits[metric]
        over = float(value) >= float(limit)
        label = _METRIC_LABELS[metric]
        await sync_condition(
            event_key=f"{key_prefix}:{metric}",
            active=over,
            site_id=site_id,
            source_type=source_type,
            source_id=source_id,
            source_name=source_name,
            host=host,
            alert_type=f"high_{metric}",
            severity="high",
            message=f"{label} {float(value):.1f}% 达到阈值 {limit}%",
            site_name=site_name or "",
        )


async def clear_resource_alerts(
    *,
    key_prefix: str,
    source_type: str,
    source_id: int,
    source_name: str,
    host: str,
    site_id: Optional[int],
    site_name: str,
) -> None:
    for metric in ("cpu", "memory", "disk"):
        await sync_condition(
            event_key=f"{key_prefix}:{metric}",
            active=False,
            site_id=site_id,
            source_type=source_type,
            source_id=source_id,
            source_name=source_name,
            host=host,
            alert_type=f"high_{metric}",
            severity="high",
            message=f"{_METRIC_LABELS[metric]}已恢复",
            site_name=site_name or "",
        )


async def scan_offline() -> None:
    """采集器、服务器、网络设备离线扫描。采集器中断时不把全店网络设备打成单台故障。"""
    limits = await get_thresholds()
    seconds = int(limits["offline_seconds"])

    sites = await db.fetch_all("SELECT * FROM sites")
    probe_online: dict[int, bool] = {}
    for site in sites:
        site_id = site["id"]
        # 云端站点、总部平台直接轮询的站点没有采集器
        if (site.get("site_type") or "store") == "cloud" or (site.get("collect_mode") or "probe") == "central":
            probe_online[site_id] = True
            continue
        phase = await _phase("sites", site_id, seconds, ["last_seen"])
        if phase == "stale":
            await db.execute(
                "UPDATE sites SET status='offline', updated_at=NOW() WHERE id=$1",
                site_id,
            )
        elif phase == "fresh":
            await db.execute(
                "UPDATE sites SET status='online', updated_at=NOW() WHERE id=$1",
                site_id,
            )
        probe_online[site_id] = phase == "fresh"
        if phase == "pending":
            continue
        await sync_condition(
            event_key=f"site:{site_id}:probe_offline",
            active=phase == "stale",
            site_id=site_id,
            source_type="site",
            source_id=site_id,
            source_name=site["name"],
            host=site.get("city") or "",
            alert_type="probe_offline",
            severity="critical",
            message=f"站点「{site['name']}」边缘采集器超过 {seconds} 秒无心跳，店内网络设备暂停逐台离线判断",
            site_name=site["name"],
        )

    servers = await db.fetch_all(
        """
        SELECT s.id, s.name, s.ip, s.site_id, si.name AS site_name
        FROM servers s
        LEFT JOIN sites si ON si.id = s.site_id
        """
    )
    for server in servers:
        phase = await _phase(
            "servers", server["id"], seconds,
            ["last_seen", "agent_last_seen", "last_heartbeat"],
        )
        if phase == "pending":
            continue
        if phase == "stale":
            await db.execute(
                "UPDATE servers SET status='offline', updated_at=NOW() WHERE id=$1",
                server["id"],
            )
        else:
            await db.execute(
                """
                UPDATE servers SET status='online', updated_at=NOW()
                WHERE id=$1 AND status='offline'
                """,
                server["id"],
            )
        await sync_condition(
            event_key=f"server:{server['id']}:offline",
            active=phase == "stale",
            site_id=server.get("site_id"),
            source_type="server",
            source_id=server["id"],
            source_name=server["name"],
            host=server.get("ip") or "",
            alert_type="offline",
            severity="critical",
            message=f"服务器超过 {seconds} 秒无心跳",
            site_name=server.get("site_name") or "",
        )
        if phase == "stale":
            await clear_resource_alerts(
                key_prefix=f"server:{server['id']}",
                source_type="server",
                source_id=server["id"],
                source_name=server["name"],
                host=server.get("ip") or "",
                site_id=server.get("site_id"),
                site_name=server.get("site_name") or "",
            )

    devices = await db.fetch_all(
        """
        SELECT d.id, d.name, d.ip, d.site_id, d.status, si.name AS site_name, si.site_type
        FROM network_devices d
        LEFT JOIN sites si ON si.id = d.site_id
        WHERE d.site_id IS NOT NULL AND COALESCE(si.collect_mode, 'probe') <> 'central'
        """
    )
    for device in devices:
        site_id = device.get("site_id")
        site_type = device.get("site_type") or "store"
        # 门店采集器自己断了时，网络设备没有新数据，不能当成每台都坏了
        if site_type != "cloud" and not probe_online.get(site_id, False):
            continue
        phase = await _phase("network_devices", device["id"], seconds, ["last_seen"])
        if phase == "pending":
            continue
        current = await db.fetch_one("SELECT status FROM network_devices WHERE id=$1", device["id"])
        # 采集器刚报过「不可达」时 last_seen 仍是新的，不能当成恢复
        reported_down = ((current or {}).get("status") or "") == "offline"
        down = phase == "stale" or reported_down
        if phase == "stale":
            await db.execute(
                "UPDATE network_devices SET status='offline', updated_at=NOW() WHERE id=$1",
                device["id"],
            )
        elif not reported_down:
            await db.execute(
                """
                UPDATE network_devices SET status='online', updated_at=NOW()
                WHERE id=$1 AND status='offline'
                """,
                device["id"],
            )
        await sync_condition(
            event_key=f"device:{device['id']}:offline",
            active=down,
            site_id=site_id,
            source_type="network",
            source_id=device["id"],
            source_name=device["name"],
            host=device.get("ip") or "",
            alert_type="offline",
            severity="critical",
            message="网络设备不可达" if reported_down else f"网络设备超过 {seconds} 秒无上报",
            site_name=device.get("site_name") or "",
        )
        if phase == "stale":
            await clear_resource_alerts(
                key_prefix=f"device:{device['id']}",
                source_type="network",
                source_id=device["id"],
                source_name=device["name"],
                host=device.get("ip") or "",
                site_id=site_id,
                site_name=device.get("site_name") or "",
            )


async def purge_old_metrics() -> None:
    row = await db.fetch_one(
        "SELECT log_retention_days FROM system_settings ORDER BY id LIMIT 1"
    )
    days = int((row or {}).get("log_retention_days") or 30)
    if days < 1:
        days = 30
    await db.execute(
        "DELETE FROM host_metrics WHERE collected_at < NOW() - ($1::int * INTERVAL '1 day')",
        days,
    )
    await db.execute(
        "DELETE FROM snmp_metrics WHERE collected_at < NOW() - ($1::int * INTERVAL '1 day')",
        days,
    )
    # 原始日志与推送记录同样按“日志保留天数”清理
    for table, column in (("login_logs", "created_at"), ("high_risk_operations", "created_at"),
                          ("syslog_logs", "received_at"), ("alert_records", "created_at"),
                          ("log_analyses", "created_at")):
        await db.execute(
            f"DELETE FROM {table} WHERE {column} < NOW() - ($1::int * INTERVAL '1 day')", days
        )
    # 告警历史用于事后追溯，单独设置更长的保留期（不短于日志保留天数）
    alert_days = max(days, int(settings.ALERT_RETENTION_DAYS))
    await db.execute(
        "DELETE FROM alert_logs WHERE created_at < NOW() - ($1::int * INTERVAL '1 day')", alert_days
    )
    logger.info(f"已清理超过 {days} 天的指标与日志、超过 {alert_days} 天的告警历史")


async def _phase(table: str, row_id: int, seconds: int, seen_columns: list) -> str:
    """pending=刚建档还没有上报；stale=超时；fresh=近期有上报。"""
    if table not in ("sites", "servers", "network_devices"):
        raise ValueError(table)
    seen_expr = ", ".join(seen_columns)
    row = await db.fetch_one(
        f"""
        SELECT CASE
            WHEN COALESCE({seen_expr}) IS NULL
                 AND created_at >= NOW() - ($2::int * INTERVAL '1 second') THEN 'pending'
            WHEN COALESCE({seen_expr}, created_at) < NOW() - ($2::int * INTERVAL '1 second') THEN 'stale'
            ELSE 'fresh'
        END AS phase
        FROM {table}
        WHERE id = $1
        """,
        row_id,
        seconds,
    )
    if not row:
        return "stale"
    return row["phase"] or "stale"


async def _notify(**kwargs: Any) -> bool:
    try:
        return await notifier.send_network_alert(
            device_name=kwargs.get("source_name") or "",
            device_host=kwargs.get("host") or "",
            alert_type=kwargs.get("alert_type") or "",
            severity=kwargs.get("severity") or "medium",
            message=kwargs.get("message") or "",
            site_name=kwargs.get("site_name") or "",
        )
    except Exception as e:
        logger.error(f"飞书健康告警发送失败：{e}")
        return False


async def _insert_alert(
    *,
    source_type: str,
    source_id: int,
    source_name: str,
    level: str,
    event_type: str,
    message: str,
    site_id: Optional[int],
    notified: bool,
    event_key: str,
    resolved: bool,
) -> Optional[int]:
    status = "resolved" if resolved else "new"
    row = await db.fetch_one(
        """
        INSERT INTO alert_logs
            (source_type, source_id, source_name, level, event_type, event_description,
             raw_data, status, notified, event_key, site_id, resolved_at)
        VALUES ($1, $2, $3, $4, $5, $6, '', $7, $8, $9, $10,
                CASE WHEN $7 = 'resolved' THEN NOW() ELSE NULL END)
        RETURNING id
        """,
        source_type,
        source_id,
        source_name,
        level,
        event_type,
        message,
        status,
        notified,
        event_key,
        site_id,
    )
    return row["id"] if row else None


async def _upsert_state(**fields: Any) -> None:
    await db.execute(
        """
        INSERT INTO health_alert_state
            (event_key, site_id, source_type, source_id, alert_type, active, severity,
             message, alert_log_id, opened_at, resolved_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, TRUE, $6, $7, $8, NOW(), NULL, NOW())
        ON CONFLICT (event_key) DO UPDATE SET
            site_id = EXCLUDED.site_id,
            source_type = EXCLUDED.source_type,
            source_id = EXCLUDED.source_id,
            alert_type = EXCLUDED.alert_type,
            active = TRUE,
            severity = EXCLUDED.severity,
            message = EXCLUDED.message,
            alert_log_id = EXCLUDED.alert_log_id,
            opened_at = NOW(),
            resolved_at = NULL,
            updated_at = NOW()
        """,
        fields["event_key"],
        fields["site_id"],
        fields["source_type"],
        fields["source_id"],
        fields["alert_type"],
        fields["severity"],
        fields["message"],
        fields["alert_log_id"],
    )
