"""AI 日志分析：拉取服务器日志 / 粘贴日志 → 脱敏预处理 → AI 分析 → 问题与解决方案"""
import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.crypto import decrypt
from app.core.database import db
from app.core.scope import ensure_row_access, scope_filter, site_scope
from app.core.system_state import forbid_after_activation
from app.core.security import require_operator, verify_token
from app.services import ai_analyzer
from app.services.log_fetch import LOG_TYPES, LogFetchError, build_command, fetch_server_logs, log_types_for
from app.services.log_preprocess import preprocess

logger = logging.getLogger(__name__)
router = APIRouter()

# 同时进行的 AI 分析数量上限（SSH + 大模型调用都比较慢）
_CONCURRENCY = asyncio.Semaphore(3)
_TASKS: set = set()
MAX_MANUAL_CHARS = 2_000_000


class FetchRequest(BaseModel):
    server_id: int
    log_type: str = "overview"
    lines: int = Field(default=300, ge=10, le=2000)
    since_minutes: int = Field(default=1440, ge=5, le=7 * 24 * 60)
    unit: Optional[str] = Field(default=None, max_length=128)
    path: Optional[str] = Field(default=None, max_length=250)


class AnalyzeRequest(FetchRequest):
    source: Literal["ssh", "manual"] = "ssh"
    server_id: Optional[int] = None  # manual 模式可不关联服务器
    content: Optional[str] = Field(default=None, max_length=MAX_MANUAL_CHARS)
    note: Optional[str] = Field(default=None, max_length=1000)


_LIST_COLUMNS = """
    id, server_id, server_name, source, log_type, params, log_stats, status, summary, health_score,
    risk_level, provider, model, error, created_by, created_at, finished_at,
    COALESCE(jsonb_array_length(issues), 0) AS issue_count
"""


async def _load_server(server_id: int, payload: dict) -> dict:
    server = await ensure_row_access(payload, "servers", server_id, "服务器")
    server["password"] = decrypt(server.get("password"))
    return server


def _label(log_type: str) -> str:
    return LOG_TYPES.get(log_type, (None, log_type))[1]


@router.get("/log-analysis/log-types")
async def get_log_types(os_type: str = Query("linux"), payload: dict = Depends(verify_token)):
    """可拉取的日志类型（按操作系统）"""
    return log_types_for(os_type)


@router.post("/log-analysis/fetch")
async def fetch_logs(body: FetchRequest, payload: dict = Depends(require_operator)):
    """只拉取并预览日志（已脱敏），不调用 AI"""
    server = await _load_server(body.server_id, payload)
    cfg = await ai_analyzer.load_config()
    try:
        raw = await fetch_server_logs(server, body.log_type, body.lines, body.since_minutes, body.unit, body.path)
    except LogFetchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    content, stats = preprocess(raw, cfg["max_input_chars"])
    return {"content": content, "stats": stats}


async def _run_analysis(analysis_id: int, body: AnalyzeRequest, server: Optional[dict]):
    async with _CONCURRENCY:
        try:
            cfg = await ai_analyzer.load_config(with_secret=True)
            if body.source == "ssh":
                raw = await fetch_server_logs(server, body.log_type, body.lines, body.since_minutes,
                                              body.unit, body.path)
            else:
                raw = body.content or ""
            content, stats = preprocess(raw, cfg["max_input_chars"])
            await db.execute(
                "UPDATE log_analyses SET log_excerpt=$1, log_stats=$2, provider=$3, model=$4 WHERE id=$5",
                content, stats, cfg["provider"], cfg["model"], analysis_id,
            )
            context = {
                "server_name": (server or {}).get("name"),
                "os_type": (server or {}).get("os_type"),
                "log_type": body.log_type,
                "log_label": _label(body.log_type) if body.source == "ssh" else "用户粘贴的日志",
                "since_minutes": body.since_minutes if body.source == "ssh" else None,
                "note": body.note,
            }
            result = await ai_analyzer.analyze(content, stats, context, cfg)
            await db.execute(
                """
                UPDATE log_analyses SET status='success', summary=$1, health_score=$2, risk_level=$3,
                       issues=$4, recommendations=$5, usage=$6, model=COALESCE($7, model), finished_at=NOW()
                WHERE id=$8
                """,
                result["summary"], result["health_score"], result["risk_level"], result["issues"],
                result["recommendations"], result["usage"], (result["usage"] or {}).get("model"), analysis_id,
            )
        except (LogFetchError, ai_analyzer.AIError) as e:
            await _fail(analysis_id, str(e))
        except Exception as e:  # 兜底：记录失败，避免一直停在“分析中”
            logger.exception(f"日志分析 #{analysis_id} 异常")
            await _fail(analysis_id, f"分析异常：{e}")


