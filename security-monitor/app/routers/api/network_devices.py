"""网络设备管理（门店 / 分支 / 总部的设备统一在这里配置，由各站点采集器自动拉取）"""
import ipaddress
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.audit import audit_note
from app.core.crypto import decrypt, encrypt
from app.core.database import db
from app.core.scope import ensure_row_access, require_site_in_scope, scope_filter, site_scope
from app.core.security import can_manage, require_operator, verify_token
from app.routers.api.sites import clean_site_id

router = APIRouter()

DEVICE_TYPES = ("switch", "router", "firewall", "ap", "other")


class NetworkDeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    ip: str = Field(..., min_length=1, max_length=255)
    device_type: str = "switch"
    snmp_version: str = "v2c"
    snmp_community: Optional[str] = Field(default=None, max_length=200)
    snmp_port: int = Field(default=161, ge=1, le=65535)
    syslog_enabled: bool = False
    syslog_port: Optional[int] = 514
    site_id: Optional[int] = None


def _device_type(value: Optional[str]) -> str:
    value = (value or "other").strip().lower()
    return value if value in DEVICE_TYPES else "other"


@router.get("/network-devices")
async def list_network_devices(site_id: Optional[int] = None, status: Optional[str] = None,
                               payload: dict = Depends(verify_token)):
    """获取网络设备列表（按用户门店权限过滤）"""
    scope = await site_scope(payload)
    cond, args = scope_filter("d.site_id", scope, [])
    if site_id:
        args.append(site_id)
        cond += f" AND d.site_id = ${len(args)}"
    if status:
        args.append(status)
        cond += f" AND d.status = ${len(args)}"
    rows = await db.fetch_all(
        f"""
        SELECT d.*, s.name AS site_name, s.site_type,
               (SELECT COUNT(*) FROM interface_status i WHERE i.device_id = d.id AND i.oper_status = 'up') AS if_up,
               (SELECT COUNT(*) FROM interface_status i WHERE i.device_id = d.id) AS if_total
        FROM network_devices d
        LEFT JOIN sites s ON s.id = d.site_id
        WHERE {cond}
        ORDER BY s.name NULLS FIRST, d.ip
        """,
        *args,
    )
    result = []
    for row in rows:
        item = dict(row)
        # 团体字相当于设备只读口令：只读用户不可见
        item["snmp_community"] = decrypt(item.get("snmp_community")) if can_manage(payload) else None
        result.append(item)
    return result


@router.get("/network-devices/{device_id}")
async def get_network_device(device_id: int, payload: dict = Depends(verify_token)):
    row = await ensure_row_access(payload, "network_devices", device_id, "设备")
    row["snmp_community"] = decrypt(row.get("snmp_community")) if can_manage(payload) else None
    row["interfaces"] = await db.fetch_all(
        "SELECT if_index, if_name, admin_status, oper_status, collected_at FROM interface_status "
        "WHERE device_id=$1 ORDER BY if_index NULLS LAST, if_name",
        device_id,
    )
    return row


@router.post("/network-devices")
async def create_network_device(device: NetworkDeviceCreate, payload: dict = Depends(require_operator)):
    """创建网络设备"""
    site_id = clean_site_id(device.site_id)
    await require_site_in_scope(payload, site_id)
    try:
        row = await db.fetch_one("""
            INSERT INTO network_devices (name, ip, device_type, snmp_version, snmp_community, snmp_port, syslog_enabled, syslog_port, status, site_id, source)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'offline', $9, 'manual')
            RETURNING *
        """, device.name.strip(), device.ip.strip(), _device_type(device.device_type), device.snmp_version,
            encrypt(device.snmp_community) if device.snmp_community else None, device.snmp_port,
            device.syslog_enabled, device.syslog_port, site_id)
    except Exception as exc:
        if "idx_network_devices_site_ip" in str(exc) or "unique" in str(exc).lower():
            raise HTTPException(status_code=400, detail="该站点已有相同 IP 的网络设备")
        raise
    item = dict(row)
    item["snmp_community"] = None
    return item


