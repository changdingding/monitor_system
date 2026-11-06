"""系统启用状态：启用前为「实施模式」，允许清理测试数据；启用后数据不可清除，且不可撤销。"""
from fastapi import HTTPException

from app.core.database import db


async def get_state() -> dict:
    row = await db.fetch_one("SELECT activated, activated_at, activated_by, note FROM system_state WHERE id=1")
    if not row:
        await db.execute("INSERT INTO system_state (id, activated) VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING")
        return {"activated": False, "activated_at": None, "activated_by": None, "note": None}
    return dict(row)


async def is_activated() -> bool:
    return bool((await get_state()).get("activated"))


async def forbid_after_activation(what: str = "数据") -> None:
    if await is_activated():
        raise HTTPException(status_code=403, detail=f"系统已正式启用，{what}不可清除（所有记录永久留档）")
