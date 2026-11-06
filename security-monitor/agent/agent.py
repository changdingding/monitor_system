#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全监控 Agent（安装在被监控服务器上）
- 本地采集登录/退出、高危操作
- 主动上报到管理端 API
- 管理端再推送飞书

依赖：Python 3.8+（仅标准库，Windows 需可调用 powershell）
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

AGENT_VERSION = "1.1.0"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = {
            "server_url": "http://YOUR_SERVER:8000",
            "agent_token": "PASTE_TOKEN_HERE",
            "poll_interval_seconds": 30,
            "heartbeat_interval_seconds": 60,
        }
        CONFIG_PATH.write_text(json.dumps(example, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[!] 已生成 {CONFIG_PATH}，请填写 server_url 和 agent_token 后重新运行")
        sys.exit(1)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not cfg.get("server_url") or "YOUR_SERVER" in cfg.get("server_url", ""):
        print("[!] 请先在 config.json 中配置 server_url")
        sys.exit(1)
    if not cfg.get("agent_token") or cfg.get("agent_token") == "PASTE_TOKEN_HERE":
        print("[!] 请先在 config.json 中配置 agent_token")
        sys.exit(1)
    cfg["server_url"] = cfg["server_url"].rstrip("/")
    cfg.setdefault("poll_interval_seconds", 30)
    cfg.setdefault("heartbeat_interval_seconds", 60)
    cfg.setdefault("metrics_interval_seconds", 60)
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sessions": {}, "seen_high_risk": [], "last_heartbeat": 0}


def save_state(state: dict):
    # 限制高危指纹缓存大小
    seen = state.get("seen_high_risk") or []
    if len(seen) > 5000:
        state["seen_high_risk"] = seen[-3000:]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


SPOOL_DIR = BASE_DIR / "spool"
MAX_SPOOL = 480


class AgentApiError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def api_post(cfg: dict, path: str, payload: dict) -> dict:
    url = f"{cfg['server_url']}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Token": cfg["agent_token"],
            "User-Agent": f"security-monitor-agent/{AGENT_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise AgentApiError(f"HTTP {exc.code} {detail}", exc.code >= 500 or exc.code == 429) from exc
    except urllib.error.URLError as exc:
        raise AgentApiError(str(exc.reason), True) from exc


def _spool_append(path: str, payload: dict) -> None:
    SPOOL_DIR.mkdir(exist_ok=True)
    name = f"{int(time.time() * 1000)}-{time.time_ns() % 100000}.json"
    (SPOOL_DIR / name).write_text(json.dumps({"path": path, "payload": payload}, ensure_ascii=False), encoding="utf-8")
    files = sorted(SPOOL_DIR.glob("*.json"))
    for old in files[:-MAX_SPOOL]:
        try:
            old.unlink()
        except FileNotFoundError:
            pass


def _flush_spool(cfg: dict) -> bool:
    if not SPOOL_DIR.exists():
        return True
    for item_path in sorted(SPOOL_DIR.glob("*.json")):
        try:
            item = json.loads(item_path.read_text(encoding="utf-8"))
            api_post(cfg, item["path"], item["payload"])
        except AgentApiError as exc:
            if not exc.retryable:
                try:
                    item_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            return False
        except Exception:
            return False
        try:
            item_path.unlink()
        except FileNotFoundError:
            pass
    return True


def post_or_spool(cfg: dict, path: str, payload: dict) -> dict:
    try:
        return api_post(cfg, path, payload)
    except AgentApiError as exc:
        if exc.retryable:
            _spool_append(path, payload)
            print(f"[!] {exc}，已暂存，恢复后补发")
            return {}
        raise


def run_cmd(cmd: str, shell: bool = True) -> str:
    try:
        p = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="ignore",
        )
        return p.stdout or ""
    except Exception as e:
        print(f"[collect error] {e}")
        return ""


def format_time(value) -> str:
    if value is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    m = re.search(r"/Date\((-?\d+)\)/", text)
    if m:
        ms = int(m.group(1))
        return datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    if "T" in text:
        return text.replace("T", " ").replace("Z", "").split(".")[0]
    return text


def collect_windows_session_events() -> list:
    ps = r"""
$events = Get-WinEvent -FilterHashtable @{LogName='Security'; ID=4624,4634,4647; StartTime=(Get-Date).AddMinutes(-3)} -ErrorAction SilentlyContinue
if (-not $events) { '[]' } else {
  $events | Sort-Object TimeCreated | Select-Object TimeCreated, Id,
    @{N='Account';E={ if ($_.Id -eq 4624) { $_.Properties[5].Value } else { $_.Properties[1].Value } }},
    @{N='IpAddress';E={ if ($_.Id -eq 4624) { $_.Properties[18].Value } else { '' } }},
    @{N='LogonType';E={ if ($_.Id -eq 4624) { $_.Properties[8].Value } else { 0 } }} |
    ConvertTo-Json -Compress
}
"""
    out = run_cmd(["powershell", "-NoProfile", "-Command", ps], shell=False)
    out = out.strip() or "[]"
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    events = []
    for item in data:
        eid = int(item.get("Id") or 0)
        account = (item.get("Account") or "").strip()
        if not account or account.endswith("$"):
            continue
        if eid == 4624:
            if str(item.get("LogonType") or "") not in ("2", "7", "10", "11"):
                continue
            action, action_text = "login", "账号登录"
        elif eid in (4634, 4647):
            action, action_text = "logout", "账号退出"
        else:
            continue
        events.append({
            "category": "session",
            "action": action,
            "action_text": action_text,
            "account": account,
            "event_time": format_time(item.get("TimeCreated")),
            "source_ip": item.get("IpAddress") or "",
            "os_type": "windows",
            "hostname": platform.node(),
        })
    return events


def collect_linux_session_events() -> list:
    cmd = r"""
(grep -E 'Accepted (password|publickey)|session opened for user|session closed for user|Disconnected from user' /var/log/auth.log 2>/dev/null || \
 grep -E 'Accepted (password|publickey)|session opened for user|session closed for user|Disconnected from user' /var/log/secure 2>/dev/null || \
 journalctl -u ssh --since '3 min ago' --no-pager 2>/dev/null | grep -E 'Accepted|session opened|session closed|Disconnected from user' || true) | tail -80
"""
    out = run_cmd(cmd)
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if "accepted password" in lower or "accepted publickey" in lower or "session opened for user" in lower:
            action, action_text = "login", "账号登录"
        elif "session closed for user" in lower or "disconnected from user" in lower:
            action, action_text = "logout", "账号退出"
        else:
            continue
        account = "unknown"
        m = re.search(r"\b(?:user|for)\s+(\S+)", line, re.I)
        if m:
            account = m.group(1).strip("()")
        source_ip = ""
        m_ip = re.search(r"\bfrom\s+(\d+\.\d+\.\d+\.\d+)", line, re.I)
        if m_ip:
            source_ip = m_ip.group(1)
        parts = line.split()
        event_time = " ".join(parts[:3]) if len(parts) >= 3 else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        events.append({
            "category": "session",
            "action": action,
            "action_text": action_text,
            "account": account,
            "event_time": event_time,
            "source_ip": source_ip,
            "os_type": "linux",
            "hostname": platform.node(),
        })
    return events


def collect_windows_high_risk() -> list:
    ps = r"""
$events = Get-WinEvent -FilterHashtable @{LogName='Security'; ID=4697,4698,4720,4732,1102; StartTime=(Get-Date).AddMinutes(-3)} -ErrorAction SilentlyContinue
if (-not $events) { '[]' } else {
  $events | Sort-Object TimeCreated | Select-Object TimeCreated, Id,
    @{N='Account';E={$_.Properties[1].Value}},
    @{N='Message';E={$_.Message}} |
    ConvertTo-Json -Compress
}
"""
    out = run_cmd(["powershell", "-NoProfile", "-Command", ps], shell=False).strip() or "[]"
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    mapping = {
        4697: ("service_install", "high"),
        4698: ("scheduled_task", "high"),
        4720: ("user_create", "critical"),
        4732: ("group_member_change", "high"),
        1102: ("log_clear", "critical"),
    }
    events = []
    for item in data:
        eid = int(item.get("Id") or 0)
        if eid not in mapping:
            continue
        op, level = mapping[eid]
        events.append({
            "category": "high_risk",
            "operation_type": op,
            "risk_level": level,
            "account": item.get("Account") or "",
            "event_time": format_time(item.get("TimeCreated")),
            "description": (item.get("Message") or "")[:300],
            "os_type": "windows",
            "hostname": platform.node(),
        })
    return events


def collect_linux_high_risk() -> list:
    cmd = r"""
(grep -E 'sudo:|COMMAND=|useradd|userdel|usermod|passwd:' /var/log/auth.log 2>/dev/null || \
 grep -E 'sudo:|COMMAND=|useradd|userdel|usermod|passwd:' /var/log/secure 2>/dev/null || true) | tail -40
"""
    out = run_cmd(cmd)
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if "sudo" in lower and ("command" in lower or "tty=" in lower):
            op, level = "sudo_command", "high"
        elif any(k in lower for k in ("useradd", "userdel", "usermod", "passwd:")):
            op, level = "user_change", "critical"
        else:
            continue
        events.append({
            "category": "high_risk",
            "operation_type": op,
            "risk_level": level,
            "account": "root",
            "event_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "description": line[:300],
            "os_type": "linux",
            "hostname": platform.node(),
        })
    return events


def _linux_cpu_percent(state: dict):
    try:
        parts = open("/proc/stat", "r", encoding="utf-8").readline().split()
        nums = [int(item) for item in parts[1:8]]
    except Exception:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    prev = state.get("cpu_sample") or {}
    state["cpu_sample"] = {"idle": idle, "total": total}
    if not prev or total <= prev.get("total", 0):
        return None
    delta_total = total - prev["total"]
    delta_idle = idle - prev.get("idle", 0)
    if delta_total <= 0:
        return None
    return round((1 - delta_idle / delta_total) * 100, 1)


def _linux_memory_percent():
    info = {}
    try:
        for line in open("/proc/meminfo", "r", encoding="utf-8"):
            key, raw = line.split(":", 1)
            info[key] = int(raw.strip().split()[0])
    except Exception:
        return None
    total = info.get("MemTotal") or 0
    avail = info.get("MemAvailable", info.get("MemFree"))
    if not total or avail is None:
        return None
    return round((total - avail) / total * 100, 1)


def _linux_disk_percent():
    worst = None
    paths = {"/"}
    try:
        for line in open("/proc/mounts", "r", encoding="utf-8"):
            parts = line.split()
            if len(parts) >= 3 and parts[2] in ("ext4", "xfs", "btrfs", "ntfs", "overlay"):
                paths.add(parts[1].replace("\\040", " "))
    except Exception:
        pass
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
        except Exception:
            continue
        if usage.total <= 0:
            continue
        percent = round(usage.used / usage.total * 100, 1)
        if worst is None or percent > worst:
            worst = percent
    return worst


def _windows_metrics() -> dict:
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$cpu=(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
$os=Get-CimInstance Win32_OperatingSystem
$mem=$null
if ($os -and $os.TotalVisibleMemorySize -gt 0) {
  $mem=[math]::Round((($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/$os.TotalVisibleMemorySize)*100,1)
}
$d=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$disk=$null
if ($d -and $d.Size -gt 0) {
  $disk=[math]::Round((($d.Size-$d.FreeSpace)/$d.Size)*100,1)
}
Write-Output ("{0},{1},{2}" -f $cpu,$mem,$disk)
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="ignore",
        )
        line = (result.stdout or "").strip().splitlines()[-1]
        parts = [item.strip() for item in line.split(",")]
        while len(parts) < 3:
            parts.append("")

        def num(text):
            try:
                return round(float(text), 1)
            except Exception:
                return None

        return {"cpu": num(parts[0]), "memory": num(parts[1]), "disk": num(parts[2])}
    except Exception as exc:
        print(f"[metrics] {exc}")
        return {"cpu": None, "memory": None, "disk": None}


def collect_host_metrics(state: dict, is_windows: bool) -> dict:
    if is_windows:
        return _windows_metrics()
    return {
        "cpu": _linux_cpu_percent(state),
        "memory": _linux_memory_percent(),
        "disk": _linux_disk_percent(),
    }


def filter_session_by_state(events: list, state: dict) -> list:
    """本地会话状态机：登录一次/退出一次"""
    sessions = state.setdefault("sessions", {})
    out = []
    for ev in sorted(events, key=lambda x: x.get("event_time") or ""):
        account = (ev.get("account") or "").strip()
        if not account:
            continue
        cur = sessions.get(account, "logged_out")
        action = ev.get("action")
        if action == "login":
            if cur == "logged_in":
                continue
            sessions[account] = "logged_in"
            out.append(ev)
        elif action == "logout":
            if cur != "logged_in":
                continue
            sessions[account] = "logged_out"
            out.append(ev)
    return out


def filter_high_risk(events: list, state: dict) -> list:
    seen = set(state.get("seen_high_risk") or [])
    out = []
    for ev in events:
        raw = f"{ev.get('operation_type')}|{ev.get('account')}|{ev.get('event_time')}|{(ev.get('description') or '')[:80]}"
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    state["seen_high_risk"] = list(seen)
    return out


def main():
    cfg = load_config()
    state = load_state()
    is_windows = platform.system().lower().startswith("win")
    print(f"[*] Security Monitor Agent {AGENT_VERSION} started on {platform.node()} ({platform.system()})")
    print(f"[*] Reporting to {cfg['server_url']}")

    while True:
        now = time.time()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            _flush_spool(cfg)
            if now - float(state.get("last_heartbeat") or 0) >= int(cfg["heartbeat_interval_seconds"]):
                post_or_spool(cfg, "/api/v1/agent/heartbeat", {
                    "hostname": platform.node(),
                    "os_type": "windows" if is_windows else "linux",
                    "agent_version": AGENT_VERSION,
                    "collected_at": stamp,
                })
                state["last_heartbeat"] = now
                print(f"[{datetime.now().strftime('%H:%M:%S')}] heartbeat ok")

            if now - float(state.get("last_metrics") or 0) >= int(cfg.get("metrics_interval_seconds") or 60):
                metrics = collect_host_metrics(state, is_windows)
                metrics["collected_at"] = stamp
                post_or_spool(cfg, "/api/v1/agent/metrics", metrics)
                state["last_metrics"] = now
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] metrics "
                    f"cpu={metrics.get('cpu')} mem={metrics.get('memory')} disk={metrics.get('disk')}"
                )

            if is_windows:
                session_events = collect_windows_session_events()
                risk_events = collect_windows_high_risk()
            else:
                session_events = collect_linux_session_events()
                risk_events = collect_linux_high_risk()

            session_events = filter_session_by_state(session_events, state)
            risk_events = filter_high_risk(risk_events, state)
            events = session_events + risk_events

            if events:
                resp = post_or_spool(cfg, "/api/v1/agent/events", {"events": events, "collected_at": stamp})
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] reported {len(events)} events, "
                    f"alerted={resp.get('alerted')} skipped={resp.get('skipped')}"
                )

            save_state(state)
        except AgentApiError as e:
            print(f"[!] {e}")
        except Exception as e:
            print(f"[!] loop error: {e}")

        time.sleep(int(cfg["poll_interval_seconds"]))


if __name__ == "__main__":
    main()
