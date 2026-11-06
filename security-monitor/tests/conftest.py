"""
集成测试夹具：连接本地 PostgreSQL，每次测试会话重建测试库并加载 init.sql。

环境变量（均有默认值）：
  TEST_DB_HOST=localhost TEST_DB_PORT=5432 TEST_DB_USER=postgres
  TEST_DB_PASSWORD=testpass TEST_DB_NAME=secmon_pytest
"""
import asyncio
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = {
    "host": os.getenv("TEST_DB_HOST", "localhost"),
    "port": int(os.getenv("TEST_DB_PORT", "5432")),
    "user": os.getenv("TEST_DB_USER", "postgres"),
    "password": os.getenv("TEST_DB_PASSWORD", "testpass"),
    "database": os.getenv("TEST_DB_NAME", "secmon_pytest"),
}

# 必须在导入 app 之前设置，覆盖 .env
os.environ.update(
    DB_HOST=DB["host"], DB_PORT=str(DB["port"]), DB_USER=DB["user"],
    DB_PASSWORD=DB["password"], DB_NAME=DB["database"],
    JWT_SECRET="test-secret-" + "x" * 40, CREDENTIAL_KEY="test-cred-" + "k" * 40, CORS_ORIGINS="",
    FEISHU_WEBHOOK_URL="", FEISHU_SECRET="",
)


async def _recreate_db():
    import asyncpg
    admin = await asyncpg.connect(**{**DB, "database": "postgres"})
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB["database"]}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DB["database"]}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(**DB)
    try:
        await conn.execute((ROOT / "init.sql").read_text(encoding="utf-8"))
    finally:
        await conn.close()


def run_sql(query, *args, fetch="val"):
    """在独立连接上执行 SQL（测试断言用）"""
    import asyncpg

    async def _go():
        conn = await asyncpg.connect(**DB)
        try:
            return await getattr(conn, {"val": "fetchval", "row": "fetchrow", "all": "fetch", "exec": "execute"}[fetch])(query, *args)
        finally:
            await conn.close()
    return asyncio.run(_go())


@pytest.fixture(scope="session")
def client():
    asyncio.run(_recreate_db())
    from fastapi.testclient import TestClient
    from app.main import app
    from app.scheduler import scheduler
    # 测试中不跑定时任务
    scheduler.start = lambda: None
    scheduler.stop = lambda: None
    with TestClient(app) as c:
        yield c


def login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


ADMIN_PASSWORD = "Admin#Pass2026"


@pytest.fixture(scope="session")
def admin_h(client):
    # 默认密码登录只拿到受限 token，必须先改密
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/v1/auth/change-password", headers=h,
                    json={"old_password": "admin123", "new_password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return login(client, "admin", ADMIN_PASSWORD)


@pytest.fixture(scope="session")
def viewer_h(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "viewer1", "password": "viewerpass1", "nickname": "v",
        "email": None, "role": "viewer", "status": "active"})
    assert r.status_code == 200, r.text
    return login(client, "viewer1", "viewerpass1")


@pytest.fixture(scope="session")
def operator_h(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "op1", "password": "operpass11", "nickname": "o",
        "email": None, "role": "operator", "status": "active"})
    assert r.status_code == 200, r.text
    return login(client, "op1", "operpass11")
