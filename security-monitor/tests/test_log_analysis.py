"""AI 日志分析：预处理脱敏、命令白名单、配置权限、分析流程（AI 与 SSH 均为模拟）"""
import time

import pytest

from conftest import run_sql
from app.services import ai_analyzer, log_fetch
from app.services.log_fetch import LogFetchError, build_command
from app.services.log_preprocess import preprocess


# ---------- 预处理 ----------

def test_preprocess_masks_secrets_and_folds_duplicates():
    raw = "\n".join(
        [f"2026-11-06T10:00:{i:02d} sshd[{100 + i}]: Failed password for root from 1.2.3.{i} port 22" for i in range(30)]
        + [
            "app: connect mysql://admin:Sup3rS3cret@db:3306 failed",
            "config password=Hunter2!! token: abcdef123456",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXNpZw",
            "key sk-abcdefghijklmnopqrstuvwxyz",
            "\x1b[31mred error\x1b[0m",
        ]
    )
    text, stats = preprocess(raw)
    assert "Sup3rS3cret" not in text and "Hunter2" not in text and "abcdef123456" not in text
    assert "eyJhbGci" not in text and "sk-abcdefghij" not in text
    assert "\x1b" not in text and "red error" in text
    # 30 条只是 IP/端口/PID/时间不同的暴力破解行折叠为 1 条
    assert "相似行共 30 条" in text
    assert stats["raw_lines"] == 35 and stats["unique_lines"] == 6 and not stats["truncated"]


def test_preprocess_truncates_keeping_latest():
    import string
    word = lambda i: "".join(string.ascii_lowercase[int(d)] for d in str(i))  # 数字转字母，避免被当作相似行折叠
    raw = "\n".join(f"event {word(i)} happened" for i in range(5000))
    text, stats = preprocess(raw, max_chars=3000)
    assert stats["truncated"] and len(text) <= 3000 and stats["unique_lines"] == 5000
    assert text.endswith(f"event {word(4999)} happened") and "event a happened" not in text


# ---------- 命令构造与白名单 ----------

@pytest.mark.parametrize("unit", ["nginx;rm -rf /", "$(id)", "a b", "", "x" * 200])
def test_service_unit_rejected(unit):
    with pytest.raises(LogFetchError):
        build_command("linux", "service", unit=unit)


@pytest.mark.parametrize("path", ["/etc/shadow", "/var/log/../../etc/shadow", "var/log/syslog",
                                  "/var/log/a;id", "/root/.ssh/id_rsa", "/home/u/x.log'"])
def test_file_path_rejected(path):
    with pytest.raises(LogFetchError):
        build_command("linux", "file", path=path)


def test_valid_commands():
    assert "journalctl -u nginx.service" in build_command("linux", "service", unit="nginx.service")
    assert "tail -n 300 -- /var/log/nginx/error.log" in build_command("linux", "file", path="/var/log/nginx/error.log")
    assert build_command("linux", "file", path="/opt/app/logs/app.log")
    assert "-n 2000" in build_command("linux", "system", lines=999999)  # 行数被限制
    assert build_command("windows", "security").startswith("powershell -NoProfile -EncodedCommand ")
    with pytest.raises(LogFetchError):
        build_command("windows", "kernel")
    with pytest.raises(LogFetchError):
        build_command("linux", "unknown")


# ---------- 结果解析 ----------

def test_extract_and_normalize():
    data = ai_analyzer.extract_json('好的，结果如下：\n```json\n{"summary":"s","health_score":150,'
                                    '"risk_level":"bogus","issues":[{"title":"t1","severity":"low"},'
                                    '{"title":"t2","severity":"CRITICAL","evidence":"one line"}],'
                                    '"recommendations":"r"}\n```')
    r = ai_analyzer.normalize_result(data)
    assert r["health_score"] == 100
    assert [i["severity"] for i in r["issues"]] == ["critical", "low"]  # 按严重程度排序
    assert r["issues"][0]["evidence"] == ["one line"]
    assert r["risk_level"] == "critical"  # 非法取值回退为最高问题等级
    assert r["recommendations"] == ["r"]
    with pytest.raises(ai_analyzer.AIError):
        ai_analyzer.extract_json("没有 JSON")


# ---------- AI 配置 ----------

