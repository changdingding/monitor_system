"""入参校验回归测试"""
from conftest import run_sql


def test_alert_status_validated_and_resolved_at(client, operator_h):
    alert_id = run_sql("""INSERT INTO alert_logs (source_type, source_id, source_name, level, event_type, event_description, status)
                          VALUES ('server', 1, 's', 'high', 't', 'd', 'new') RETURNING id""")
    assert client.patch(f"/api/v1/alerts/{alert_id}/status", headers=operator_h, json={"status": "bogus"}).status_code == 422
    assert client.patch(f"/api/v1/alerts/{alert_id}/status", headers=operator_h, json={}).status_code == 422
    r = client.patch(f"/api/v1/alerts/{alert_id}/status", headers=operator_h, json={"status": "acknowledged"})
    assert r.status_code == 200 and r.json()["resolved_at"] is None
    r = client.patch(f"/api/v1/alerts/{alert_id}/status", headers=operator_h, json={"status": "resolved"})
    assert r.status_code == 200 and r.json()["resolved_at"] is not None


def test_feishu_webhook_restricted(client, admin_h):
    for bad in ("http://169.254.169.254/latest/meta-data", "https://evil.example.com/open-apis/bot/v2/hook/x",
                "http://open.feishu.cn/open-apis/bot/v2/hook/abc"):
        r = client.put("/api/v1/feishu/config", headers=admin_h, json={"webhook_url": bad, "enabled": True})
        assert r.status_code == 422, bad
    ok = "https://open.feishu.cn/open-apis/bot/v2/hook/0a1b-2c3d"
    r = client.put("/api/v1/feishu/config", headers=admin_h, json={"webhook_url": ok, "enabled": False, "id": 1, "updated_at": "x"})
    assert r.status_code == 200 and r.json()["webhook_url"] == ok
    # 空地址允许（未启用时）
    assert client.put("/api/v1/feishu/config", headers=admin_h, json={"webhook_url": "", "enabled": False}).status_code == 200


def test_settings_ranges(client, admin_h):
    assert client.put("/api/v1/settings", headers=admin_h, json={"cpu_threshold": 150}).status_code == 422
    assert client.put("/api/v1/settings", headers=admin_h, json={"log_retention_days": "abc"}).status_code == 422
    r = client.put("/api/v1/settings", headers=admin_h, json={
        "log_retention_days": 30, "alert_debounce_seconds": 60, "collector_interval_seconds": 300,
        "monitor_interval_seconds": 10, "cpu_threshold": 85, "memory_threshold": 90, "disk_threshold": 80, "offline_seconds": 180})
    assert r.status_code == 200 and r.json()["cpu_threshold"] == 85
    # 只改部分阈值时，其余阈值保留
    r = client.put("/api/v1/settings", headers=admin_h, json={"memory_threshold": 70})
    assert r.json()["cpu_threshold"] == 85 and r.json()["memory_threshold"] == 70


def test_user_update_validated(client, admin_h, viewer_h):
    uid = run_sql("SELECT id FROM users WHERE username='viewer1'")
    assert client.put(f"/api/v1/users/{uid}", headers=admin_h, json={"role": "superuser"}).status_code == 422
    r = client.put(f"/api/v1/users/{uid}", headers=admin_h, json={"nickname": "vv", "role": "viewer", "status": "active"})
    assert r.status_code == 200 and r.json()["nickname"] == "vv"
    assert client.post(f"/api/v1/users/{uid}/reset-password", headers=admin_h, json={"new_password": "newpass123"}).status_code == 200
