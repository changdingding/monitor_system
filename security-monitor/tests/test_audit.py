"""操作审计：写操作自动入库、敏感字段不落库、目标名称、仅管理员可查"""
from conftest import run_sql


def _latest(action):
    return run_sql("SELECT * FROM audit_logs WHERE action=$1 ORDER BY id DESC LIMIT 1", action, fetch="row")


def test_create_update_delete_server_audited(client, admin_h):
    r = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "audit-srv", "ip": "10.9.9.9", "os_type": "linux", "port": 22,
        "username": "root", "password": "SuperSecret!42", "collect_mode": "ssh"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    row = _latest("新增服务器")
    assert row["username"] == "admin" and row["success"] is True and row["status_code"] == 200
    assert row["target_type"] == "server" and row["target_id"] == sid and row["target_name"] == "audit-srv"

    r = client.put(f"/api/v1/servers/{sid}", headers=admin_h, json={
        "name": "audit-srv2", "ip": "10.9.9.9", "os_type": "linux", "port": 22,
        "username": "root", "password": "AnotherSecret!7", "collect_mode": "ssh"})
    assert r.status_code == 200
    assert _latest("修改服务器")["target_id"] == sid

    assert client.delete(f"/api/v1/servers/{sid}", headers=admin_h).status_code == 200
    row = _latest("删除服务器")
    # 删除前解析名称，删除后仍能看到删的是谁
    assert row["target_id"] == sid and row["target_name"] == "audit-srv2"

    # 请求体中的密码不得出现在审计表任何位置
    leaked = run_sql("SELECT COUNT(*) FROM audit_logs WHERE audit_logs::text LIKE '%Secret!%'")
    assert leaked == 0


def test_login_attempts_audited(client):
    client.post("/api/v1/auth/login", json={"username": "ghost", "password": "whatever1"})
    row = _latest("登录")
    assert row["username"] == "ghost" and row["success"] is False and row["status_code"] == 401
    assert row["detail"] and "不存在" in row["detail"]
    assert "whatever1" not in str(dict(row))


def test_successful_login_records_user_id(client, operator_h):
    row = run_sql("SELECT * FROM audit_logs WHERE action='登录' AND username='op1' AND success "
                  "ORDER BY id DESC LIMIT 1", fetch="row")
    assert row is not None
    assert row["user_id"] == run_sql("SELECT id FROM users WHERE username='op1'")


def test_denied_action_audited(client, viewer_h):
    r = client.post("/api/v1/servers", headers=viewer_h, json={
        "name": "nope", "ip": "10.1.1.1", "os_type": "linux"})
    assert r.status_code == 403
    row = _latest("新增服务器")
    assert row["username"] == "viewer1" and row["success"] is False and row["status_code"] == 403


def test_reads_and_agent_traffic_not_audited(client, admin_h):
    before = run_sql("SELECT COUNT(*) FROM audit_logs")
    client.get("/api/v1/servers", headers=admin_h)
    client.post("/api/v1/agent/heartbeat", headers={"X-Agent-Token": "bogus"}, json={})
    assert run_sql("SELECT COUNT(*) FROM audit_logs") == before


def test_audit_api_admin_only_and_filters(client, admin_h, operator_h):
    assert client.get("/api/v1/audit-logs", headers=operator_h).status_code == 403
    r = client.get("/api/v1/audit-logs", headers=admin_h, params={"action": "登录", "success": "false"})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1 and all(i["action"] == "登录" and not i["success"] for i in data["items"])
    r = client.get("/api/v1/audit-logs", headers=admin_h, params={"username": "VIEWER", "size": 5})
    assert r.status_code == 200 and all("viewer" in i["username"] for i in r.json()["items"])
    actions = client.get("/api/v1/audit-logs/actions", headers=admin_h).json()
    assert "登录" in actions and "新增服务器" in actions
    assert client.get("/api/v1/audit-logs", headers=admin_h, params={"size": 1000}).status_code == 422