def test_ai_config_admin_only_and_key_hidden(client, admin_h, operator_h, viewer_h):
    assert client.get("/api/v1/ai/config", headers=operator_h).status_code == 403
    assert client.put("/api/v1/ai/config", headers=operator_h, json={}).status_code == 403
    assert client.get("/api/v1/ai/status", headers=viewer_h).json()["enabled"] is False

    r = client.put("/api/v1/ai/config", headers=admin_h, json={
        "enabled": True, "provider": "anthropic", "api_key": "sk-ant-TopSecretKey123", "model": "claude-opus-5-5"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key_set"] is True and "api_key" not in body
    assert "TopSecretKey" not in r.text
    stored = run_sql("SELECT api_key FROM ai_config LIMIT 1")
    assert stored.startswith("enc:v1:")

    # 不传 api_key 时保留原 Key
    r = client.put("/api/v1/ai/config", headers=admin_h, json={"enabled": True, "model": "claude-opus-5-5"})
    assert r.json()["api_key_set"] is True

    # OpenAI 兼容接口必须填地址
    r = client.put("/api/v1/ai/config", headers=admin_h, json={"enabled": True, "provider": "openai", "model": "m"})
    assert r.status_code == 400

    # 审计不记录 Key
    assert run_sql("SELECT COUNT(*) FROM audit_logs WHERE audit_logs::text LIKE '%TopSecretKey%'") == 0
    assert run_sql("SELECT COUNT(*) FROM audit_logs WHERE action='修改 AI 配置'") >= 2


# ---------- 分析流程 ----------

FAKE_RESULT = {
    "summary": "发现磁盘空间不足与 SSH 暴力破解",
    "health_score": 55,
    "risk_level": "high",
    "issues": [
        {"title": "SSH 暴力破解", "severity": "high", "category": "安全",
         "evidence": ["Failed password for root"], "analysis": "a", "solution": "启用 fail2ban",
         "commands": ["lastb | head"]},
        {"title": "根分区使用率 95%", "severity": "critical", "category": "磁盘",
         "evidence": ["/dev/sda1 95%"], "analysis": "b", "solution": "清理日志", "commands": ["du -sh /var/*"]},
    ],
    "recommendations": ["配置日志轮转"],
}


@pytest.fixture
def fake_ai(monkeypatch):
    seen = {}

    async def _call(cfg, system, user):
        seen["user"] = user
        seen["cfg"] = cfg
        return dict(FAKE_RESULT), {"input_tokens": 100, "output_tokens": 50, "model": cfg["model"]}

    monkeypatch.setattr(ai_analyzer, "call_model", _call)
    return seen


def _wait(client, h, aid, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/log-analysis/{aid}", headers=h)
        assert r.status_code == 200
        if r.json()["status"] != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("分析超时")


def _enable_ai(client, admin_h):
    r = client.put("/api/v1/ai/config", headers=admin_h, json={
        "enabled": True, "provider": "anthropic", "api_key": "sk-ant-test", "model": "claude-opus-5-5"})
    assert r.status_code == 200


def test_manual_analysis_flow(client, admin_h, operator_h, viewer_h, fake_ai):
    _enable_ai(client, admin_h)
    payload = {"source": "manual", "content": "kernel: disk full\npassword=LeakMe999\n", "note": "昨晚变慢"}
    assert client.post("/api/v1/log-analysis", headers=viewer_h, json=payload).status_code == 403

    r = client.post("/api/v1/log-analysis", headers=operator_h, json=payload)
    assert r.status_code == 200, r.text
    result = _wait(client, operator_h, r.json()["id"])
    assert result["status"] == "success", result
    assert result["health_score"] == 55 and result["risk_level"] == "high"
    assert [i["severity"] for i in result["issues"]] == ["critical", "high"]
    assert result["usage"]["input_tokens"] == 100
    # 发给 AI 与落库的内容都已脱敏，并带上用户说明
    assert "LeakMe999" not in fake_ai["user"] and "LeakMe999" not in result["log_excerpt"]
    assert "昨晚变慢" in fake_ai["user"] and "<logs>" in fake_ai["user"]

    lst = client.get("/api/v1/log-analysis", headers=viewer_h).json()
    item = next(i for i in lst["items"] if i["id"] == result["id"])
    assert item["issue_count"] == 2 and "log_excerpt" not in item

    assert client.delete(f"/api/v1/log-analysis/{result['id']}", headers=viewer_h).status_code == 403
    assert client.delete(f"/api/v1/log-analysis/{result['id']}", headers=operator_h).status_code == 200
    assert client.get(f"/api/v1/log-analysis/{result['id']}", headers=viewer_h).status_code == 404


def test_ssh_analysis_flow_and_failures(client, admin_h, fake_ai, monkeypatch):
    _enable_ai(client, admin_h)
    r = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "log-srv", "ip": "10.1.1.1", "os_type": "linux", "port": 22,
        "username": "root", "password": "SshPass!123", "collect_mode": "ssh"})
    sid = r.json()["id"]

    calls = {}

    async def fake_fetch(server, log_type, lines, since, unit, path):
        calls.update(server=server["name"], pwd=server["password"], log_type=log_type, unit=unit)
        return "Nov  6 10:00:00 host nginx[1]: upstream timed out\n"

    monkeypatch.setattr("app.routers.api.log_analysis.fetch_server_logs", fake_fetch)

    # 非法服务名直接 400，不生成记录
    r = client.post("/api/v1/log-analysis", headers=admin_h, json={
        "source": "ssh", "server_id": sid, "log_type": "service", "unit": "nginx;reboot"})
    assert r.status_code == 400

    r = client.post("/api/v1/log-analysis", headers=admin_h, json={
        "source": "ssh", "server_id": sid, "log_type": "service", "unit": "nginx", "since_minutes": 60})
    assert r.status_code == 200, r.text
    result = _wait(client, admin_h, r.json()["id"])
    assert result["status"] == "success" and result["server_name"] == "log-srv"
    assert calls == {"server": "log-srv", "pwd": "SshPass!123", "log_type": "service", "unit": "nginx"}
    assert "最近 60 分钟" in fake_ai["user"] and "upstream timed out" in fake_ai["user"]

    # 拉取失败 → 记录为 failed 并带错误原因
    async def broken_fetch(*a, **k):
        raise LogFetchError("SSH 连接失败：timed out")

    monkeypatch.setattr("app.routers.api.log_analysis.fetch_server_logs", broken_fetch)
    r = client.post("/api/v1/log-analysis", headers=admin_h, json={"source": "ssh", "server_id": sid, "log_type": "system"})
    result = _wait(client, admin_h, r.json()["id"])
    assert result["status"] == "failed" and "SSH 连接失败" in result["error"]

    # 删除服务器后分析记录保留，server_id 置空
    assert client.delete(f"/api/v1/servers/{sid}", headers=admin_h).status_code == 200
    assert run_sql("SELECT COUNT(*) FROM log_analyses WHERE server_name='log-srv' AND server_id IS NULL") >= 2


def test_ai_disabled_rejected(client, admin_h):
    client.put("/api/v1/ai/config", headers=admin_h, json={"enabled": False, "model": "claude-opus-5-5"})
    r = client.post("/api/v1/log-analysis", headers=admin_h, json={"source": "manual", "content": "x"})
    assert r.status_code == 400 and "未启用" in r.json()["detail"]


def test_fetch_requires_ssh_password(client, admin_h):
    r = client.post("/api/v1/servers", headers=admin_h, json={
        "name": "agent-only", "ip": "10.1.1.2", "os_type": "linux", "collect_mode": "agent"})
    sid = r.json()["id"]
    r = client.post("/api/v1/log-analysis/fetch", headers=admin_h, json={"server_id": sid, "log_type": "system"})
    assert r.status_code == 400 and "粘贴日志" in r.json()["detail"]
    client.delete(f"/api/v1/servers/{sid}", headers=admin_h)


def test_log_types_by_os(client, viewer_h):
    linux = {t["key"] for t in client.get("/api/v1/log-analysis/log-types?os_type=linux", headers=viewer_h).json()}
    win = {t["key"] for t in client.get("/api/v1/log-analysis/log-types?os_type=windows", headers=viewer_h).json()}
    assert {"overview", "kernel", "service", "file"} <= linux and "security" not in linux
    assert {"overview", "system", "application", "security"} <= win and "kernel" not in win


def test_linux_line_time_used_for_dedup():
    from app.collector.log_collector import collector
    out = ("2026-11-06T10:15:02.123456+08:00 host sudo:   alice : TTY=pts/0 ; COMMAND=/bin/ls\n"
           "Nov  6 10:16:03 host sudo:   bob : TTY=pts/1 ; COMMAND=/bin/cat\n")
    ops = collector._parse_high_risk_ops(out, "linux")
    assert ops[0]["operation_time"] == "2026-11-06 10:15:02"
    assert ops[1]["operation_time"].endswith("-11-06 10:16:03")  # 传统格式无年份，按当前年（跨年回退一年）
    assert [o["account"] for o in ops] == ["alice", "bob"]
    # 同一行解析两次，时间不随“当前时间”变化 → 去重键稳定
    assert collector._parse_high_risk_ops(out, "linux")[0]["operation_time"] == ops[0]["operation_time"]