@router.put("/network-devices/{device_id}")
async def update_network_device(device_id: int, device: NetworkDeviceCreate, payload: dict = Depends(require_operator)):
    """更新网络设备。团体字留空表示保持原值"""
    current = await ensure_row_access(payload, "network_devices", device_id, "设备")
    site_id = clean_site_id(device.site_id)
    await require_site_in_scope(payload, site_id)
    community = encrypt(device.snmp_community) if device.snmp_community else current.get("snmp_community")
    try:
        row = await db.fetch_one("""
            UPDATE network_devices SET name=$1, ip=$2, device_type=$3, snmp_version=$4, snmp_community=$5, snmp_port=$6, syslog_enabled=$7, syslog_port=$8, site_id=$9, updated_at=NOW()
            WHERE id=$10 RETURNING *
        """, device.name.strip(), device.ip.strip(), _device_type(device.device_type), device.snmp_version, community,
            device.snmp_port, device.syslog_enabled, device.syslog_port, site_id, device_id)
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=400, detail="该站点已有相同 IP 的网络设备")
        raise
    item = dict(row)
    item["snmp_community"] = None
    return item


@router.delete("/network-devices/{device_id}")
async def delete_network_device(device_id: int, payload: dict = Depends(require_operator)):
    """删除网络设备"""
    await ensure_row_access(payload, "network_devices", device_id, "设备")
    await db.execute("DELETE FROM snmp_metrics WHERE device_id=$1", device_id)
    await db.execute("DELETE FROM interface_status WHERE device_id=$1", device_id)
    await db.execute(
        "DELETE FROM health_alert_state WHERE source_type='network' AND source_id=$1",
        device_id,
    )
    await db.execute("DELETE FROM network_devices WHERE id=$1", device_id)
    return {"status": "success"}


class ImportRow(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    ip: str = Field(..., max_length=64)
    device_type: Optional[str] = "switch"
    snmp_community: Optional[str] = Field(default=None, max_length=200)
    snmp_port: Optional[int] = Field(default=161, ge=1, le=65535)


class ImportRequest(BaseModel):
    site_id: int
    devices: List[ImportRow] = Field(..., min_length=1, max_length=2000)
    overwrite: bool = False  # 已存在同 IP 时是否覆盖名称 / 类型 / 团体字


@router.post("/network-devices/import")
async def import_network_devices(body: ImportRequest, request: Request, payload: dict = Depends(require_operator)):
    """批量导入某站点的网络设备（门店设备多时一次贴进来，采集器会自动拉取）"""
    site = await ensure_row_access(payload, "sites", body.site_id, "站点")
    if site.get("site_type") == "cloud":
        raise HTTPException(status_code=400, detail="云端站点不需要网络设备采集")
    created = updated = skipped = 0
    errors = []
    for index, item in enumerate(body.devices, start=1):
        ip = (item.ip or "").strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            errors.append(f"第 {index} 行 IP 无效：{ip}")
            continue
        name = (item.name or "").strip() or ip
        community = encrypt(item.snmp_community.strip()) if (item.snmp_community or "").strip() else None
        existing = await db.fetch_one("SELECT id FROM network_devices WHERE site_id=$1 AND ip=$2", body.site_id, ip)
        if existing:
            if not body.overwrite:
                skipped += 1
                continue
            await db.execute(
                """
                UPDATE network_devices SET name=$1, device_type=$2, snmp_community=COALESCE($3, snmp_community),
                       snmp_port=$4, updated_at=NOW() WHERE id=$5
                """,
                name, _device_type(item.device_type), community, item.snmp_port or 161, existing["id"],
            )
            updated += 1
            continue
        await db.execute(
            """
            INSERT INTO network_devices (name, ip, device_type, snmp_version, snmp_community, snmp_port, status, site_id, source)
            VALUES ($1, $2, $3, 'v2c', $4, $5, 'offline', $6, 'import')
            """,
            name, ip, _device_type(item.device_type), community, item.snmp_port or 161, body.site_id,
        )
        created += 1
    audit_note(request, site=site["name"], created=created, updated=updated, skipped=skipped, errors=len(errors))
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors[:50], "name": site["name"]}