async def _fail(analysis_id: int, message: str):
    await db.execute(
        "UPDATE log_analyses SET status='failed', error=$1, finished_at=NOW() WHERE id=$2",
        message[:2000], analysis_id,
    )


@router.post("/log-analysis")
async def create_analysis(body: AnalyzeRequest, payload: dict = Depends(require_operator)):
    """创建分析任务（后台执行），前端轮询 GET /log-analysis/{id} 获取结果"""
    cfg = await ai_analyzer.load_config()
    if not cfg["enabled"]:
        raise HTTPException(status_code=400, detail="AI 分析未启用，请管理员先在「AI 配置」中填写并启用")

    server = None
    if body.source == "ssh":
        if not body.server_id:
            raise HTTPException(status_code=400, detail="请选择服务器")
        server = await _load_server(body.server_id, payload)
        try:  # 提前校验参数，非法输入直接返回 400 而不是生成一条失败记录
            build_command(server.get("os_type"), body.log_type, body.lines, body.since_minutes, body.unit, body.path)
        except LogFetchError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        if not (body.content or "").strip():
            raise HTTPException(status_code=400, detail="请粘贴要分析的日志内容")
        if body.server_id:
            server = await _load_server(body.server_id, payload)

    params = {"lines": body.lines, "since_minutes": body.since_minutes, "unit": body.unit,
              "path": body.path, "note": body.note}
    row = await db.fetch_one(
        """
        INSERT INTO log_analyses (server_id, server_name, source, log_type, params, status, provider, model, created_by)
        VALUES ($1, $2, $3, $4, $5, 'running', $6, $7, $8)
        RETURNING id
        """,
        (server or {}).get("id"), (server or {}).get("name"), body.source,
        body.log_type if body.source == "ssh" else "manual", params,
        cfg["provider"], cfg["model"], payload.get("username"),
    )
    task = asyncio.create_task(_run_analysis(row["id"], body, server))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"id": row["id"], "status": "running", "name": (server or {}).get("name")}


@router.get("/log-analysis")
async def list_analyses(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    server_id: Optional[int] = None,
    payload: dict = Depends(verify_token),
):
    where, args = await _visible(payload)
    if server_id:
        args.append(server_id)
        where += f" AND server_id=${len(args)}"
    total = (await db.fetch_one(f"SELECT COUNT(*) AS n FROM log_analyses {where}", *args))["n"]
    n = len(args)
    rows = await db.fetch_all(
        f"SELECT {_LIST_COLUMNS} FROM log_analyses {where} ORDER BY id DESC LIMIT ${n + 1} OFFSET ${n + 2}",
        *args, size, (page - 1) * size,
    )
    for r in rows:
        r["log_label"] = "粘贴日志" if r["source"] == "manual" else _label(r["log_type"])
    return {"items": rows, "total": total, "page": page, "size": size}


async def _visible(payload: dict) -> tuple:
    """受限用户：只看范围内服务器的分析，以及自己发起的未关联服务器的分析"""
    scope = await site_scope(payload)
    if scope is None:
        return "WHERE TRUE", []
    cond, args = scope_filter("site_id", scope, [])
    args.append(payload.get("username"))
    return (f"WHERE (server_id IN (SELECT id FROM servers WHERE {cond})"
            f" OR (server_id IS NULL AND created_by = ${len(args)}))"), args


@router.get("/log-analysis/{analysis_id}")
async def get_analysis(analysis_id: int, payload: dict = Depends(verify_token)):
    where, args = await _visible(payload)
    args.append(analysis_id)
    row = await db.fetch_one(f"SELECT * FROM log_analyses {where} AND id=${len(args)}", *args)
    if not row:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    row["log_label"] = "粘贴日志" if row["source"] == "manual" else _label(row["log_type"])
    return row


@router.delete("/log-analysis/{analysis_id}")
async def delete_analysis(analysis_id: int, payload: dict = Depends(require_operator)):
    await forbid_after_activation("分析记录")
    where, args = await _visible(payload)
    args.append(analysis_id)
    row = await db.fetch_one(f"DELETE FROM log_analyses {where} AND id=${len(args)} RETURNING id", *args)
    if not row:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    return {"status": "success"}


async def recover_interrupted() -> None:
    """服务重启时，把仍处于“分析中”的记录标记为失败（后台任务已随进程结束）"""
    await db.execute(
        "UPDATE log_analyses SET status='failed', error='服务重启，分析被中断，请重新发起', finished_at=NOW() "
        "WHERE status='running'"
    )
