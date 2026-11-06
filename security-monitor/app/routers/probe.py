"""
边缘采集器接入：拉取集中配置、心跳与网络设备上报。

设备清单、团体字、自动发现网段都在总部网页上配置，采集器每轮通过 GET /probe/config 拉取，
现场只需填写总部地址和站点 Token（安装向导里就能改）。
"""
import logging
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.database import db
from app.core.crypto import decrypt, token_hash
from app.services.health import check_resource_thresholds, clear_resource_alerts, sync_condition
from app.services.ingest_time import is_fresh, naive, parse_collected_at

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/probe", tags=["probe"])

_DEVICE_TYPES = {"switch", "router", "firewall", "ap", "other"}


class ProbeHeartbeat(BaseModel):
    hostname: Optional[str] = None
    probe_version: Optional[str] = None
    collected_at: Optional[str] = None


class InterfaceIn(BaseModel):
    if_index: Optional[int] = None
    if_name: str
    admin_status: Optional[str] = None
    oper_status: Optional[str] = None


class DeviceIn(BaseModel):
    name: str
    ip: str
    discovered: bool = False  # 由采集器网段扫描发现（总部尚未建档）
    sys_name: Optional[str] = None
    device_type: Optional[str] = "other"
    reachable: bool = False
    snmp_ok: bool = False
    cpu: Optional[float] = None
    memory: Optional[float] = None
    vendor: Optional[str] = None
    collected_at: Optional[str] = None
    interfaces: List[InterfaceIn] = Field(default_factory=list)


class ProbeReport(BaseModel):
    collected_at: Optional[str] = None
    devices: List[DeviceIn] = Field(default_factory=list)


async def _auth_probe(x_probe_token: str = Header(..., alias="X-Probe-Token")) -> dict:
    if not x_probe_token:
        raise HTTPException(status_code=401, detail="缺少采集器 Token")
    row = await db.fetch_one("SELECT * FROM sites WHERE probe_token_hash=$1", token_hash(x_probe_token))
    if not row:
        raise HTTPException(status_code=401, detail="无效的采集器 Token")
    return dict(row)


