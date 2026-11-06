"""仪表盘"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import db
from app.core.security import verify_token

router = APIRouter()


@router.get("/dashboard/overview")
async def dashboard_overview(payload: dict = Depends(verify_token)):
    """仪表盘概览"""
    # 服务器统计
    server_stats = await db.fetch_one("""
        SELECT 
            COUNT(*) as total_servers,
            COUNT(CASE WHEN status = 'online' THEN 1 END) as online_servers
        FROM servers
    """)
    
    # 网络设备统计
    device_stats = await db.fetch_one("""
        SELECT 
            COUNT(*) as total_devices,
            COUNT(CASE WHEN status = 'online' THEN 1 END) as online_devices
        FROM network_devices
    """)
    
    # 告警统计
    alert_stats = await db.fetch_one("""
        SELECT 
            COUNT(*) as total_alerts_today,
            COUNT(CASE WHEN level = 'critical' THEN 1 END) as critical_alerts_today,
            COUNT(CASE WHEN status IN ('new', 'acknowledged') THEN 1 END) as unresolved_alerts
        FROM alert_logs
        WHERE created_at >= CURRENT_DATE
    """)

    site_stats = await db.fetch_one("""
        SELECT
            COUNT(*) AS total_sites,
            COUNT(*) FILTER (WHERE site_type = 'store' AND status = 'online') AS online_probes,
            COUNT(*) FILTER (WHERE site_type = 'store') AS store_sites
        FROM sites
    """)
    health_stats = await db.fetch_one(
        "SELECT COUNT(*) AS active_health_alerts FROM health_alert_state WHERE active"
    )
    
    return {
        "total_servers": server_stats["total_servers"] or 0,
        "online_servers": server_stats["online_servers"] or 0,
        "total_devices": device_stats["total_devices"] or 0,
        "online_devices": device_stats["online_devices"] or 0,
        "total_alerts_today": alert_stats["total_alerts_today"] or 0,
        "critical_alerts_today": alert_stats["critical_alerts_today"] or 0,
        "unresolved_alerts": alert_stats["unresolved_alerts"] or 0,
        "total_sites": site_stats["total_sites"] or 0,
        "store_sites": site_stats["store_sites"] or 0,
        "online_probes": site_stats["online_probes"] or 0,
        "active_health_alerts": health_stats["active_health_alerts"] or 0,
    }


@router.get("/dashboard/alert-trend")
async def alert_trend(days: int = 7, site_id: Optional[int] = None, payload: dict = Depends(verify_token)):
    """告警趋势，可按门店筛选"""
    days = max(1, min(int(days or 7), 90))
    rows = await db.fetch_all("""
        SELECT 
            DATE(created_at) as date,
            COUNT(*) FILTER (WHERE level = 'critical') as critical,
            COUNT(*) FILTER (WHERE level = 'high') as high,
            COUNT(*) FILTER (WHERE level = 'medium') as medium,
            COUNT(*) FILTER (WHERE level = 'low') as low
        FROM alert_logs
        WHERE created_at >= CURRENT_DATE - ($1::int * INTERVAL '1 day')
          AND ($2::int IS NULL OR site_id = $2)
        GROUP BY DATE(created_at)
        ORDER BY date
    """, days, site_id)
    
    return [{
        "date": str(row["date"]),
        "critical": row["critical"] or 0,
        "high": row["high"] or 0,
        "medium": row["medium"] or 0,
        "low": row["low"] or 0,
    } for row in rows]


@router.get("/dashboard/recent-alerts")
async def recent_alerts(limit: int = 10, payload: dict = Depends(verify_token)):
    """最近告警"""
    rows = await db.fetch_all("""
        SELECT * FROM alert_logs
        ORDER BY created_at DESC
        LIMIT $1
    """, limit)
    
    return [dict(row) for row in rows]


def _with_health_score(row) -> dict:
    """在线率为主，未恢复告警和资源尖峰扣分。采集器离线时整店压到危险。"""
    item = dict(row)
    server_total = int(item.get("server_total") or 0)
    device_total = int(item.get("device_total") or 0)
    server_online = int(item.get("server_online") or 0)
    device_online = int(item.get("device_online") or 0)
    probe_down = (item.get("site_type") or "store") == "store" and (item.get("status") or "") != "online"
    weighted = 0.0
    weight = 0.0
    if server_total:
        weighted += (server_online / server_total) * 55
        weight += 55
    if device_total:
        weighted += (device_online / device_total) * 45
        weight += 45
    availability = 100.0 if weight == 0 else weighted / weight * 100
    penalty = min(40, int(item.get("active_alerts") or 0) * 8)
    cpu = item.get("max_cpu")
    disk = item.get("max_disk")
    if cpu is not None and float(cpu) >= 90:
        penalty += 10
    elif cpu is not None and float(cpu) >= 80:
        penalty += 5
    if disk is not None and float(disk) >= 90:
        penalty += 10
    elif disk is not None and float(disk) >= 85:
        penalty += 5
    if probe_down:
        availability = min(availability, 20)
    score = max(0, min(100, int(round(availability - penalty))))
    if probe_down or score < 60:
        level = "danger"
    elif score < 80:
        level = "warn"
    else:
        level = "good"
    item["health_score"] = score
    item["health_level"] = level
    return item


@router.get("/dashboard/sites")
async def dashboard_sites(payload: dict = Depends(verify_token)):
    """按门店汇总在线率、资源峰值和未恢复告警。"""
    rows = await db.fetch_all("""
        SELECT
            s.id, s.name, s.site_type, s.city, s.status, s.last_seen,
            (SELECT COUNT(*) FROM servers sv WHERE sv.site_id = s.id) AS server_total,
            (SELECT COUNT(*) FROM servers sv WHERE sv.site_id = s.id AND sv.status = 'online') AS server_online,
            (SELECT COUNT(*) FROM network_devices d WHERE d.site_id = s.id) AS device_total,
            (SELECT COUNT(*) FROM network_devices d WHERE d.site_id = s.id AND d.status = 'online') AS device_online,
            (SELECT MAX(sv.cpu) FROM servers sv WHERE sv.site_id = s.id) AS max_cpu,
            (SELECT MAX(sv.disk) FROM servers sv WHERE sv.site_id = s.id) AS max_disk,
            (SELECT COUNT(*) FROM health_alert_state h WHERE h.site_id = s.id AND h.active) AS active_alerts
        FROM sites s
        ORDER BY s.name
    """)
    ranked = [_with_health_score(row) for row in rows]
    ranked.sort(key=lambda item: (item["health_score"], item["name"]))
    return ranked


@router.get("/dashboard/sites/{site_id}")
async def dashboard_site_detail(site_id: int, payload: dict = Depends(verify_token)):
    site = await db.fetch_one("SELECT * FROM sites WHERE id=$1", site_id)
    if not site:
        raise HTTPException(status_code=404, detail="门店不存在")
    detail = dict(site)
    detail.pop("probe_token", None)
    detail.pop("probe_token_hash", None)
    servers = await db.fetch_all(
        """
        SELECT id, name, ip, os_type, status, cpu, memory, disk, last_seen, agent_last_seen
        FROM servers WHERE site_id=$1 ORDER BY name
        """,
        site_id,
    )
    devices = await db.fetch_all(
        """
        SELECT id, name, ip, device_type, vendor, status, cpu, memory, last_seen
        FROM network_devices WHERE site_id=$1 ORDER BY name
        """,
        site_id,
    )
    device_rows = []
    for device in devices:
        item = dict(device)
        item["interfaces"] = await db.fetch_all(
            """
            SELECT if_index, if_name, admin_status, oper_status, collected_at
            FROM interface_status WHERE device_id=$1
            ORDER BY if_index NULLS LAST, if_name
            """,
            device["id"],
        )
        device_rows.append(item)
    server_rows = [dict(row) for row in servers]
    cpus = [row["cpu"] for row in server_rows if row.get("cpu") is not None]
    disks = [row["disk"] for row in server_rows if row.get("disk") is not None]
    alert_row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM health_alert_state WHERE site_id=$1 AND active",
        site_id,
    )
    scored = _with_health_score({
        "name": detail.get("name"),
        "site_type": detail.get("site_type"),
        "status": detail.get("status"),
        "server_total": len(server_rows),
        "server_online": sum(1 for row in server_rows if row.get("status") == "online"),
        "device_total": len(devices),
        "device_online": sum(1 for row in devices if row.get("status") == "online"),
        "max_cpu": max(cpus) if cpus else None,
        "max_disk": max(disks) if disks else None,
        "active_alerts": int((alert_row or {}).get("n") or 0),
    })
    detail["health_score"] = scored["health_score"]
    detail["health_level"] = scored["health_level"]
    return {"site": detail, "servers": server_rows, "devices": device_rows}


@router.get("/dashboard/servers/{server_id}/metrics")
async def dashboard_server_metrics(server_id: int, hours: int = 24, payload: dict = Depends(verify_token)):
    hours = max(1, min(int(hours or 24), 168))
    rows = await db.fetch_all(
        """
        SELECT cpu, memory, disk, collected_at
        FROM host_metrics
        WHERE server_id=$1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')
        ORDER BY collected_at
        """,
        server_id,
        hours,
    )
    return [dict(row) for row in rows]
