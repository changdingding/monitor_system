"""登录防爆破：连续失败锁定、成功清零、管理员解锁 / 重置密码解锁"""
import pytest

from app.core.config import settings
from conftest import login, run_sql

PWD = "lockpass123"


@pytest.fixture(scope="module")
def user_id(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "locktest", "password": PWD, "nickname": "l",
        "email": None, "role": "viewer", "status": "active"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _try(client, password):
    return client.post("/api/v1/auth/login", json={"username": "locktest", "password": password})


def _unlock_db(uid):
    run_sql("UPDATE users SET failed_login_count=0, locked_until=NULL WHERE id=$1", uid, fetch="exec")


def test_success_resets_counter(client, user_id):
    for _ in range(settings.LOGIN_MAX_FAILURES - 1):
        assert _try(client, "wrong-pass").status_code == 401
    assert _try(client, PWD).status_code == 200
    assert run_sql("SELECT failed_login_count FROM users WHERE id=$1", user_id) == 0
    # 计数已清零，再失败 N-1 次仍不锁
    for _ in range(settings.LOGIN_MAX_FAILURES - 1):
        assert _try(client, "wrong-pass").status_code == 401
    assert _try(client, PWD).status_code == 200


def test_lock_after_max_failures(client, user_id):
    for _ in range(settings.LOGIN_MAX_FAILURES - 1):
        assert _try(client, "wrong-pass").status_code == 401
    r = _try(client, "wrong-pass")
    assert r.status_code == 429 and "锁定" in r.json()["detail"]
    # 锁定期内正确密码也被拒绝
    r = _try(client, PWD)
    assert r.status_code == 429
    _unlock_db(user_id)


def test_admin_unlock(client, admin_h, viewer_h, user_id):
    for _ in range(settings.LOGIN_MAX_FAILURES):
        _try(client, "wrong-pass")
    assert _try(client, PWD).status_code == 429

    users = client.get("/api/v1/users", headers=admin_h).json()
    assert next(u for u in users if u["id"] == user_id)["locked_until"] is not None

    assert client.post(f"/api/v1/users/{user_id}/unlock", headers=viewer_h).status_code == 403
    assert client.post("/api/v1/users/999999/unlock", headers=admin_h).status_code == 404
    assert client.post(f"/api/v1/users/{user_id}/unlock", headers=admin_h).status_code == 200
    assert _try(client, PWD).status_code == 200
    users = client.get("/api/v1/users", headers=admin_h).json()
    assert next(u for u in users if u["id"] == user_id)["locked_until"] is None


def test_reset_password_unlocks(client, admin_h, user_id):
    for _ in range(settings.LOGIN_MAX_FAILURES):
        _try(client, "wrong-pass")
    assert _try(client, PWD).status_code == 429
    new = "lockpass456"
    r = client.post(f"/api/v1/users/{user_id}/reset-password", headers=admin_h, json={"new_password": new})
    assert r.status_code == 200, r.text
    assert _try(client, new).status_code == 200


def test_expired_lock_allows_login(client, user_id):
    run_sql("UPDATE users SET locked_until=(NOW() AT TIME ZONE 'UTC') - interval '1 minute' WHERE id=$1",
            user_id, fetch="exec")
    assert _try(client, "lockpass456").status_code == 200
    assert run_sql("SELECT locked_until FROM users WHERE id=$1", user_id) is None


def test_unknown_user_generic_error(client):
    r = client.post("/api/v1/auth/login", json={"username": "no-such-user", "password": "whatever1"})
    assert r.status_code == 401 and r.json()["detail"] == "用户名或密码错误"
