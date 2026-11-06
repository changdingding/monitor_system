"""鉴权与权限（P0）回归测试"""
import jwt

from conftest import ADMIN_PASSWORD, login, run_sql


def test_no_token_rejected(client):
    assert client.get("/api/v1/users").status_code in (401, 422)


def test_forged_token_with_old_default_secret_rejected(client):
    fake = jwt.encode({"user_id": 1, "role": "admin", "username": "admin"},
                      "your-secret-key-change-in-production", algorithm="HS256")
    r = client.get("/api/v1/users", headers={"Authorization": f"Bearer {fake}"})
    assert r.status_code == 401


def test_legacy_sha256_hash_upgraded_to_bcrypt(client, admin_h):
    # admin_h 夹具已用 init.sql 中的 SHA-256 哈希登录并改密
    assert run_sql("SELECT password_hash FROM users WHERE username='admin'").startswith("$2")
    login(client, "admin", ADMIN_PASSWORD)
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 401


def test_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_short_password_rejected(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "short", "password": "abc", "nickname": "s", "role": "viewer", "status": "active"})
    assert r.status_code == 400


def test_invalid_role_rejected(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "badrole", "password": "goodpass1", "nickname": "s", "role": "root", "status": "active"})
    assert r.status_code == 400


def test_viewer_read_only(client, viewer_h):
    assert client.get("/api/v1/servers", headers=viewer_h).status_code == 200
    assert client.post("/api/v1/servers", headers=viewer_h,
                       json={"name": "s", "ip": "10.0.0.1", "os_type": "linux"}).status_code == 403
    assert client.get("/api/v1/users", headers=viewer_h).status_code == 403
    assert client.put("/api/v1/settings", headers=viewer_h, json={}).status_code == 403


def test_operator_can_manage_assets_not_users(client, operator_h):
    r = client.post("/api/v1/servers", headers=operator_h,
                    json={"name": "op-srv", "ip": "10.0.0.2", "os_type": "linux"})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/users", headers=operator_h).status_code == 403


def test_cannot_delete_or_demote_self(client, admin_h):
    assert client.delete("/api/v1/users/1", headers=admin_h).status_code == 400
    r = client.put("/api/v1/users/1", headers=admin_h,
                   json={"nickname": "a", "email": None, "role": "viewer", "status": "active"})
    assert r.status_code == 400


def test_reset_password_missing_field_rejected(client, admin_h):
    assert client.post("/api/v1/users/2/reset-password", headers=admin_h, json={}).status_code == 422


def test_removed_anonymous_test_endpoints(client):
    assert client.post("/api/v1/test/log-collection?server_id=1").status_code == 404
    assert client.post("/api/v1/test/feishu").status_code in (401, 422)


def test_health_checks_database(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database"] == "connected"


def _raw_login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_reset_by_admin_forces_change(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "resetme", "password": "initpass11", "nickname": "r", "role": "operator", "status": "active"})
    uid = r.json()["id"]
    assert _raw_login(client, "resetme", "initpass11")["must_change_password"] is False
    assert client.post(f"/api/v1/users/{uid}/reset-password", headers=admin_h,
                       json={"new_password": "temppass11"}).status_code == 200
    data = _raw_login(client, "resetme", "temppass11")
    assert data["must_change_password"] is True
    h = {"Authorization": f"Bearer {data['token']}"}
    # 受限 token 不能访问其它接口
    assert client.get("/api/v1/servers", headers=h).status_code == 403
    # 改密校验
    bad = [({"old_password": "wrong", "new_password": "newpass111"}, 400),
           ({"old_password": "temppass11", "new_password": "temppass11"}, 400),
           ({"old_password": "temppass11", "new_password": "admin123"}, 400),
           ({"old_password": "temppass11", "new_password": "short"}, 400)]
    for body, code in bad:
        assert client.post("/api/v1/auth/change-password", headers=h, json=body).status_code == code, body
    assert client.post("/api/v1/auth/change-password", headers=h,
                       json={"old_password": "temppass11", "new_password": "newpass111"}).status_code == 200
    data = _raw_login(client, "resetme", "newpass111")
    assert data["must_change_password"] is False
    assert client.get("/api/v1/servers", headers={"Authorization": f"Bearer {data['token']}"}).status_code == 200


def test_admin_resetting_own_password_not_forced(client, admin_h):
    assert client.post("/api/v1/users/1/reset-password", headers=admin_h,
                       json={"new_password": ADMIN_PASSWORD}).status_code == 200
    assert _raw_login(client, "admin", ADMIN_PASSWORD)["must_change_password"] is False
