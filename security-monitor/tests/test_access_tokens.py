"""Agent / 采集器 Token：数据库只存密文 + 摘要，鉴权按摘要；历史明文自动迁移且旧 Token 仍可用"""
import hashlib

from conftest import run_sql


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _agent_hb(client, token):
    return client.post("/api/v1/agent/heartbeat", headers={"X-Agent-Token": token}, json={})


def _probe_hb(client, token):
    return client.post("/api/v1/probe/heartbeat", headers={"X-Probe-Token": token}, json={})


def test_agent_token_stored_encrypted_and_authenticates(client, admin_h):
    r = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "tok-srv", "ip": "10.7.7.7", "os_type": "linux", "collect_mode": "agent"})
    assert r.status_code == 200, r.text
    sid, token = r.json()["id"], r.json()["agent_token"]
    assert token

    stored, digest = run_sql("SELECT agent_token, agent_token_hash FROM servers WHERE id=$1", sid, fetch="row")
    assert stored.startswith("enc:v1:") and token not in stored
    assert digest == _sha(token)
    # 管理端仍能看到明文（用于重新打包）
    assert client.get(f"/api/v1/servers/{sid}/agent-install", headers=admin_h).json()["agent_token"] == token

    r = _agent_hb(client, token)
    assert r.status_code == 200 and r.json()["server_id"] == sid
    # 直接拿数据库里的密文 / 摘要冒充都不行
    assert _agent_hb(client, stored).status_code == 401
    assert _agent_hb(client, digest).status_code == 401


def test_reset_agent_token_invalidates_old(client, admin_h):
    sid = run_sql("SELECT id FROM servers WHERE name='tok-srv'")
    old = client.get(f"/api/v1/servers/{sid}/agent-install", headers=admin_h).json()["agent_token"]
    r = client.post(f"/api/v1/servers/{sid}/reset-agent-token", headers=admin_h)
    assert r.status_code == 200
    new = r.json()["agent_token"]
    assert new and new != old
    assert _agent_hb(client, old).status_code == 401
    assert _agent_hb(client, new).status_code == 200


def test_probe_token_stored_encrypted_and_authenticates(client, admin_h):
    r = client.post("/api/v1/sites", headers=admin_h, json={"name": "tok-site", "site_type": "store"})
    assert r.status_code == 200, r.text
    site_id, token = r.json()["id"], r.json()["probe_token"]
    stored = run_sql("SELECT probe_token FROM sites WHERE id=$1", site_id)
    assert stored.startswith("enc:v1:")
    listed = next(s for s in client.get("/api/v1/sites", headers=admin_h).json() if s["id"] == site_id)
    assert listed["probe_token"] == token and "probe_token_hash" not in listed

    assert _probe_hb(client, token).status_code == 200
    r = client.post(f"/api/v1/sites/{site_id}/reset-probe-token", headers=admin_h)
    new = r.json()["probe_token"]
    assert _probe_hb(client, token).status_code == 401
    assert _probe_hb(client, new).status_code == 200

    # 改为云端站点后 Token 作废
    r = client.put(f"/api/v1/sites/{site_id}", headers=admin_h, json={"name": "tok-site", "site_type": "cloud"})
    assert r.status_code == 200 and r.json()["probe_token"] is None
    assert _probe_hb(client, new).status_code == 401


def test_legacy_plaintext_tokens_migrated(client):
    from app.core.crypto import migrate_access_tokens
    from app.core.database import db

    legacy_agent, legacy_probe = "legacy-agent-token-abc123", "legacy-probe-token-xyz789"
    sid = run_sql("INSERT INTO servers (name, ip, os_type, agent_token) VALUES ('legacy-tok', '10.6.6.6', 'linux', $1) "
                  "RETURNING id", legacy_agent)
    site_id = run_sql("INSERT INTO sites (name, site_type, probe_token) VALUES ('legacy-tok-site', 'store', $1) "
                      "RETURNING id", legacy_probe)

    assert client.portal.call(migrate_access_tokens, db) >= 2

    stored, digest = run_sql("SELECT agent_token, agent_token_hash FROM servers WHERE id=$1", sid, fetch="row")
    assert stored.startswith("enc:v1:") and digest == _sha(legacy_agent)
    assert run_sql("SELECT probe_token FROM sites WHERE id=$1", site_id).startswith("enc:v1:")
    # 已安装的 Agent / 采集器继续用原 Token
    assert _agent_hb(client, legacy_agent).status_code == 200
    assert _probe_hb(client, legacy_probe).status_code == 200
    # 再次迁移无事可做
    assert client.portal.call(migrate_access_tokens, db) == 0

    plain = run_sql("SELECT COUNT(*) FROM servers WHERE agent_token IS NOT NULL AND agent_token NOT LIKE 'enc:v1:%'")
    assert plain == 0
