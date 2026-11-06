"""凭据加密存储"""
from conftest import run_sql
from app.core import crypto


def test_roundtrip_and_legacy_passthrough():
    enc = crypto.encrypt("S3cret!")
    assert enc.startswith("enc:v1:") and "S3cret" not in enc
    assert crypto.decrypt(enc) == "S3cret!"
    assert crypto.encrypt(enc) == enc            # 不会二次加密
    assert crypto.decrypt("plain") == "plain"    # 历史明文原样返回
    assert crypto.encrypt("") == "" and crypto.decrypt(None) is None


def test_wrong_key_returns_none(monkeypatch):
    enc = crypto.encrypt("x")
    monkeypatch.setattr(crypto.settings, "CREDENTIAL_KEY", "another-key-" + "z" * 40)
    assert crypto.decrypt(enc) is None


def test_server_password_encrypted_at_rest(client, admin_h):
    r = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "enc-srv", "ip": "10.9.9.9", "os_type": "linux", "username": "root",
        "password": "p" * 200, "collect_mode": "ssh"})
    assert r.status_code == 200, r.text
    stored = run_sql("SELECT password FROM servers WHERE name='enc-srv'")
    assert stored.startswith("enc:v1:") and crypto.decrypt(stored) == "p" * 200
    # 列表接口不返回密码
    assert all("password" not in s for s in client.get("/api/v1/servers", headers=admin_h).json())


def test_snmp_community_encrypted_but_listed_plain(client, admin_h):
    r = client.post("/api/v1/network-devices", headers=admin_h, json={
        "name": "enc-sw", "ip": "10.9.9.10", "device_type": "switch",
        "snmp_version": "v2c", "snmp_community": "myCommunity", "snmp_port": 161})
    assert r.status_code == 200, r.text
    assert run_sql("SELECT snmp_community FROM network_devices WHERE name='enc-sw'").startswith("enc:v1:")
    listed = [d for d in client.get("/api/v1/network-devices", headers=admin_h).json() if d["name"] == "enc-sw"]
    assert listed[0]["snmp_community"] == "myCommunity"


def test_startup_migrates_plaintext(client):
    run_sql("INSERT INTO servers (name, ip, os_type, port, username, password) VALUES ('legacy', '10.8.8.8', 'linux', 22, 'root', 'oldplain')", fetch="exec")
    client.portal.call(crypto.migrate_plaintext_credentials, __import__("app.core.database", fromlist=["db"]).db)
    stored = run_sql("SELECT password FROM servers WHERE name='legacy'")
    assert stored.startswith("enc:v1:") and crypto.decrypt(stored) == "oldplain"
