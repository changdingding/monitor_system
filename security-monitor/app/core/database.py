"""
数据库连接
"""
from typing import Any, Optional
import json

import asyncpg
from sqlalchemy import ClauseElement
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# 创建异步引擎（供 SQLAlchemy Core / ORM 使用）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

# 创建会话工厂
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 创建基类
Base = declarative_base()


async def get_db() -> AsyncSession:
    """获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def _init_asyncpg_connection(conn: asyncpg.Connection):
    """注册 json/jsonb 编解码，支持直接传 list/dict"""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class Database:
    """数据库操作封装：支持 asyncpg 原生 SQL 与 SQLAlchemy Core"""

    def __init__(self, engine, async_session_local):
        self.engine = engine
        self.AsyncSessionLocal = async_session_local
        self._pool: Optional[asyncpg.Pool] = None

    async def init_db(self):
        """初始化数据库连接池与表结构"""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
                database=settings.DB_NAME,
                min_size=2,
                max_size=10,
                init=_init_asyncpg_connection,
            )

        async with self.engine.begin() as conn:
            # 导入所有模型以确保它们被注册
            from app.models import (
                alert_rules,
                servers,
                network_devices,
                login_logs,
                high_risk_operations,
                syslog_logs,
                snmp_metrics,
                alert_records,
            )
            await conn.run_sync(Base.metadata.create_all)

        await self._apply_schema()

    async def _apply_schema(self):
        """补齐门店、指标、端口和健康告警表，已有库重复启动也安全。"""
        from app.core.schema_sql import SCHEMA_STATEMENTS

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            for stmt in SCHEMA_STATEMENTS:
                await conn.execute(stmt)

    async def close(self):
        """关闭数据库连接"""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        await self.engine.dispose()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.init_db()
        assert self._pool is not None
        return self._pool

    @staticmethod
    def _record_to_dict(record: Optional[asyncpg.Record]) -> Optional[dict]:
        if record is None:
            return None
        return dict(record)

    async def fetch_one(self, query: str, *args: Any) -> Optional[dict]:
        """执行查询并返回单行（dict）"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return self._record_to_dict(row)

    async def fetch_all(self, query: str, *args: Any) -> list:
        """执行查询并返回多行（dict 列表）"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(row) for row in rows]

    async def execute(self, query: Any, *args: Any, **kwargs: Any):
        """
        执行写操作：
        - str + 位置参数：走 asyncpg（API 路由使用 $1 风格）
        - SQLAlchemy ClauseElement：走 AsyncSession（调度器 / 规则引擎）
        """
        if isinstance(query, str):
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                return await conn.execute(query, *args)
        if isinstance(query, ClauseElement):
            async with self.AsyncSessionLocal() as session:
                result = await session.execute(query, *args, **kwargs)
                await session.commit()
                return result
        async with self.AsyncSessionLocal() as session:
            result = await session.execute(query, *args, **kwargs)
            await session.commit()
            return result


# 全局数据库实例
db = Database(engine, AsyncSessionLocal)
