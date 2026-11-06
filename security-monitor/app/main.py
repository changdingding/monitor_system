"""
安全监控系统 - FastAPI 主应用
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.database import db
from app.scheduler import scheduler
from app.engine.rule_engine import rule_engine
from app.notifier.feishu import notifier
from app.routers.api import router as api_router, require_admin
from app.routers.agent import router as agent_router
from app.routers.probe import router as probe_router

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 曾作为默认值出现在文档 / compose 中的数据库密码，生产环境不允许使用
WEAK_DB_PASSWORDS = {"", "postgres", "postgres123", "password", "123456"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("安全监控系统启动中...")

    # 安全检查：JWT 密钥必须由环境变量提供
    if not settings.JWT_SECRET or len(settings.JWT_SECRET) < 32 \
            or settings.JWT_SECRET == "your-secret-key-change-in-production":
        raise RuntimeError(
            "JWT_SECRET 未设置或过短（至少 32 位）。请在 .env 中设置，例如："
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    
    if not settings.CREDENTIAL_KEY or len(settings.CREDENTIAL_KEY) < 32:
        raise RuntimeError(
            "CREDENTIAL_KEY 未设置或过短（至少 32 位），用于加密 SSH 密码与 SNMP 团体字。"
            "请在 .env 中设置，且设置后不要更换，否则已保存的凭据无法解密"
        )

    if settings.DB_PASSWORD in WEAK_DB_PASSWORDS or len(settings.DB_PASSWORD) < 8:
        raise RuntimeError(
            "DB_PASSWORD 未设置或使用了默认弱密码。请在 .env 中设置强密码；"
            "已有数据库需同时执行 ALTER USER postgres PASSWORD '新密码'"
        )

    # 初始化数据库，并加密历史明文凭据
    await db.init_db()
    from app.core.crypto import migrate_plaintext_credentials, migrate_access_tokens
    await migrate_plaintext_credentials(db)
    await migrate_access_tokens(db)
    from app.routers.api.log_analysis import recover_interrupted
    await recover_interrupted()
    
    # 启动调度器，并按系统设置调整采集间隔
    scheduler.start()
    await scheduler.load_intervals()
    
    logger.info("安全监控系统已启动")
    
    yield
    
    # 关闭时
    logger.info("安全监控系统关闭中...")
    scheduler.stop()
    await db.close()
    logger.info("安全监控系统已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="安全监控系统",
    description="多源异构安全监控与告警系统",
    version="1.0.0",
    lifespan=lifespan
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGIN_LIST,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 操作审计（记录 /api/v1 下的写操作）
from app.core.audit import AuditMiddleware
app.add_middleware(AuditMiddleware)

# 注册 API 路由
app.include_router(api_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(probe_router, prefix="/api/v1")


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "安全监控系统 API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """健康检查：真实探测数据库连通性，失败返回 503"""
    try:
        await db.fetch_one("SELECT 1 AS ok")
    except Exception as e:
        logger.error(f"健康检查失败：{e}")
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "unreachable"})
    return {"status": "healthy", "database": "connected"}


@app.post("/api/v1/test/feishu")
async def test_feishu(payload: dict = Depends(require_admin)):
    """测试飞书推送（仅管理员）"""
    try:
        await notifier.send_test_message()
        return {"status": "success", "message": "测试消息已发送"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
