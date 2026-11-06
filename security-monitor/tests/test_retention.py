"""删除带日志的服务器 / 设备；过期日志与告警清理"""
from conftest import run_sql


def test_delete_server_with_logs(client, admin_h):
    sid = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "del-with-logs", "ip": "10.5.5.5", "os_type": "linux"}).json()["id"]
    run_sql("INSERT INTO login_logs (server_id, account, login_time, os_type) VALUES ($1,'root',NOW(),'linux')",
            sid, fetch="exec")
    run_sql("INSERT INTO high_risk_operations (server_id, account, operation_type, operation_time, os_type, description) "
            "VALUES ($1,'root','useradd',NOW(),'linux','useradd test')", sid, fetch="exec")
    run_sql("INSERT INTO alert_records (server_id, severity, title, message) "
            "VALUES ($1,'high','retention-x','m')", sid, fetch="exec")

    r = client.delete(f"/api/v1/servers/{sid}", headers=admin_h)
    assert r.status_code == 200, r.text
    assert run_sql("SELECT COUNT(*) FROM login_logs WHERE server_id=$1", sid) == 0
    assert run_sql("SELECT COUNT(*) FROM high_risk_operations WHERE server_id=$1", sid) == 0
    # 告警推送记录保留，只是不再关联服务器
    assert run_sql("SELECT COUNT(*) FROM alert_records WHERE title='retention-x' AND server_id IS NULL") >= 1


def test_purge_old_logs_and_alerts(client):
    from app.core.database import db
    from app.services.health import purge_old_metrics

    sid = run_sql("INSERT INTO servers (name, ip, os_type) VALUES ('purge-srv','10.4.4.4','linux') RETURNING id")
    for age in (5, 40):
        run_sql("INSERT INTO login_logs (server_id, account, login_time, os_type, created_at) "
                "VALUES ($1,'u',NOW(),'linux', NOW() - make_interval(days => $2))", sid, age, fetch="exec")
    for age in (40, 400):
        run_sql("INSERT INTO alert_logs (source_type, source_id, source_name, level, event_type, event_description, created_at) "
                "VALUES ('server',$1,'purge-srv','low','t','purge-test', NOW() - make_interval(days => $2))",
                sid, age, fetch="exec")

    client.portal.call(purge_old_metrics)

    # 默认日志保留 30 天：40 天前的登录日志被清理，5 天前的保留
    ages = run_sql("SELECT array_agg(EXTRACT(DAY FROM NOW() - created_at)::int ORDER BY created_at) "
                   "FROM login_logs WHERE server_id=$1", sid)
    assert ages == [5]
    # 告警保留 365 天：40 天前的保留，400 天前的清理
    left = run_sql("SELECT COUNT(*) FROM alert_logs WHERE event_description='purge-test'")
    assert left == 1
