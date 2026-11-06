"""站点：总部 / 分支（区域）/ 门店 / 云端，支持上下级（分支管理）"""
import io
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.crypto import decrypt, encrypt, new_access_token
from app.core.database import db
from app.core.scope import descendants, ensure_row_access, require_site_in_scope, scope_filter, site_scope
from app.core.security import can_manage, require_operator, verify_token

router = APIRouter()

SITE_TYPES = ("hq", "branch", "store", "cloud")
# 可以部署边缘采集器的站点类型（云端服务器直接装 Agent，不需要采集器）
PROBE_SITE_TYPES = ("hq", "branch", "store")
SITE_TYPE_LABELS = {"hq": "总部", "branch": "分支", "store": "门店", "cloud": "云端"}


class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    site_type: Optional[str] = "store"
    city: Optional[str] = Field(default=None, max_length=100)
    parent_id: Optional[int] = None
    code: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = Field(default=None, max_length=255)
    contact: Optional[str] = Field(default=None, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=50)
    # 采集器集中配置：留空表示不修改已保存的团体字
    snmp_community: Optional[str] = Field(default=None, max_length=200)
    scan_enabled: bool = False
    scan_cidrs: Optional[str] = Field(default=None, max_length=2000)
    probe_interval_seconds: int = Field(default=60, ge=30, le=3600)
    collect_mode: Optional[str] = None  # probe | central；不传时总部默认 central，其余 probe


def clean_site_id(value: Optional[int]) -> Optional[int]:
    """前端 0 / 空表示“不归属站点”"""
    if value in (None, 0):
        return None
    return int(value)


def _normalize_site_type(value: Optional[str]) -> str:
    site_type = (value or "store").lower()
    if site_type not in SITE_TYPES:
        raise HTTPException(status_code=400, detail="站点类型只能是 hq / branch / store / cloud")
    return site_type


def _collect_mode(site_type: str, value: Optional[str]) -> str:
    if site_type == "cloud":
        return "probe"
    mode = (value or ("central" if site_type == "hq" else "probe")).lower()
    if mode not in ("probe", "central"):
        raise HTTPException(status_code=400, detail="采集方式只能是 probe（站内采集器）或 central（总部平台直接轮询）")
    return mode


def _text(value: Optional[str]) -> Optional[str]:
    return (value or "").strip() or None


def normalize_cidrs(text: Optional[str]) -> Optional[str]:
    """校验自动发现网段：只允许私有地址，单个网段不大于 /22（1024 个地址），最多 8 个"""
    import ipaddress

    items = [part.strip() for part in (text or "").replace("，", ",").replace("\n", ",").split(",") if part.strip()]
    if not items:
        return None
    if len(items) > 8:
        raise HTTPException(status_code=400, detail="自动发现网段最多 8 个")
    result = []
    for item in items:
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"网段格式不正确：{item}（示例 192.168.1.0/24）")
        if net.version != 4 or not net.is_private:
            raise HTTPException(status_code=400, detail=f"只允许扫描内网 IPv4 网段：{item}")
        if net.prefixlen < 22:
            raise HTTPException(status_code=400, detail=f"网段过大：{item}，单个网段最大 /22")
        result.append(str(net))
    return ",".join(dict.fromkeys(result))


_SITE_COLUMNS = """
    s.id, s.name, s.site_type, s.parent_id, p.name AS parent_name, s.code, s.city, s.address, s.contact, s.phone,
    s.probe_token, s.status, s.last_seen, s.probe_version, s.probe_hostname,
    s.scan_enabled, s.scan_cidrs, s.probe_interval_seconds, s.collect_mode, (s.snmp_community IS NOT NULL) AS has_community,
    s.created_at, s.updated_at,
    (SELECT COUNT(*) FROM network_devices d WHERE d.site_id = s.id) AS device_count,
    (SELECT COUNT(*) FROM servers sv WHERE sv.site_id = s.id) AS server_count
"""


