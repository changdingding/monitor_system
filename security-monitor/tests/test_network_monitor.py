"""总部网络设备巡检：SNMP 不可达 → offline，超阈值 → error 并告警"""
import pytest

from conftest import run_sql
import app.collector.network_monitor as nm
from app.scheduler import scheduler


@pytest.fixture
def device(client, admin_h):
    r = client.post("/api/v1/network-devices", headers=admin_h, json={
        "name": "core-sw", "ip": "192.0.2.10", "device_type": "switch",
        "snmp_version": "v2c", "snmp_community": "c", "snmp_port": 16161})
    assert r.status_code == 200, r.text
    dev = r.json()
    yield {"id": dev["id"], "name": "core-sw", "ip": "192.0.2.10",
           "snmp_version": "v2c", "snmp_community": "c", "snmp_port": 16161}
    client.delete(f"/api/v1/network-devices/{dev['id']}", headers=admin_h)


def _status(dev_id):
    return run_sql("SELECT status FROM network_devices WHERE id=$1", dev_id)


def test_unreachable_marks_offline(client, device, monkeypatch):
    calls = []
    monkeypatch.setattr(nm._probe, "snmp_get", lambda h, c, oid, port, *a, **k: calls.append(port))
    client.portal.call(scheduler._check_device, device, {"cpu": 90, "memory": 90})
    assert _status(device["id"]) == "offline"
    assert calls and all(p == 16161 for p in calls), "应使用设备配置的 SNMP 端口"


def test_over_threshold_marks_error_and_alerts(client, device, monkeypatch):
    monkeypatch.setattr(nm._probe, "snmp_get", lambda *a, **k: "1.3.6.1.4.1.9.1.1")
    monkeypatch.setattr(nm._probe, "read_cpu", lambda *a, **k: 95.0)
    monkeypatch.setattr(nm._probe, "read_memory", lambda *a, **k: 10.0)
    client.portal.call(scheduler._check_device, device, {"cpu": 90, "memory": 90})
    assert _status(device["id"]) == "error"
    n = run_sql("SELECT COUNT(*) FROM alert_logs WHERE source_type='network' AND source_id=$1 AND event_type='high_cpu'", device["id"])
    assert n == 1


def test_healthy_marks_online(client, device, monkeypatch):
    monkeypatch.setattr(nm._probe, "snmp_get", lambda *a, **k: "x")
    monkeypatch.setattr(nm._probe, "read_cpu", lambda *a, **k: 20.0)
    monkeypatch.setattr(nm._probe, "read_memory", lambda *a, **k: 30.0)
    client.portal.call(scheduler._check_device, device, {"cpu": 90, "memory": 90})
    assert _status(device["id"]) == "online"
