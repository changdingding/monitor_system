"""仪表盘 / 态势大屏（所有数据按用户门店权限过滤）"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import db
from app.core.scope import alert_scope_filter, ensure_row_access, scope_filter, site_scope
from app.core.security import verify_token

router = APIRouter()


@router.get("/dashboard/overview")
async def dashboard_overview(payload: dict = Depends(verify_token)):
    """仪表盘概览"""
    scope = await site_scope(payload)
    server_cond, server_args = scope_filter("site_id", scope, [])
    server_stats = await db.fetch_one(f"""
        SELECT COUNT(*) AS total_servers,
               COUNT(*) FILTER (WHERE status = 'online') AS online_servers
        FROM servers WHERE {server_cond}
    """, *server_args)
    device_stats = await db.fetch_one(f"""
        SELECT COUNT(*) AS total_devices,
               COUNT(*) FILTER (WHERE status = 'online') AS online_devices,
               COUNT(*) FILTER (WHERE status = 'error') AS error_devices
        FROM network_devices WHERE {server_cond}
    """, *server_args)
    iface = await db.fetch_one(f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE i.oper_status = 'up') AS up,
               COUNT(*) FILTER (WHERE i.oper_status = 'down' AND COALESCE(i.admin_status, 'up') = 'up') AS down
        FROM interface_status i JOIN network_devices d ON d.id = i.device_id
        WHERE {scope_filter("d.site_id", scope, [])[0]}
    """, *server_args)

    alert_cond, alert_args = alert_scope_filter("", scope, [])
    alert_stats = await db.fetch_one(f"""
        SELECT
            COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) AS total_alerts_today,
            COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE AND level = 'critical') AS critical_alerts_today,
            COUNT(*) FILTER (WHERE status IN ('new', 'acknowledged')) AS unresolved_alerts,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS alerts_24h,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '48 hours'
                             AND created_at < NOW() - INTERVAL '24 hours') AS alerts_prev_24h
        FROM alert_logs WHERE {alert_cond}
    """, *alert_args)

    site_cond, site_args = scope_filter("id", scope, [])
    site_stats = await db.fetch_one(f"""
        SELECT
            COUNT(*) AS total_sites,
            COUNT(*) FILTER (WHERE site_type = 'store') AS store_sites,
            COUNT(*) FILTER (WHERE site_type = 'hq') AS hq_sites,
            COUNT(*) FILTER (WHERE site_type = 'branch') AS branch_sites,
            COUNT(*) FILTER (WHERE site_type = 'cloud') AS cloud_sites,
            -- 应有采集器的站点：门店，以及部署过采集器（有过心跳）的总部 / 分支
            COUNT(*) FILTER (WHERE site_type <> 'cloud' AND collect_mode <> 'central'
                             AND (site_type = 'store' OR last_seen IS NOT NULL)) AS probe_sites,
            COUNT(*) FILTER (WHERE site_type <> 'cloud' AND collect_mode <> 'central'
                             AND (site_type = 'store' OR last_seen IS NOT NULL) AND status = 'online') AS online_probes
        FROM sites WHERE {site_cond}
    """, *site_args)
    health_cond, health_args = scope_filter("site_id", scope, [])
    health_stats = await db.fetch_one(
        f"SELECT COUNT(*) AS active_health_alerts FROM health_alert_state WHERE active AND ({health_cond}"
        + (" OR site_id IS NULL)" if scope is None else ")"),
        *health_args,
    )

    def n(row, key):
        return int((row or {}).get(key) or 0)

    return {
        "total_servers": n(server_stats, "total_servers"),
        "online_servers": n(server_stats, "online_servers"),
        "total_devices": n(device_stats, "total_devices"),
        "online_devices": n(device_stats, "online_devices"),
        "error_devices": n(device_stats, "error_devices"),
        "interfaces_total": n(iface, "total"),
        "interfaces_up": n(iface, "up"),
        "interfaces_down": n(iface, "down"),
        "total_alerts_today": n(alert_stats, "total_alerts_today"),
        "critical_alerts_today": n(alert_stats, "critical_alerts_today"),
        "unresolved_alerts": n(alert_stats, "unresolved_alerts"),
        "alerts_24h": n(alert_stats, "alerts_24h"),
        "alerts_prev_24h": n(alert_stats, "alerts_prev_24h"),
        "total_sites": n(site_stats, "total_sites"),
        "store_sites": n(site_stats, "store_sites"),
        "hq_sites": n(site_stats, "hq_sites"),
        "branch_sites": n(site_stats, "branch_sites"),
        "cloud_sites": n(site_stats, "cloud_sites"),
        "probe_sites": n(site_stats, "probe_sites"),
        "online_probes": n(site_stats, "online_probes"),
        "active_health_alerts": n(health_stats, "active_health_alerts"),
    }


