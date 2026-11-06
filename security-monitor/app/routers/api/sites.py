"""门店 / 云端"""
import io
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.crypto import decrypt, new_access_token
from app.core.database import db
from app.core.security import can_manage, require_operator, verify_token

router = APIRouter()


class SiteCreate(BaseModel):
    name: str
    site_type: Optional[str] = "store"
    city: Optional[str] = None


def clean_site_id(value: Optional[int]) -> Optional[int]:
    """前端 0 / 空表示“不归属站点”"""
    if value in (None, 0):
        return None
    return int(value)


def _normalize_site_type(value: Optional[str]) -> str:
    site_type = (value or "store").lower()
    if site_type not in ("store", "cloud"):
        raise HTTPException(status_code=400, detail="站点类型只能是 store 或 cloud")
    return site_type


@router.get("/sites")
async def list_sites(payload: dict = Depends(verify_token)):
    rows = await db.fetch_all(
        """
        SELECT id, name, site_type, city, probe_token, status, last_seen, created_at, updated_at
        FROM sites ORDER BY created_at DESC
        """
    )
    show_token = can_manage(payload)
    return [{**dict(row), "probe_token": decrypt(row["probe_token"]) if show_token else None} for row in rows]


@router.post("/sites")
async def create_site(site: SiteCreate, payload: dict = Depends(require_operator)):
    site_type = _normalize_site_type(site.site_type)
    plain, enc, digest = new_access_token() if site_type == "store" else (None, None, None)
    row = await db.fetch_one(
        """
        INSERT INTO sites (name, site_type, city, probe_token, probe_token_hash, status)
        VALUES ($1, $2, $3, $4, $5, 'offline')
        RETURNING id, name, site_type, city, status, last_seen, created_at, updated_at
        """,
        site.name.strip(),
        site_type,
        (site.city or "").strip() or None,
        enc,
        digest,
    )
    return {**dict(row), "probe_token": plain}


@router.put("/sites/{site_id}")
async def update_site(site_id: int, site: SiteCreate, payload: dict = Depends(require_operator)):
    site_type = _normalize_site_type(site.site_type)
    current = await db.fetch_one("SELECT * FROM sites WHERE id=$1", site_id)
    if not current:
        raise HTTPException(status_code=404, detail="门店不存在")
    enc, digest = current.get("probe_token"), current.get("probe_token_hash")
    if site_type == "store" and not enc:
        _, enc, digest = new_access_token()
    if site_type == "cloud":
        enc = digest = None
    row = await db.fetch_one(
        """
        UPDATE sites
        SET name=$1, site_type=$2, city=$3, probe_token=$4, probe_token_hash=$5, updated_at=NOW()
        WHERE id=$6
        RETURNING id, name, site_type, city, probe_token, status, last_seen, created_at, updated_at
        """,
        site.name.strip(),
        site_type,
        (site.city or "").strip() or None,
        enc,
        digest,
        site_id,
    )
    return {**dict(row), "probe_token": decrypt(row["probe_token"])}


@router.delete("/sites/{site_id}")
async def delete_site(site_id: int, payload: dict = Depends(require_operator)):
    await db.execute("DELETE FROM health_alert_state WHERE site_id=$1", site_id)
    await db.execute("DELETE FROM sites WHERE id=$1", site_id)
    return {"status": "success"}


@router.post("/sites/{site_id}/reset-probe-token")
async def reset_probe_token(site_id: int, payload: dict = Depends(require_operator)):
    current = await db.fetch_one("SELECT id, site_type FROM sites WHERE id=$1", site_id)
    if not current:
        raise HTTPException(status_code=404, detail="门店不存在")
    if current.get("site_type") != "store":
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
    server_url: str = Query(..., description="门店能访问的管理端地址，如 http://10.0.0.8:8000"),
    payload: dict = Depends(require_operator),
):
    from app.services.probe_package import build_probe_package

    row = await db.fetch_one("SELECT id, name, site_type, probe_token FROM sites WHERE id=$1", site_id)
    if not row:
        raise HTTPException(status_code=404, detail="门店不存在")
    if row.get("site_type") != "store":
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