def _pct(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 100:
        return None
    return round(number, 1)


async def _touch_site(site: dict, collected_at: Optional[str]) -> None:
    when = parse_collected_at(collected_at)
    if when is None:
        return
    fresh = is_fresh(when)
    await db.execute(
        """
        UPDATE sites SET
            status = CASE
                WHEN $3 AND (last_seen IS NULL OR $2 >= last_seen) THEN 'online'
                ELSE status
            END,
            last_seen = CASE WHEN last_seen IS NULL OR $2 >= last_seen THEN $2 ELSE last_seen END,
            updated_at = NOW()
        WHERE id = $1
        """,
        site["id"],
        when,
        fresh,
    )
    if not fresh:
        return
    await sync_condition(
        event_key=f"site:{site['id']}:probe_offline",
        active=False,
        site_id=site["id"],
        source_type="site",
        source_id=site["id"],
        source_name=site["name"],
        host=site.get("city") or "",
        alert_type="probe_offline",
        severity="critical",
        message="边缘采集器已恢复心跳",
        site_name=site["name"],
    )


@router.post("/heartbeat")
async def probe_heartbeat(body: ProbeHeartbeat, site: dict = Depends(_auth_probe)):
    await _touch_site(site, body.collected_at)
    await db.execute(
        "UPDATE sites SET probe_version=COALESCE($1, probe_version), probe_hostname=COALESCE($2, probe_hostname) WHERE id=$3",
        (body.probe_version or "")[:32] or None, (body.hostname or "")[:255] or None, site["id"],
    )
    return {"status": "ok", "site_id": site["id"], "site_name": site["name"]}


@router.get("/config")
async def probe_config(site: dict = Depends(_auth_probe)):
    """下发给采集器的集中配置：设备清单（含团体字）、自动发现网段、采集间隔"""
    default_community = decrypt(site.get("snmp_community")) or None
    rows = await db.fetch_all(
        """
        SELECT id, name, ip, device_type, snmp_community, snmp_port, vendor
        FROM network_devices WHERE site_id=$1 ORDER BY ip
        """,
        site["id"],
    )
    devices = []
    for row in rows:
        devices.append({
            "id": row["id"],
            "name": row["name"],
            "ip": row["ip"],
            "device_type": row["device_type"] or "other",
            "community": decrypt(row.get("snmp_community")) or default_community or "public",
            "snmp_port": int(row.get("snmp_port") or 161),
            "vendor": row.get("vendor") or "auto",
        })
    cidrs = [c for c in (site.get("scan_cidrs") or "").split(",") if c.strip()]
    return {
        "site_id": site["id"],
        "site_name": site["name"],
        "collect_mode": site.get("collect_mode") or "probe",
        "interval_seconds": int(site.get("probe_interval_seconds") or 60),
        "devices": devices,
        "scan": {
            "enabled": bool(site.get("scan_enabled")) and bool(cidrs),
            "cidrs": cidrs,
            "community": default_community or "public",
            "interval_minutes": 60,
        },
    }


@router.post("/report")
async def probe_report(body: ProbeReport, site: dict = Depends(_auth_probe)):
    await _touch_site(site, body.collected_at)
    accepted = 0
    for device in body.devices:
        if await _upsert_device(site, device, body.collected_at):
            accepted += 1
    return {"status": "ok", "site_id": site["id"], "accepted": accepted}


async def _upsert_device(site: dict, device: DeviceIn, report_time: Optional[str] = None) -> bool:
    ip = (device.ip or "").strip()
    name = (device.sys_name or device.name or ip or "未命名设备").strip()[:100] if device.discovered \
        else (device.name or ip or "未命名设备").strip()[:100]
    if not ip:
        return False
    device_type = (device.device_type or "other").lower()
    if device_type not in _DEVICE_TYPES:
        device_type = "other"
    when = parse_collected_at(device.collected_at or report_time)
    if when is None:
        return False
    fresh = is_fresh(when)
    cpu = _pct(device.cpu)
    memory = _pct(device.memory)
    vendor = (device.vendor or "").strip().lower()[:32] or None
    if device.snmp_ok:
        status = "online"
    elif device.reachable:
        status = "error"
    else:
        status = "offline"

    row = await db.fetch_one(
        "SELECT id, name, last_seen FROM network_devices WHERE site_id=$1 AND ip=$2",
        site["id"],
        ip,
    )
    if not row:
        try:
            row = await db.fetch_one(
                """
                INSERT INTO network_devices
                    (name, ip, device_type, snmp_version, status, site_id, cpu, memory, vendor, last_seen, source)
                VALUES ($1, $2, $3, 'v2c', $4, $5, $6, $7, $8, $9, $10)
                RETURNING id, name, last_seen
                """,
                name,
                ip,
                device_type,
                status,
                site["id"],
                cpu,
                memory,
                vendor,
                when,
                "discovered" if device.discovered else "probe",
            )
        except asyncpg.UniqueViolationError:
            row = await db.fetch_one(
                "SELECT id, name, last_seen FROM network_devices WHERE site_id=$1 AND ip=$2",
                site["id"],
                ip,
            )
    if not row:
        return False
    device_id = row["id"]
    display_name = row["name"] or name
    previous_seen = naive(row.get("last_seen"))
    latest = previous_seen is None or when >= previous_seen
    if latest:
        await db.execute(
            """
            UPDATE network_devices
            SET status=$1, cpu=$2, memory=$3, last_seen=$4, vendor=COALESCE($5, vendor),
                device_type=$6, updated_at=NOW()
            WHERE id=$7
            """,
            status,
            cpu,
            memory,
            when,
            vendor,
            device_type,
            device_id,
        )
    for metric_name, metric_value in (("cpu", cpu), ("memory", memory)):
        if metric_value is None:
            continue
        await db.execute(
            """
            INSERT INTO snmp_metrics (device_id, metric_type, metric_value, metric_unit, collected_at)
            VALUES ($1, $2, $3, '%', $4)
            """,
            device_id,
            metric_name,
            metric_value,
            when,
        )
    if not latest or not fresh:
        return True

    site_name = site["name"]
    await sync_condition(
        event_key=f"device:{device_id}:offline",
        active=status == "offline",
        site_id=site["id"],
        source_type="network",
        source_id=device_id,
        source_name=display_name,
        host=ip,
        alert_type="offline",
        severity="critical",
        message="网络设备不可达",
        site_name=site_name,
    )
    await sync_condition(
        event_key=f"device:{device_id}:snmp",
        active=status == "error",
        site_id=site["id"],
        source_type="network",
        source_id=device_id,
        source_name=display_name,
        host=ip,
        alert_type="snmp_unreachable",
        severity="high",
        message="设备网络可达，但 SNMP 无响应",
        site_name=site_name,
    )
    if status == "online":
        await check_resource_thresholds(
            source_type="network",
            source_id=device_id,
            source_name=display_name,
            host=ip,
            site_id=site["id"],
            site_name=site_name,
            cpu=cpu,
            memory=memory,
            disk=None,
            key_prefix=f"device:{device_id}",
        )
        await _sync_interfaces(site, device_id, display_name, ip, device.interfaces[:128], when)
    else:
        await clear_resource_alerts(
            key_prefix=f"device:{device_id}",
            source_type="network",
            source_id=device_id,
            source_name=display_name,
            host=ip,
            site_id=site["id"],
            site_name=site_name,
        )
    return True


async def _sync_interfaces(site: dict, device_id: int, device_name: str, host: str, interfaces: List[InterfaceIn], collected_at) -> None:
    for item in interfaces:
        if_name = (item.if_name or "").strip()[:128]
        if not if_name:
            continue
        oper = (item.oper_status or "").strip().lower() or None
        admin = (item.admin_status or "").strip().lower() or None
        previous = await db.fetch_one(
            "SELECT oper_status FROM interface_status WHERE device_id=$1 AND if_name=$2",
            device_id,
            if_name,
        )
        await db.execute(
            """
            INSERT INTO interface_status (device_id, if_index, if_name, admin_status, oper_status, collected_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (device_id, if_name) DO UPDATE SET
                if_index = EXCLUDED.if_index,
                admin_status = EXCLUDED.admin_status,
                oper_status = EXCLUDED.oper_status,
                collected_at = EXCLUDED.collected_at
            """,
            device_id,
            item.if_index,
            if_name,
            admin,
            oper,
            collected_at,
        )
        old = (previous or {}).get("oper_status")
        if old is None:
            desired = None
        elif oper == "down" and old == "up":
            desired = True
        elif oper == "down":
            desired = None
        elif oper == "up":
            desired = False
        else:
            desired = None
        if desired is None:
            continue
        await sync_condition(
            event_key=f"device:{device_id}:if:{if_name}",
            active=desired,
            site_id=site["id"],
            source_type="network",
            source_id=device_id,
            source_name=device_name,
            host=host,
            alert_type="interface_down",
            severity="high",
            message=f"端口 {if_name} 从 up 变为 down",
            site_name=site["name"],
        )