async def _public_site(site_id: int, show_token: bool) -> dict:
    row = await db.fetch_one(
        f"SELECT {_SITE_COLUMNS} FROM sites s LEFT JOIN sites p ON p.id = s.parent_id WHERE s.id=$1", site_id
    )
    item = dict(row)
    item["probe_token"] = decrypt(item["probe_token"]) if show_token else None
    return item


@router.get("/sites")
async def list_sites(payload: dict = Depends(verify_token)):
    scope = await site_scope(payload)
    cond, args = scope_filter("s.id", scope, [])
    rows = await db.fetch_all(
        f"""
        SELECT {_SITE_COLUMNS}
        FROM sites s LEFT JOIN sites p ON p.id = s.parent_id
        WHERE {cond}
        ORDER BY CASE s.site_type WHEN 'hq' THEN 0 WHEN 'branch' THEN 1 WHEN 'store' THEN 2 ELSE 3 END,
                 s.code NULLS LAST, s.name
        """,
        *args,
    )
    show_token = can_manage(payload)
    return [{**dict(row), "probe_token": decrypt(row["probe_token"]) if show_token else None} for row in rows]


async def _check_parent(payload: dict, site_id: Optional[int], parent_id: Optional[int]) -> Optional[int]:
    parent_id = clean_site_id(parent_id)
    if parent_id is None:
        # 受限用户不能创建 / 移动到顶级（顶级站点不在其授权范围内）
        if await site_scope(payload) is not None:
            raise HTTPException(status_code=403, detail="您只能在已授权的分支下管理门店，请选择上级站点")
        return None
    parent = await db.fetch_one("SELECT id, site_type FROM sites WHERE id=$1", parent_id)
    if not parent:
        raise HTTPException(status_code=400, detail="上级站点不存在")
    if parent["site_type"] == "cloud":
        raise HTTPException(status_code=400, detail="云端站点不能作为上级")
    await require_site_in_scope(payload, parent_id)
    if site_id is not None and parent_id in await descendants([site_id]):
        raise HTTPException(status_code=400, detail="不能把站点移动到自己或自己的下级之下")
    return parent_id


@router.post("/sites")
async def create_site(site: SiteCreate, payload: dict = Depends(require_operator)):
    site_type = _normalize_site_type(site.site_type)
    parent_id = await _check_parent(payload, None, site.parent_id)
    plain, enc, digest = new_access_token() if site_type in PROBE_SITE_TYPES else (None, None, None)
    row = await db.fetch_one(
        """
        INSERT INTO sites (name, site_type, city, probe_token, probe_token_hash, status, parent_id, code, address,
                           contact, phone, snmp_community, scan_enabled, scan_cidrs, probe_interval_seconds, collect_mode)
        VALUES ($1, $2, $3, $4, $5, 'offline', $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        RETURNING id
        """,
        site.name.strip(), site_type, _text(site.city), enc, digest, parent_id, _text(site.code),
        _text(site.address), _text(site.contact), _text(site.phone),
        encrypt(site.snmp_community) if site.snmp_community else None,
        site.scan_enabled, normalize_cidrs(site.scan_cidrs), site.probe_interval_seconds,
        _collect_mode(site_type, site.collect_mode),
    )
    created = await _public_site(row["id"], show_token=False)
    return {**created, "probe_token": plain}


