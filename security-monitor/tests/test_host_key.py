"""SSH 主机密钥首次信任（TOFU）与凭据可见性测试：使用进程内 paramiko SSH 服务端"""
import socket
import threading

import paramiko
import pytest

from conftest import run_sql

SSH_USER, SSH_PASS = "tester", "sshpass123"


class _Server(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        ok = (username, password) == (SSH_USER, SSH_PASS)
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        channel.send(b"ok\n")
        channel.send_exit_status(0)
        return True


class FakeSSH:
    """监听本地端口的最小 SSH 服务；host_key 可随时替换以模拟服务器重装"""

    def __init__(self):
        self.host_key = paramiko.RSAKey.generate(2048)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.host_key)
            try:
                t.start_server(server=_Server())
            except Exception:
                t.close()

    def fingerprint_line(self):
        return f"{self.host_key.get_name()} {self.host_key.get_base64()}"


@pytest.fixture(scope="module")
def ssh():
    s = FakeSSH()
    yield s
    s.sock.close()


@pytest.fixture(scope="module")
def server_id(client, admin_h, ssh):
    r = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "hk-test", "ip": "127.0.0.1", "os_type": "linux", "port": ssh.port,
        "username": SSH_USER, "password": SSH_PASS, "collect_mode": "ssh"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _test(client, h, sid):
    r = client.post(f"/api/v1/servers/{sid}/test", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _row(client, h, sid):
    return next(s for s in client.get("/api/v1/servers", headers=h).json() if s["id"] == sid)


def test_first_connect_records_host_key(client, admin_h, ssh, server_id):
    assert run_sql("SELECT ssh_host_key FROM servers WHERE id=$1", server_id) is None
    assert _test(client, admin_h, server_id)["success"] is True
    assert run_sql("SELECT ssh_host_key FROM servers WHERE id=$1", server_id) == ssh.fingerprint_line()
    row = _row(client, admin_h, server_id)
    assert row["ssh_host_key_known"] is True and row["ssh_host_key_type"] == "ssh-rsa"
    # 列表不暴露原始公钥
    assert "ssh_host_key" not in row


def test_same_key_still_accepted(client, admin_h, server_id):
    assert _test(client, admin_h, server_id)["success"] is True


def test_changed_key_rejected_then_reset(client, admin_h, viewer_h, ssh, server_id):
    old = ssh.fingerprint_line()
    ssh.host_key = paramiko.RSAKey.generate(2048)  # 模拟中间人 / 重装
    res = _test(client, admin_h, server_id)
    assert res["success"] is False and "主机密钥" in res["message"]
    assert run_sql("SELECT ssh_host_key FROM servers WHERE id=$1", server_id) == old

    # 只读用户不能重置
    assert client.post(f"/api/v1/servers/{server_id}/reset-host-key", headers=viewer_h).status_code == 403
    r = client.post(f"/api/v1/servers/{server_id}/reset-host-key", headers=admin_h)
    assert r.status_code == 200, r.text
    assert _test(client, admin_h, server_id)["success"] is True
    assert run_sql("SELECT ssh_host_key FROM servers WHERE id=$1", server_id) == ssh.fingerprint_line()


def test_reset_unknown_server_404(client, admin_h):
    assert client.post("/api/v1/servers/999999/reset-host-key", headers=admin_h).status_code == 404


def test_changing_address_clears_host_key(client, admin_h, ssh, server_id):
    body = {"name": "hk-test", "ip": "127.0.0.1", "os_type": "linux", "port": ssh.port,
            "username": SSH_USER, "collect_mode": "ssh"}
    assert client.put(f"/api/v1/servers/{server_id}", headers=admin_h, json=body).status_code == 200
    assert run_sql("SELECT ssh_host_key FROM servers WHERE id=$1", server_id) is not None  # 未改地址：保留
    body["port"] = ssh.port + 1
    assert client.put(f"/api/v1/servers/{server_id}", headers=admin_h, json=body).status_code == 200
    assert run_sql("SELECT ssh_host_key FROM servers WHERE id=$1", server_id) is None
    body["port"] = ssh.port
    assert client.put(f"/api/v1/servers/{server_id}", headers=admin_h, json=body).status_code == 200


def test_tokens_hidden_from_viewer(client, admin_h, viewer_h, server_id):
    client.get(f"/api/v1/servers/{server_id}/agent-install", headers=admin_h)  # 确保已生成 token
    assert _row(client, admin_h, server_id)["agent_token"]
    assert _row(client, viewer_h, server_id)["agent_token"] is None

    r = client.post("/api/v1/sites", headers=admin_h, json={"name": "hk-site"})
    assert r.status_code == 200, r.text
    sites_admin = client.get("/api/v1/sites", headers=admin_h).json()
    sites_viewer = client.get("/api/v1/sites", headers=viewer_h).json()
    assert any(s["probe_token"] for s in sites_admin)
    assert all(s["probe_token"] is None for s in sites_viewer)
