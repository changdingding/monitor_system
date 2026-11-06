"""网络设备管理"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.crypto import decrypt, encrypt
from app.core.database import db
from app.core.security import can_manage, require_operator, verify_token
from app.routers.api.sites import clean_site_id

router = APIRouter()


class NetworkDeviceCreate(BaseModel):
    name: str
    ip: str
    device_type: str
    snmp_version: str
    snmp_community: Optional[str] = None
    snmp_port: int = 161
    syslog_enabled: bool = False
    syslog_port: Optional[int] = 514
    site_id: Optional[int] = None


@router.get("/network-devices")
async def list_network_devices(payload: dict = Depends(verify_token)):
    """获取网络设备列表"""
    rows = await db.fetch_all(
        """
        SELECT d.*, s.name AS site_name
        FROM network_devices d
        LEFT JOIN sites s ON s.id = d.site_id
        ORDER BY d.created_at DESC
        """
    )
    result = []
    for row in rows:
        item = dict(row)
        # 团体字相当于设备只读口令：只读用户不可见
        item["snmp_community"] = decrypt(item.get("snmp_community")) if can_manage(payload) else None
        result.append(item)
    return result


@router.post("/network-devices")
async def create_network_device(device: NetworkDeviceCreate, payload: dict = Depends(require_operator)):
    """创建网络设备"""
    try:
        row = await db.fetch_one("""
            INSERT INTO network_devices (name, ip, device_type, snmp_version, snmp_community, snmp_port, syslog_enabled, syslog_port, status, site_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'offline', $9)
            RETURNING *
        """, device.name, device.ip, device.device_type, device.snmp_version, encrypt(device.snmp_community), device.snmp_port, device.syslog_enabled, device.syslog_port, clean_site_id(device.site_id))
    except Exception as exc:
        if "idx_network_devices_site_ip" in str(exc) or "unique" in str(exc).lower():
            raise HTTPException(status_code=400, detail="该门店已有相同 IP 的网络设备")
        raise
    return dict(row)


@router.put("/network-devices/{device_id}")
async def update_network_device(device_id: int, device: NetworkDeviceCreate, payload: dict = Depends(require_operator)):
    """更新网络设备"""
    row = await db.fetch_one("""
        UPDATE network_devices SET name=$1, ip=$2, device_type=$3, snmp_version=$4, snmp_community=$5, snmp_port=$6, syslog_enabled=$7, syslog_port=$8, site_id=$9, updated_at=NOW()
        WHERE id=$10 RETURNING *
    """, device.name, device.ip, device.device_type, device.snmp_version, encrypt(device.snmp_community), device.snmp_port, device.syslog_enabled, device.syslog_port, clean_site_id(device.site_id), device_id)
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    return dict(row)


@router.delete("/network-devices/{device_id}")
async def delete_network_device(device_id: int, payload: dict = Depends(require_operator)):
    """删除网络设备"""
    await db.execute("DELETE FROM snmp_metrics WHERE device_id=$1", device_id)
    await db.execute("DELETE FROM interface_status WHERE device_id=$1", device_id)
    await db.execute(
        "DELETE FROM health_alert_state WHERE source_type='network' AND source_id=$1",
        device_id,
    )
    await db.execute("DELETE FROM network_devices WHERE id=$1", device_id)
    return {"status": "success"}