@router.get("/dashboard/alert-trend")
async def alert_trend(days: int = 7, site_id: Optional[int] = None, payload: dict = Depends(verify_token)):
    """告警趋势（补齐没有告警的日期），可按站点筛选"""
    days = max(1, min(int(days or 7), 90))
    cond, args = alert_scope_filter("a", await site_scope(payload), [days])
    if site_id:
        extra, args = alert_scope_filter("a", {int(site_id)}, args)
        cond += f" AND {extra}"
    rows = await db.fetch_all(f"""
        SELECT d::date AS date,
               COUNT(a.id) FILTER (WHERE a.level = 'critical') AS critical,
               COUNT(a.id) FILTER (WHERE a.level = 'high') AS high,
               COUNT(a.id) FILTER (WHERE a.level = 'medium') AS medium,
               COUNT(a.id) FILTER (WHERE a.level = 'low') AS low
        FROM generate_series(CURRENT_DATE - ($1::int - 1) * INTERVAL '1 day', CURRENT_DATE, INTERVAL '1 day') d
        LEFT JOIN alert_logs a ON DATE(a.created_at) = d::date AND {cond}
        GROUP BY d ORDER BY d
    """, *args)
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
    limit = max(1, min(int(limit or 10), 100))
    cond, args = alert_scope_filter("a", await site_scope(payload), [limit])
    rows = await db.fetch_all(f"""
        SELECT a.*, s.name AS site_name FROM alert_logs a
        LEFT JOIN sites s ON s.id = a.site_id
        WHERE {cond}
        ORDER BY a.created_at DESC
        LIMIT $1
    """, *args)
    return [dict(row) for row in rows]


def _with_health_score(row) -> dict:
    """在线率为主，未恢复告警和资源尖峰扣分。采集器离线时整店压到危险。"""
    item = dict(row)
    server_total = int(item.get("server_total") or 0)
    device_total = int(item.get("device_total") or 0)
    server_online = int(item.get("server_online") or 0)
    device_online = int(item.get("device_online") or 0)
    # 部署过采集器（有过心跳）的站点，采集器断开时整站压到危险；从未部署过的总部 / 分支不扣分
    site_type = item.get("site_type") or "store"
    probe_expected = site_type == "store" or (site_type in ("hq", "branch") and item.get("last_seen") is not None)
    if (item.get("collect_mode") or "probe") == "central" or site_type == "cloud":
        probe_expected = False
    probe_down = probe_expected and (item.get("status") or "") != "online"
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
    item["probe_down"] = probe_down
    return item


async def _scored_sites(payload: dict) -> list:
    cond, args = scope_filter("s.id", await site_scope(payload), [])
    rows = await db.fetch_all(f"""
        SELECT
            s.id, s.name, s.site_type, s.parent_id, s.code, s.city, s.status, s.last_seen, s.probe_version,
            s.collect_mode,
            (SELECT COUNT(*) FROM servers sv WHERE sv.site_id = s.id) AS server_total,
            (SELECT COUNT(*) FROM servers sv WHERE sv.site_id = s.id AND sv.status = 'online') AS server_online,
            (SELECT COUNT(*) FROM network_devices d WHERE d.site_id = s.id) AS device_total,
            (SELECT COUNT(*) FROM network_devices d WHERE d.site_id = s.id AND d.status = 'online') AS device_online,
            (SELECT MAX(sv.cpu) FROM servers sv WHERE sv.site_id = s.id) AS max_cpu,
            (SELECT MAX(sv.disk) FROM servers sv WHERE sv.site_id = s.id) AS max_disk,
            (SELECT COUNT(*) FROM health_alert_state h WHERE h.site_id = s.id AND h.active) AS active_alerts,
            (SELECT COUNT(*) FROM interface_status i JOIN network_devices d ON d.id = i.device_id
              WHERE d.site_id = s.id AND i.oper_status = 'down' AND COALESCE(i.admin_status, 'up') = 'up') AS if_down
        FROM sites s
        WHERE {cond}
        ORDER BY s.name
    """, *args)
    return [_with_health_score(row) for row in rows]


@router.get("/dashboard/sites")
async def dashboard_sites(payload: dict = Depends(verify_token)):
    """按站点汇总在线率、资源峰值和未恢复告警（健康分升序：问题站点排前面）"""
    ranked = await _scored_sites(payload)
    ranked.sort(key=lambda item: (item["health_score"], item["name"]))
    return ranked


