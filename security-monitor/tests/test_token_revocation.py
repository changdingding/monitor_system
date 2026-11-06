"""已签发 token 的即时失效：停用 / 删除 / 降级 / 改密 / 管理员重置"""
import pytest

from conftest import login

PWD = "revokepass1"


def _make_user(client, admin_h, name, role="operator"):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": name, "password": PWD, "nickname": name,
        "email": None, "role": role, "status": "active"})
    assert r.status_code == 200, r.text
    return r.json()["id"], login(client, name, PWD)


def _create_site(client, h):
    return client.post("/api/v1/sites", headers=h, json={"name": "rv-site", "site_type": "cloud"}).status_code


def _update(client, admin_h, uid, name, role, status):
    r = client.put(f"/api/v1/users/{uid}", headers=admin_h,
                   json={"nickname": name, "email": None, "role": role, "status": status})
    assert r.status_code == 200, r.text


def test_disabled_user_token_rejected(client, admin_h):
    uid, h = _make_user(client, admin_h, "rv_disable")
    assert _create_site(client, h) == 200
    _update(client, admin_h, uid, "rv_disable", "operator", "disabled")
    assert client.get("/api/v1/servers", headers=h).status_code == 401
    assert _create_site(client, h) == 401


def test_demoted_user_loses_write_immediately(client, admin_h):
    uid, h = _make_user(client, admin_h, "rv_demote")
    assert _create_site(client, h) == 200
    _update(client, admin_h, uid, "rv_demote", "viewer", "active")
    # token 里仍写着 operator，但以数据库角色为准
    assert _create_site(client, h) == 403
    assert client.get("/api/v1/servers", headers=h).status_code == 200
    servers = client.get("/api/v1/servers", headers=h).json()
    assert all(s["agent_token"] is None for s in servers)


def test_deleted_user_token_rejected(client, admin_h):
    uid, h = _make_user(client, admin_h, "rv_delete")
    assert client.delete(f"/api/v1/users/{uid}", headers=admin_h).status_code == 200
    assert client.get("/api/v1/servers", headers=h).status_code == 401


def test_own_password_change_revokes_other_sessions(client, admin_h):
    _, h1 = _make_user(client, admin_h, "rv_self")
    h2 = login(client, "rv_self", PWD)  # 另一台设备
    r = client.post("/api/v1/auth/change-password", headers=h1,
                    json={"old_password": PWD, "new_password": "revokepass2"})
    assert r.status_code == 200, r.text
    new_h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/v1/servers", headers=h1).status_code == 401
    assert client.get("/api/v1/servers", headers=h2).status_code == 401
    # 当前会话拿到的新 token 可继续使用
    assert client.get("/api/v1/servers", headers=new_h).status_code == 200


def test_admin_reset_revokes_target_sessions(client, admin_h):
    uid, h = _make_user(client, admin_h, "rv_reset")
    r = client.post(f"/api/v1/users/{uid}/reset-password", headers=admin_h, json={"new_password": "revokepass3"})
    assert r.status_code == 200
    assert client.get("/api/v1/servers", headers=h).status_code == 401
    # 管理员自己的会话不受影响
    assert client.get("/api/v1/servers", headers=admin_h).status_code == 200


@pytest.mark.parametrize("tv", [None, 0])
def test_legacy_token_without_tv_still_valid(client, admin_h, tv):
    """升级前签发的 token 没有 tv 字段，视为版本 0，不应在升级时把所有人踢下线"""
    import jwt
    from app.core.config import settings
    from conftest import run_sql
    name = f"rv_legacy_{tv}"
    uid, _ = _make_user(client, admin_h, name)
    assert run_sql("SELECT token_version FROM users WHERE id=$1", uid) == 0
    claims = {"user_id": uid, "username": name, "role": "operator", "exp": 9999999999}
    if tv is not None:
        claims["tv"] = tv
    tok = jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")
    assert client.get("/api/v1/servers", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_viewer_cannot_see_snmp_community(client, admin_h, viewer_h):
    r = client.post("/api/v1/network-devices", headers=admin_h, json={
        "name": "comm-dev", "ip": "10.3.3.3", "device_type": "switch", "snmp_version": "v2c", "snmp_community": "s3cr3t-comm"})
    assert r.status_code == 200, r.text
    dev = lambda h: next(d for d in client.get("/api/v1/network-devices", headers=h).json() if d["name"] == "comm-dev")
    assert dev(admin_h)["snmp_community"] == "s3cr3t-comm"
    assert dev(viewer_h)["snmp_community"] is None
