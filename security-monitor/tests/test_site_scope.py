"""站点树、门店数据权限、采集器集中配置、批量导入、仪表盘"""
import pytest

from conftest import login, run_sql


def _site(client, h, **body):
    r = client.post("/api/v1/sites", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def tree(client, admin_h):
    hq = _site(client, admin_h, name="总部", site_type="hq")
    east = _site(client, admin_h, name="华东分支", site_type="branch", parent_id=hq["id"])
    s1 = _site(client, admin_h, name="上海一店", site_type="store", parent_id=east["id"],
               snmp_community="site-comm", scan_enabled=True, scan_cidrs="192.168.10.0/24")
    s2 = _site(client, admin_h, name="北京一店", site_type="store", parent_id=hq["id"])
    servers = {}
    for site, name in ((s1, "sh-srv"), (s2, "bj-srv")):
        r = client.post("/api/v1/servers", headers=admin_h, json={
            "name": name, "ip": f"10.9.{site['id']}.1", "os_type": "linux", "site_id": site["id"]})
        assert r.status_code == 200, r.text
        servers[name] = r.json()["id"]
    for site, name in ((s1, "sh-sw"), (s2, "bj-sw")):
        r = client.post("/api/v1/network-devices", headers=admin_h, json={
            "name": name, "ip": f"10.8.{site['id']}.1", "device_type": "switch", "snmp_version": "v2c",
            "site_id": site["id"]})
        assert r.status_code == 200, r.text
    return {"hq": hq, "east": east, "s1": s1, "s2": s2, "servers": servers}


@pytest.fixture(scope="module")
def east_op(client, admin_h, tree):
    """只授权「华东分支」的操作员：应能看到分支及下级上海一店，看不到北京一店"""
    r = client.post("/api/v1/users", headers=admin_h, json={
        "username": "east-op", "password": "eastpass11", "nickname": "east", "role": "operator",
        "data_scope": "sites", "site_ids": [tree["east"]["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["site_ids"] == [tree["east"]["id"]]
    return login(client, "east-op", "eastpass11")


def test_hq_defaults_to_central_and_tree(client, admin_h, tree):
    sites = {s["name"]: s for s in client.get("/api/v1/sites", headers=admin_h).json()}
    assert sites["总部"]["collect_mode"] == "central"
    assert sites["上海一店"]["parent_name"] == "华东分支"
    assert sites["上海一店"]["has_community"] is True
    assert "snmp_community" not in sites["上海一店"]
    assert tree["hq"]["probe_token"], "总部也能部署采集器"


def test_cannot_create_cycle(client, admin_h, tree):
    east = tree["east"]
    r = client.put(f"/api/v1/sites/{tree['hq']['id']}", headers=admin_h,
                   json={"name": "总部", "site_type": "hq", "parent_id": east["id"]})
    assert r.status_code == 400


def test_invalid_scan_cidr_rejected(client, admin_h):
    for cidr in ("8.8.8.0/24", "10.0.0.0/16", "abc"):
        r = client.post("/api/v1/sites", headers=admin_h, json={"name": "x", "scan_cidrs": cidr})
        assert r.status_code == 400, cidr


def test_scoped_user_sees_only_branch(client, east_op, tree):
    names = {s["name"] for s in client.get("/api/v1/sites", headers=east_op).json()}
    assert names == {"华东分支", "上海一店"}
    servers = {s["name"] for s in client.get("/api/v1/servers", headers=east_op).json()}
    assert servers == {"sh-srv"}
    devices = {d["name"] for d in client.get("/api/v1/network-devices", headers=east_op).json()}
    assert devices == {"sh-sw"}
    me = client.get("/api/v1/auth/me", headers=east_op).json()
    assert me["data_scope"] == "sites" and me["scope_site_count"] == 2


def test_scoped_user_cannot_touch_other_store(client, east_op, tree):
    bj = tree["servers"]["bj-srv"]
    assert client.put(f"/api/v1/servers/{bj}", headers=east_op,
                      json={"name": "hack", "ip": "1.1.1.1", "os_type": "linux"}).status_code == 404
    assert client.delete(f"/api/v1/servers/{bj}", headers=east_op).status_code == 404
    assert client.get(f"/api/v1/dashboard/sites/{tree['s2']['id']}", headers=east_op).status_code == 404
    # 不能把资源建到范围外，也不能建未归属资源
    r = client.post("/api/v1/servers", headers=east_op, json={
        "name": "x", "ip": "10.1.1.1", "os_type": "linux", "site_id": tree["s2"]["id"]})
    assert r.status_code == 403
    r = client.post("/api/v1/servers", headers=east_op, json={"name": "x", "ip": "10.1.1.1", "os_type": "linux"})
    assert r.status_code == 403
    # 不能创建顶级站点，可以在授权分支下建门店
    assert client.post("/api/v1/sites", headers=east_op, json={"name": "顶级"}).status_code == 403
    r = client.post("/api/v1/sites", headers=east_op, json={"name": "杭州一店", "parent_id": tree["east"]["id"]})
    assert r.status_code == 200, r.text
    # 编辑自己的授权根（上级不在范围内）不改上级时允许
    r = client.put(f"/api/v1/sites/{tree['east']['id']}", headers=east_op,
                   json={"name": "华东分支", "site_type": "branch", "parent_id": tree["hq"]["id"]})
    assert r.status_code == 200, r.text


def test_scoped_alerts(client, admin_h, east_op, tree):
    run_sql("INSERT INTO alert_logs (source_type, source_id, source_name, level, event_type, event_description, site_id)"
            " VALUES ('site', $1, '北京一店', 'high', 'probe_offline', 'bj', $1)", tree["s2"]["id"], fetch="exec")
    run_sql("INSERT INTO alert_logs (source_type, source_id, source_name, level, event_type, event_description)"
            " VALUES ('server', $1, 'sh-srv', 'high', 'login', 'sh')", tree["servers"]["sh-srv"], fetch="exec")
    items = client.get("/api/v1/alerts", headers=east_op, params={"size": 200}).json()["items"]
    descs = {a["event_description"] for a in items}
    assert "sh" in descs and "bj" not in descs
    all_descs = {a["event_description"] for a in client.get("/api/v1/alerts", headers=admin_h,
                                                            params={"size": 200}).json()["items"]}
    assert {"sh", "bj"} <= all_descs


def test_dashboard_endpoints(client, admin_h, east_op):
    for h in (admin_h, east_op):
        for path in ("overview", "alert-trend", "recent-alerts", "sites", "distribution", "insights"):
            r = client.get(f"/api/v1/dashboard/{path}", headers=h)
            assert r.status_code == 200, (path, r.text)
    trend = client.get("/api/v1/dashboard/alert-trend", headers=admin_h, params={"days": 7}).json()
    assert len(trend) == 7, "没有告警的日期也要补齐"
    insights = client.get("/api/v1/dashboard/insights", headers=admin_h).json()
    assert 0 <= insights["score"] <= 100 and insights["items"]
    scoped = client.get("/api/v1/dashboard/overview", headers=east_op).json()
    assert scoped["total_servers"] == 1 and scoped["total_devices"] == 1


def test_probe_pulls_central_config(client, admin_h, tree):
    token = tree["s1"]["probe_token"]
    r = client.get("/api/v1/probe/config", headers={"X-Probe-Token": token})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["site_name"] == "上海一店"
    assert [d["name"] for d in cfg["devices"]] == ["sh-sw"]
    assert cfg["devices"][0]["community"] == "site-comm", "设备未填团体字时使用站点默认团体字"
    assert cfg["scan"]["enabled"] and cfg["scan"]["cidrs"] == ["192.168.10.0/24"]
    # 采集器上报自动发现的设备后自动建档
    r = client.post("/api/v1/probe/report", headers={"X-Probe-Token": token}, json={"devices": [
        {"name": "192.168.10.7", "ip": "192.168.10.7", "sys_name": "SH-AP-01", "discovered": True,
         "reachable": True, "snmp_ok": True}]})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    row = run_sql("SELECT name, source FROM network_devices WHERE ip='192.168.10.7'", fetch="row")
    assert row["name"] == "SH-AP-01" and row["source"] == "discovered"


def test_batch_import(client, admin_h, east_op, tree):
    body = {"site_id": tree["s1"]["id"], "devices": [
        {"name": "sw-a", "ip": "192.168.10.21", "device_type": "switch", "snmp_community": "c1"},
        {"ip": "192.168.10.22", "device_type": "router"},
        {"ip": "not-an-ip"},
    ]}
    r = client.post("/api/v1/network-devices/import", headers=east_op, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2 and len(r.json()["errors"]) == 1
    r = client.post("/api/v1/network-devices/import", headers=east_op, json=body)
    assert r.json()["skipped"] == 2
    # 范围外站点不能导入
    r = client.post("/api/v1/network-devices/import", headers=east_op,
                    json={"site_id": tree["s2"]["id"], "devices": [{"ip": "10.0.0.1"}]})
    assert r.status_code == 404
    assert run_sql("SELECT COUNT(*) FROM audit_logs WHERE action='批量导入网络设备'") >= 1


def test_update_device_keeps_community_when_blank(client, admin_h, tree):
    dev_id = run_sql("SELECT id FROM network_devices WHERE ip='192.168.10.21'")
    r = client.put(f"/api/v1/network-devices/{dev_id}", headers=admin_h, json={
        "name": "sw-a2", "ip": "192.168.10.21", "device_type": "switch", "snmp_version": "v2c",
        "site_id": tree["s1"]["id"]})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/network-devices/{dev_id}", headers=admin_h).json()["snmp_community"] == "c1"


def test_scope_change_applies_immediately(client, admin_h, east_op, tree):
    uid = run_sql("SELECT id FROM users WHERE username='east-op'")
    r = client.put(f"/api/v1/users/{uid}", headers=admin_h, json={
        "nickname": "east", "role": "operator", "status": "active", "data_scope": "all"})
    assert r.status_code == 200, r.text
    names = {s["name"] for s in client.get("/api/v1/sites", headers=east_op).json()}
    assert "北京一店" in names
    r = client.put(f"/api/v1/users/{uid}", headers=admin_h, json={
        "nickname": "east", "role": "operator", "status": "active", "data_scope": "sites", "site_ids": []})
    assert r.status_code == 400
