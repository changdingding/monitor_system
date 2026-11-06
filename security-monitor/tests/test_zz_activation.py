"""系统启用与实施数据清理（启用不可撤销，所以本文件按文件名排在最后执行）"""
import pytest

from conftest import login, run_sql


@pytest.fixture(scope="module")
def viewer_h(client, admin_h):
    # 其他测试可能让会话级 viewer 的 token 失效，这里单独建一个
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "act-viewer", "password": "actviewer1", "nickname": "v", "role": "viewer"})
    assert r.status_code == 200, r.text
    return login(client, "act-viewer", "actviewer1")


@pytest.fixture(scope="module")
def operator_h(client, admin_h):
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "act-op", "password": "actoper11", "nickname": "o", "role": "operator"})
    assert r.status_code == 200, r.text
    return login(client, "act-op", "actoper11")


def test_state_and_preview(client, admin_h, viewer_h):
    assert client.get("/api/v1/system/state", headers=viewer_h).json()["activated"] is False
    assert client.get("/api/v1/system/purge-preview", headers=viewer_h).status_code == 403
    preview = client.get("/api/v1/system/purge-preview", headers=admin_h).json()
    assert {c["key"] for c in preview["categories"]} == {"monitoring", "assets", "sites", "integrations", "users"}


def test_purge_requires_confirm_and_admin(client, admin_h, operator_h):
    body = {"categories": ["monitoring"], "confirm": "确认清理"}
    assert client.post("/api/v1/system/purge", headers=operator_h, json=body).status_code == 403
    assert client.post("/api/v1/system/purge", headers=admin_h,
                       json={**body, "confirm": "yes"}).status_code == 400


def test_purge_monitoring_keeps_assets_and_audit(client, admin_h):
    run_sql("INSERT INTO alert_logs (source_type, source_id, source_name, level, event_type, event_description)"
            " VALUES ('server', 1, 'x', 'low', 't', 'purge-me')", fetch="exec")
    servers_before = run_sql("SELECT COUNT(*) FROM servers")
    audit_before = run_sql("SELECT COUNT(*) FROM audit_logs")
    r = client.post("/api/v1/system/purge", headers=admin_h,
                    json={"categories": ["monitoring"], "confirm": "确认清理"})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["告警记录"] >= 1
    assert run_sql("SELECT COUNT(*) FROM alert_logs") == 0
    assert run_sql("SELECT COUNT(*) FROM servers") == servers_before
    assert run_sql("SELECT COUNT(*) FROM audit_logs") > audit_before, "清理操作本身要留审计"
    row = run_sql("SELECT detail FROM audit_logs WHERE action='清理实施数据' ORDER BY id DESC LIMIT 1")
    assert "deleted" in row


def test_activate_then_purge_forbidden(client, admin_h):
    assert client.post("/api/v1/system/activate", headers=admin_h, json={"confirm": "no"}).status_code == 400
    r = client.post("/api/v1/system/activate", headers=admin_h, json={"confirm": "确认启用", "note": "上线"})
    assert r.status_code == 200, r.text
    assert r.json()["activated_by"] == "admin"
    assert client.post("/api/v1/system/activate", headers=admin_h, json={"confirm": "确认启用"}).status_code == 400
    r = client.post("/api/v1/system/purge", headers=admin_h, json={"categories": ["monitoring"], "confirm": "确认清理"})
    assert r.status_code == 403
    assert run_sql("SELECT COUNT(*) FROM audit_logs WHERE action='系统正式启用' AND success") == 1
    me = client.get("/api/v1/auth/me", headers=admin_h).json()
    assert me["system_activated"] is True


def test_log_analysis_delete_forbidden_after_activation(client, admin_h):
    aid = run_sql("INSERT INTO log_analyses (source, log_type, status) VALUES ('manual', 'manual', 'success') RETURNING id")
    assert client.delete(f"/api/v1/log-analysis/{aid}", headers=admin_h).status_code == 403
