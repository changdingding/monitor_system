"""
数据范围（门店权限）。

角色（admin / operator / viewer）决定「能做什么」，数据范围决定「能看哪些站点」：
- 管理员、data_scope='all' 的用户：全部站点，以及未归属站点的总部直连资源
- data_scope='sites'：只能访问 user_sites 中授权的站点及其全部下级（授权分支即包含分支下所有门店）

用法：
    scope = await site_scope(payload)          # None = 不限制；否则为可访问的站点 id 集合
    sql, args = scope_filter("s.site_id", scope, args)
"""
from typing import Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException

from app.core.database import db

_CACHE_KEY = "_site_scope"


async def descendants(site_ids: Iterable[int]) -> Set[int]:
    """站点及其全部下级的 id（递归，防环）"""
    ids = [int(i) for i in site_ids if i is not None]
    if not ids:
        return set()
    rows = await db.fetch_all(
        """
        WITH RECURSIVE tree(id, path) AS (
            SELECT id, ARRAY[id] FROM sites WHERE id = ANY($1::int[])
            UNION ALL
            SELECT s.id, t.path || s.id FROM sites s
            JOIN tree t ON s.parent_id = t.id
            WHERE NOT s.id = ANY(t.path)
        )
        SELECT DISTINCT id FROM tree
        """,
        ids,
    )
    return {row["id"] for row in rows}


async def site_scope(payload: dict) -> Optional[Set[int]]:
    """当前用户可访问的站点 id 集合；None 表示不受限。结果缓存在本次请求的 payload 中。"""
    if _CACHE_KEY in payload:
        return payload[_CACHE_KEY]
    scope: Optional[Set[int]] = None
    if payload.get("role") != "admin":
        user = await db.fetch_one("SELECT data_scope FROM users WHERE id=$1", payload.get("user_id"))
        if user and (user.get("data_scope") or "all") == "sites":
            rows = await db.fetch_all("SELECT site_id FROM user_sites WHERE user_id=$1", payload.get("user_id"))
            scope = await descendants(row["site_id"] for row in rows)
    payload[_CACHE_KEY] = scope
    return scope


def scope_filter(column: str, scope: Optional[Set[int]], args: List) -> Tuple[str, List]:
    """返回 (SQL 条件, 追加参数后的 args)。受限用户看不到未归属站点（site_id 为空）的资源。"""
    if scope is None:
        return "TRUE", args
    args = [*args, sorted(scope)]
    return f"{column} = ANY(${len(args)}::int[])", args


def alert_scope_filter(alias: str, scope: Optional[Set[int]], args: List) -> Tuple[str, List]:
    """告警的范围条件：告警自身带 site_id，或来源服务器 / 设备属于范围内站点"""
    if scope is None:
        return "TRUE", args
    args = [*args, sorted(scope)]
    n = len(args)
    a = f"{alias}." if alias else ""
    return (
        f"({a}site_id = ANY(${n}::int[])"
        f" OR ({a}source_type = 'server' AND {a}source_id IN (SELECT id FROM servers WHERE site_id = ANY(${n}::int[])))"
        f" OR ({a}source_type = 'network' AND {a}source_id IN (SELECT id FROM network_devices WHERE site_id = ANY(${n}::int[])))"
        f" OR ({a}source_type = 'site' AND {a}source_id = ANY(${n}::int[])))"
    ), args


def in_scope(scope: Optional[Set[int]], site_id: Optional[int]) -> bool:
    if scope is None:
        return True
    return site_id is not None and int(site_id) in scope


async def require_site_in_scope(payload: dict, site_id: Optional[int]) -> None:
    """写操作的目标站点校验：受限用户不能把资源放到范围外，也不能创建未归属站点的资源"""
    scope = await site_scope(payload)
    if scope is None:
        return
    if site_id is None:
        raise HTTPException(status_code=403, detail="您只能管理已授权的门店，请选择所属门店")
    if int(site_id) not in scope:
        raise HTTPException(status_code=403, detail="无权操作该门店的数据")


async def ensure_row_access(payload: dict, table: str, row_id: int, label: str) -> dict:
    """按 id 读取 servers / network_devices / sites 行并做范围校验；越权与不存在同样返回 404"""
    if table not in ("servers", "network_devices", "sites"):
        raise ValueError(table)
    row = await db.fetch_one(f"SELECT * FROM {table} WHERE id=$1", row_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    scope = await site_scope(payload)
    site_id = row["id"] if table == "sites" else row.get("site_id")
    if not in_scope(scope, site_id):
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    return dict(row)