@router.get("/dashboard/sites/{site_id}")
async def dashboard_site_detail(site_id: int, payload: dict = Depends(verify_token)):
    detail = await ensure_row_access(payload, "sites", site_id, "站点")
    for secret in ("probe_token", "probe_token_hash", "snmp_community"):
        detail.pop(secret, None)
    if detail.get("parent_id"):
        parent = await db.fetch_one("SELECT name FROM sites WHERE id=$1", detail["parent_id"])
        detail["parent_name"] = (parent or {}).get("name")
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
    interfaces = await db.fetch_all(
        """
        SELECT i.device_id, i.if_index, i.if_name, i.admin_status, i.oper_status, i.collected_at
        FROM interface_status i JOIN network_devices d ON d.id = i.device_id
        WHERE d.site_id=$1
        ORDER BY i.if_index NULLS LAST, i.if_name
        """,
        site_id,
    )
    by_device: dict = {}
    for item in interfaces:
        by_device.setdefault(item["device_id"], []).append(item)
    device_rows = [{**dict(device), "interfaces": by_device.get(device["id"], [])} for device in devices]
    server_rows = [dict(row) for row in servers]
    cpus = [row["cpu"] for row in server_rows if row.get("cpu") is not None]
    disks = [row["disk"] for row in server_rows if row.get("disk") is not None]
    alert_row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM health_alert_state WHERE site_id=$1 AND active",
        site_id,
    )
    children = await db.fetch_all(
        "SELECT id, name, site_type, status FROM sites WHERE parent_id=$1 ORDER BY name", site_id
    )
    scored = _with_health_score({
        "name": detail.get("name"),
        "site_type": detail.get("site_type"),
        "status": detail.get("status"),
        "last_seen": detail.get("last_seen"),
        "collect_mode": detail.get("collect_mode"),
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
    return {"site": detail, "servers": server_rows, "devices": device_rows, "children": [dict(c) for c in children]}


@router.get("/dashboard/servers/{server_id}/metrics")
async def dashboard_server_metrics(server_id: int, hours: int = 24, payload: dict = Depends(verify_token)):
    await ensure_row_access(payload, "servers", server_id, "服务器")
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


@router.get("/dashboard/devices/{device_id}/metrics")
async def dashboard_device_metrics(device_id: int, hours: int = 24, payload: dict = Depends(verify_token)):
    """网络设备 CPU / 内存曲线（按采集时间合并成一行）"""
    await ensure_row_access(payload, "network_devices", device_id, "设备")
    hours = max(1, min(int(hours or 24), 168))
    rows = await db.fetch_all(
        """
        SELECT collected_at,
               MAX(metric_value) FILTER (WHERE metric_type = 'cpu') AS cpu,
               MAX(metric_value) FILTER (WHERE metric_type = 'memory') AS memory
        FROM snmp_metrics
        WHERE device_id=$1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')
        GROUP BY collected_at ORDER BY collected_at
        """,
        device_id,
        hours,
    )
    return [dict(row) for row in rows]


@router.get("/dashboard/distribution")
async def dashboard_distribution(payload: dict = Depends(verify_token)):
    """大屏分布类数据：告警等级 / 类型、设备类型 / 厂商、资源 Top"""
    scope = await site_scope(payload)
    alert_cond, alert_args = alert_scope_filter("", scope, [])
    levels = await db.fetch_all(f"""
        SELECT level AS name, COUNT(*) AS value FROM alert_logs
        WHERE created_at >= NOW() - INTERVAL '7 days' AND {alert_cond}
        GROUP BY level
    """, *alert_args)
    event_types = await db.fetch_all(f"""
        SELECT event_type AS name, COUNT(*) AS value FROM alert_logs
        WHERE created_at >= NOW() - INTERVAL '7 days' AND {alert_cond}
        GROUP BY event_type ORDER BY value DESC LIMIT 6
    """, *alert_args)
    cond, args = scope_filter("site_id", scope, [])
    device_types = await db.fetch_all(f"""
        SELECT COALESCE(device_type, 'other') AS name, COUNT(*) AS value,
               COUNT(*) FILTER (WHERE status = 'online') AS online
        FROM network_devices WHERE {cond} GROUP BY 1 ORDER BY value DESC
    """, *args)
    vendors = await db.fetch_all(f"""
        SELECT COALESCE(vendor, 'unknown') AS name, COUNT(*) AS value
        FROM network_devices WHERE {cond} GROUP BY 1 ORDER BY value DESC
    """, *args)
    top_servers = await db.fetch_all(f"""
        SELECT s.id, s.name, s.cpu, s.memory, s.disk, si.name AS site_name
        FROM servers s LEFT JOIN sites si ON si.id = s.site_id
        WHERE {scope_filter("s.site_id", scope, [])[0]} AND s.status = 'online'
          AND GREATEST(COALESCE(s.cpu, 0), COALESCE(s.memory, 0), COALESCE(s.disk, 0)) > 0
        ORDER BY GREATEST(COALESCE(s.cpu, 0), COALESCE(s.memory, 0), COALESCE(s.disk, 0)) DESC LIMIT 6
    """, *args)
    top_devices = await db.fetch_all(f"""
        SELECT d.id, d.name, d.ip, d.cpu, d.memory, si.name AS site_name
        FROM network_devices d LEFT JOIN sites si ON si.id = d.site_id
        WHERE {scope_filter("d.site_id", scope, [])[0]} AND d.status = 'online'
          AND GREATEST(COALESCE(d.cpu, 0), COALESCE(d.memory, 0)) > 0
        ORDER BY GREATEST(COALESCE(d.cpu, 0), COALESCE(d.memory, 0)) DESC LIMIT 6
    """, *args)
    return {
        "alert_levels": [dict(r) for r in levels],
        "event_types": [dict(r) for r in event_types],
        "device_types": [dict(r) for r in device_types],
        "vendors": [dict(r) for r in vendors],
        "top_servers": [dict(r) for r in top_servers],
        "top_devices": [dict(r) for r in top_devices],
    }


_EVENT_LABELS = {
    "probe_offline": "采集器离线", "offline": "设备离线", "snmp_unreachable": "SNMP 无响应",
    "interface_down": "端口断开", "high_cpu": "CPU 过高", "high_memory": "内存过高", "high_disk": "磁盘过高",
}


@router.get("/dashboard/insights")
async def dashboard_insights(payload: dict = Depends(verify_token)):
    """
    智能研判：基于实时数据的规则化分析，给出综合健康指数、结论和处置建议。
    每条结论都来自数据库中的真实状态，link 为前端可跳转的页面。
    """
    from app.core.system_state import get_state

    scope = await site_scope(payload)
    sites = await _scored_sites(payload)
    items = []

    def add(level: str, title: str, detail: str, link: Optional[str] = None):
        items.append({"level": level, "title": title, "detail": detail, "link": link})

    probe_down = [s for s in sites if s.get("probe_down")]
    if probe_down:
        names = "、".join(s["name"] for s in probe_down[:5]) + (" 等" if len(probe_down) > 5 else "")
        add("danger", f"{len(probe_down)} 个站点与总部失联",
            f"{names} 的边缘采集器无心跳，站内网络设备状态无法上报。建议先确认门店断网 / 断电或采集电脑是否关机。",
            "/sites")
    weak = [s for s in sites if s["health_level"] == "danger" and not s.get("probe_down")]
    if weak:
        worst = weak[0] if len(weak) == 1 else min(weak, key=lambda s: s["health_score"])
        add("danger", f"{len(weak)} 个站点健康分低于 60",
            f"最低为「{worst['name']}」{worst['health_score']} 分（未恢复告警 {worst['active_alerts']} 条）。",
            f"/dashboard?site={worst['id']}")

    alert_cond, alert_args = alert_scope_filter("", scope, [])
    stale = await db.fetch_one(f"""
        SELECT COUNT(*) AS n FROM alert_logs
        WHERE level IN ('critical', 'high') AND status = 'new' AND created_at < NOW() - INTERVAL '1 hour'
          AND {alert_cond}
    """, *alert_args)
    if stale and stale["n"]:
        add("warn", f"{stale['n']} 条高危告警超过 1 小时未处理",
            "建议值班人员尽快确认或处置，避免问题扩大。", "/alerts?status=new")

    volume = await db.fetch_one(f"""
        SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS cur,
               COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '48 hours'
                                AND created_at < NOW() - INTERVAL '24 hours') AS prev
        FROM alert_logs WHERE {alert_cond}
    """, *alert_args)
    cur, prev = int(volume["cur"] or 0), int(volume["prev"] or 0)
    if cur >= 5 and cur > prev * 1.5:
        rise = "首次出现" if prev == 0 else f"较前 24 小时上升 {round((cur - prev) / prev * 100)}%"
        add("warn", f"近 24 小时告警 {cur} 条，{rise}", "告警量异常增长，可能存在集中故障或攻击行为。", "/alerts")

    noisy = await db.fetch_one(f"""
        SELECT source_name, event_type, COUNT(*) AS n FROM alert_logs
        WHERE created_at >= NOW() - INTERVAL '24 hours' AND {alert_cond}
        GROUP BY source_name, event_type ORDER BY n DESC LIMIT 1
    """, *alert_args)
    if noisy and noisy["n"] >= 3 and cur:
        label = _EVENT_LABELS.get(noisy["event_type"], noisy["event_type"])
        add("info", f"「{noisy['source_name']}」是最活跃的告警源",
            f"24 小时内触发「{label}」{noisy['n']} 次，占全部告警 {round(noisy['n'] / cur * 100)}%，建议重点排查。",
            "/alerts")

    dev_cond, dev_args = scope_filter("d.site_id", scope, [])
    ports = await db.fetch_one(f"""
        SELECT COUNT(*) AS n, COUNT(DISTINCT d.id) AS devices FROM interface_status i
        JOIN network_devices d ON d.id = i.device_id
        WHERE i.oper_status = 'down' AND COALESCE(i.admin_status, 'up') = 'up' AND {dev_cond}
    """, *dev_args)
    if ports and ports["n"]:
        add("info", f"{ports['n']} 个已启用端口处于断开状态",
            f"分布在 {ports['devices']} 台设备上。若不是空闲口，请检查网线、对端设备或光模块。", "/network-devices")
    snmp_err = await db.fetch_one(
        f"SELECT COUNT(*) AS n FROM network_devices d WHERE d.status = 'error' AND {dev_cond}", *dev_args
    )
    if snmp_err and snmp_err["n"]:
        add("warn", f"{snmp_err['n']} 台设备网络可达但 SNMP 无响应",
            "多为团体字错误或设备未开启 SNMP，可在「站点」中统一设置默认团体字后由采集器自动重试。",
            "/network-devices?status=error")

    srv_cond, srv_args = scope_filter("s.site_id", scope, [])
    disk = await db.fetch_all(f"""
        SELECT s.name, s.disk FROM servers s
        WHERE s.disk >= 80 AND s.status = 'online' AND {srv_cond} ORDER BY s.disk DESC LIMIT 3
    """, *srv_args)
    if disk:
        add("warn", f"{len(disk)} 台服务器磁盘使用率超过 80%",
            "、".join(f"{d['name']} {d['disk']:.0f}%" for d in disk) + "，建议清理日志或扩容。", "/servers")

    ai_cond = "TRUE" if scope is None else (
        f"server_id IN (SELECT id FROM servers s WHERE {srv_cond})"
    )
    ai = await db.fetch_one(f"""
        SELECT server_name, summary, risk_level FROM log_analyses
        WHERE status = 'success' AND risk_level IN ('high', 'critical')
          AND created_at >= NOW() - INTERVAL '3 days' AND {ai_cond}
        ORDER BY id DESC LIMIT 1
    """, *([] if scope is None else srv_args))
    if ai:
        add("danger" if ai["risk_level"] == "critical" else "warn",
            f"AI 日志分析：{ai['server_name'] or '粘贴日志'} 存在高风险",
            (ai["summary"] or "")[:160], "/log-analysis")

    # 综合健康指数：站点健康分均值；没有站点时退化为全局在线率
    if sites:
        score = round(sum(s["health_score"] for s in sites) / len(sites))
    else:
        overview = await dashboard_overview(payload)
        total = overview["total_servers"] + overview["total_devices"]
        online = overview["online_servers"] + overview["online_devices"]
        score = round(online / total * 100) if total else 100
    level = "good" if score >= 80 else ("warn" if score >= 60 else "danger")
    good_sites = sum(1 for s in sites if s["health_level"] == "good")
    if not items:
        add("good", "全网运行平稳", f"{len(sites)} 个站点均无未处理异常，继续保持实时监测。")
    summary = (
        f"共监测 {len(sites)} 个站点，{good_sites} 个状态良好；"
        + (f"发现 {sum(1 for i in items if i['level'] in ('danger', 'warn'))} 项需要关注的问题。"
           if any(i["level"] in ("danger", "warn") for i in items) else "未发现需要立即处置的问题。")
    )
    state = await get_state()
    if not state.get("activated"):
        add("info", "系统处于实施模式", "当前数据可由管理员清理。实施完成后请在「系统设置 → 系统启用」中正式启用。",
            "/settings")
    order = {"danger": 0, "warn": 1, "info": 2, "good": 3}
    items.sort(key=lambda i: order.get(i["level"], 9))
    return {"score": score, "level": level, "summary": summary, "items": items}