@router.put("/sites/{site_id}")
async def update_site(site_id: int, site: SiteCreate, payload: dict = Depends(require_operator)):
    current = await ensure_row_access(payload, "sites", site_id, "站点")
    site_type = _normalize_site_type(site.site_type)
    # 上级未变化时不再校验（受限用户被授权的顶层站点，其上级本就不在范围内）
    parent_id = clean_site_id(site.parent_id)
    if parent_id != current.get("parent_id"):
        parent_id = await _check_parent(payload, site_id, parent_id)
    enc, digest = current.get("probe_token"), current.get("probe_token_hash")
    if site_type in PROBE_SITE_TYPES and not enc:
        _, enc, digest = new_access_token()
    if site_type not in PROBE_SITE_TYPES:
        enc = digest = None
    community = encrypt(site.snmp_community) if site.snmp_community else current.get("snmp_community")
    await db.execute(
        """
        UPDATE sites
        SET name=$1, site_type=$2, city=$3, probe_token=$4, probe_token_hash=$5, parent_id=$6, code=$7,
            address=$8, contact=$9, phone=$10, snmp_community=$11, scan_enabled=$12, scan_cidrs=$13,
            probe_interval_seconds=$14, collect_mode=$15, updated_at=NOW()
        WHERE id=$16
        """,
        site.name.strip(), site_type, _text(site.city), enc, digest, parent_id, _text(site.code),
        _text(site.address), _text(site.contact), _text(site.phone), community,
        site.scan_enabled, normalize_cidrs(site.scan_cidrs), site.probe_interval_seconds,
        _collect_mode(site_type, site.collect_mode or current.get("collect_mode")), site_id,
    )
    return await _public_site(site_id, show_token=True)


@router.delete("/sites/{site_id}/community")
async def clear_site_community(site_id: int, payload: dict = Depends(require_operator)):
    """清除站点默认团体字"""
    await ensure_row_access(payload, "sites", site_id, "站点")
    await db.execute("UPDATE sites SET snmp_community=NULL, updated_at=NOW() WHERE id=$1", site_id)
    return {"status": "success"}


@router.delete("/sites/{site_id}")
async def delete_site(site_id: int, payload: dict = Depends(require_operator)):
    await ensure_row_access(payload, "sites", site_id, "站点")
    children = await db.fetch_one("SELECT COUNT(*) AS n FROM sites WHERE parent_id=$1", site_id)
    if children and children["n"]:
        raise HTTPException(status_code=400, detail=f"该站点下还有 {children['n']} 个下级站点，请先移走或删除下级")
    await db.execute("DELETE FROM health_alert_state WHERE site_id=$1", site_id)
    await db.execute("DELETE FROM sites WHERE id=$1", site_id)
    return {"status": "success"}


@router.post("/sites/{site_id}/reset-probe-token")
async def reset_probe_token(site_id: int, payload: dict = Depends(require_operator)):
    current = await ensure_row_access(payload, "sites", site_id, "站点")
    if current.get("site_type") not in PROBE_SITE_TYPES:
        raise HTTPException(status_code=400, detail="云端站点不需要边缘采集器")
    plain, enc, digest = new_access_token()
    row = await db.fetch_one(
        """
        UPDATE sites SET probe_token=$1, probe_token_hash=$2, updated_at=NOW()
        WHERE id=$3
        RETURNING id, name
        """,
        enc,
        digest,
        site_id,
    )
    return {**dict(row), "probe_token": plain}


@router.get("/sites/{site_id}/probe-package")
async def download_probe_package(
    site_id: int,
    server_url: str = Query(..., description="站点能访问的管理端地址，如 http://10.0.0.8:8000"),
    payload: dict = Depends(require_operator),
):
    from app.services.probe_package import build_probe_package

    row = await ensure_row_access(payload, "sites", site_id, "站点")
    if row.get("site_type") not in PROBE_SITE_TYPES:
        raise HTTPException(status_code=400, detail="云端站点不需要边缘采集器")
    probe_token = decrypt(row.get("probe_token"))
    if not probe_token:
        raise HTTPException(status_code=400, detail="请先生成采集器 Token（或 Token 无法解密，请重置）")
    url = (server_url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="server_url 必须以 http:// 或 https:// 开头")
    try:
        content, filename = build_probe_package(row["name"], url, probe_token)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(io.BytesIO(content), media_type="application/zip", headers=headers)
