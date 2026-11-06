#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
门店边缘采集器。
在门店局域网内按厂商模板读 SNMP，端口走标准 IF-MIB。
总部暂时连不上时，样本先落在 spool/，恢复后按时间补报。团体字不上传。
"""
from __future__ import annotations

import json
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from snmp_client import snmp_get, snmp_walk

PROBE_VERSION = "1.1.0"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SPOOL_DIR = BASE_DIR / "spool"
MAX_SPOOL = 480

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT = "1.3.6.1.2.1.1.2.0"
CPU_IDLE = "1.3.6.1.4.1.2021.11.11.0"
CPU_HR = "1.3.6.1.2.1.25.3.3.1.2"
MEM_TOTAL = "1.3.6.1.4.1.2021.4.5.0"
MEM_AVAIL = "1.3.6.1.4.1.2021.4.6.0"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"

# 厂商企业 OID。CPU/内存优先走各自 MIB，拿不到再退回 HOST-RESOURCES / UCD。
VENDOR_OIDS = {
    "cisco": {
        "cpu": "1.3.6.1.4.1.9.9.109.1.1.1.1.8",
        "mem_used": "1.3.6.1.4.1.9.9.48.1.1.1.5",
        "mem_free": "1.3.6.1.4.1.9.9.48.1.1.1.6",
    },
    "huawei": {
        "cpu": "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5",
        "mem": "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7",
    },
    "h3c": {
        "cpu": "1.3.6.1.4.1.25506.2.6.1.1.1.1.6",
        "mem": "1.3.6.1.4.1.25506.2.6.1.1.1.1.8",
    },
    "ruijie": {
        "cpu": "1.3.6.1.4.1.4881.1.1.10.2.36.1.1.3",
        "mem": "1.3.6.1.4.1.4881.1.1.10.2.36.1.1.4",
    },
}
ENTERPRISE = (
    ("1.3.6.1.4.1.2011.", "huawei"),
    ("1.3.6.1.4.1.25506.", "h3c"),
    ("1.3.6.1.4.1.9.", "cisco"),
    ("1.3.6.1.4.1.4881.", "ruijie"),
    ("1.3.6.1.4.1.2021.", "linux"),
)
_OPER = {
    1: "up",
    2: "down",
    3: "testing",
    4: "unknown",
    5: "dormant",
    6: "notPresent",
    7: "lowerLayerDown",
}


class ProbeApiError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sample = {
            "server_url": "http://YOUR_SERVER:8000",
            "probe_token": "PASTE_TOKEN_HERE",
            "interval_seconds": 60,
            "devices": [],
        }
        CONFIG_PATH.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[!] 已生成 {CONFIG_PATH}，请填写总部地址、Token 和本店设备后重新运行")
        sys.exit(1)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not cfg.get("server_url") or "YOUR_SERVER" in str(cfg.get("server_url")):
        print("[!] 请先在 config.json 中配置 server_url")
        sys.exit(1)
    if not cfg.get("probe_token") or cfg.get("probe_token") == "PASTE_TOKEN_HERE":
        print("[!] 请先在 config.json 中配置 probe_token")
        sys.exit(1)
    cfg["server_url"] = str(cfg["server_url"]).rstrip("/")
    cfg.setdefault("interval_seconds", 60)
    cfg.setdefault("devices", [])
    return cfg


def api_post(cfg: dict, path: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['server_url']}{path}",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Probe-Token": cfg["probe_token"],
            "User-Agent": f"security-monitor-probe/{PROBE_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        retryable = exc.code >= 500 or exc.code == 429
        raise ProbeApiError(f"HTTP {exc.code} {detail}", retryable) from exc
    except urllib.error.URLError as exc:
        raise ProbeApiError(str(exc.reason), True) from exc


def spool_append(path: str, payload: dict) -> None:
    SPOOL_DIR.mkdir(exist_ok=True)
    name = f"{int(time.time() * 1000)}-{time.time_ns() % 100000}.json"
    target = SPOOL_DIR / name
    target.write_text(
        json.dumps({"path": path, "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )
    files = sorted(SPOOL_DIR.glob("*.json"))
    for old in files[:-MAX_SPOOL]:
        try:
            old.unlink()
        except FileNotFoundError:
            pass


def flush_spool(cfg: dict) -> bool:
    if not SPOOL_DIR.exists():
        return True
    sent = 0
    for item_path in sorted(SPOOL_DIR.glob("*.json")):
        try:
            item = json.loads(item_path.read_text(encoding="utf-8"))
            api_post(cfg, item["path"], item["payload"])
        except ProbeApiError as exc:
            if not exc.retryable:
                try:
                    item_path.unlink()
                except FileNotFoundError:
                    pass
                print(f"[!] 丢弃无法重试的暂存 {item_path.name}: {exc}")
                continue
            print(f"[!] 总部仍不可达，暂存 {sent} 条已补发，其余留下")
            return False
        except Exception as exc:
            print(f"[!] 补发失败：{exc}")
            return False
        try:
            item_path.unlink()
        except FileNotFoundError:
            pass
        sent += 1
    if sent:
        print(f"[*] 已补发暂存 {sent} 条")
    return True


def send_or_spool(cfg: dict, path: str, payload: dict) -> bool:
    try:
        api_post(cfg, path, payload)
        return True
    except ProbeApiError as exc:
        if exc.retryable:
            spool_append(path, payload)
            print(f"[!] {exc}，已写入 spool/")
        else:
            print(f"[!] {exc}")
        return False


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clamp(value):
    if value is None:
        return None
    return round(max(0.0, min(100.0, float(value))), 1)


def _status_name(value) -> str:
    number = _number(value)
    if number is None:
        return str(value or "")
    return _OPER.get(int(number), str(int(number)))


def _avg(rows):
    values = [_number(item) for _, item in rows]
    values = [item for item in values if item is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _sum(rows):
    values = [_number(item) for _, item in rows]
    values = [item for item in values if item is not None]
    if not values:
        return None
    return sum(values)


def detect_vendor(sys_object_id, hint: str) -> str:
    chosen = (hint or "auto").strip().lower()
    if chosen in VENDOR_OIDS or chosen == "linux":
        return chosen
    text = str(sys_object_id or "")
    for prefix, name in ENTERPRISE:
        if text.startswith(prefix):
            return name
    return "generic"


def read_cpu(host, community, port, vendor):
    if vendor == "linux":
        idle = _number(snmp_get(host, community, CPU_IDLE, port))
        if idle is not None:
            return _clamp(100 - idle)
    spec = VENDOR_OIDS.get(vendor)
    if spec and spec.get("cpu"):
        value = _avg(snmp_walk(host, community, spec["cpu"], port, limit=16))
        if value is not None:
            return _clamp(value)
    idle = _number(snmp_get(host, community, CPU_IDLE, port))
    if idle is not None:
        return _clamp(100 - idle)
    return _clamp(_avg(snmp_walk(host, community, CPU_HR, port, limit=16)))


def _ucd_memory(host, community, port):
    total = _number(snmp_get(host, community, MEM_TOTAL, port))
    avail = _number(snmp_get(host, community, MEM_AVAIL, port))
    if not total or avail is None or total <= 0:
        return None
    return _clamp((total - avail) / total * 100)


def read_memory(host, community, port, vendor):
    if vendor in ("linux", "generic"):
        usage = _ucd_memory(host, community, port)
        if usage is not None:
            return usage
    spec = VENDOR_OIDS.get(vendor) or {}
    if spec.get("mem") and vendor != "cisco":
        return _clamp(_avg(snmp_walk(host, community, spec["mem"], port, limit=16)))
    if vendor == "cisco":
        used = _sum(snmp_walk(host, community, spec["mem_used"], port, limit=8))
        free = _sum(snmp_walk(host, community, spec["mem_free"], port, limit=8))
        if used is not None and free is not None and used + free > 0:
            return _clamp(used / (used + free) * 100)
    return _ucd_memory(host, community, port)


def _index_map(rows):
    mapped = {}
    for oid, value in rows:
        mapped[oid.rsplit(".", 1)[-1]] = value
    return mapped


def read_interfaces(host, community, port):
    """IF-MIB ifTable + ifXTable.ifName。名字优先用 ifName。"""
    descr = _index_map(snmp_walk(host, community, IF_DESCR, port, limit=128))
    names = _index_map(snmp_walk(host, community, IF_NAME, port, limit=128))
    admin = _index_map(snmp_walk(host, community, IF_ADMIN, port, limit=128))
    oper = _index_map(snmp_walk(host, community, IF_OPER, port, limit=128))
    indexes = list(dict.fromkeys(list(names.keys()) + list(descr.keys())))[:128]
    interfaces = []
    for index in indexes:
        label = names.get(index) or descr.get(index)
        if isinstance(label, str):
            label = label.strip()
        if not label:
            label = f"if{index}"
        oper_name = _status_name(oper.get(index))
        if oper_name == "notPresent":
            continue
        try:
            if_index = int(index)
        except ValueError:
            if_index = None
        interfaces.append({
            "if_index": if_index,
            "if_name": str(label)[:128],
            "admin_status": _status_name(admin.get(index)),
            "oper_status": oper_name,
        })
    return interfaces


def ping(ip: str) -> bool:
    if platform.system().lower().startswith("win"):
        cmd = ["ping", "-n", "1", "-w", "1000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def tcp_open(ip: str) -> bool:
    for probe_port in (443, 80, 22, 161):
        try:
            with socket.create_connection((ip, probe_port), timeout=1):
                return True
        except Exception:
            continue
    return False


def collect_device(item: dict, collected_at: str) -> dict:
    ip = str(item.get("ip") or "").strip()
    community = str(item.get("community") or "public")
    port = int(item.get("snmp_port") or 161)
    payload = {
        "name": item.get("name") or ip,
        "ip": ip,
        "device_type": item.get("device_type") or "other",
        "reachable": False,
        "snmp_ok": False,
        "cpu": None,
        "memory": None,
        "vendor": None,
        "collected_at": collected_at,
        "interfaces": [],
    }
    if not ip:
        return payload
    descr = snmp_get(ip, community, SYS_DESCR, port)
    if descr is None:
        payload["reachable"] = ping(ip) or tcp_open(ip)
        return payload
    vendor = detect_vendor(snmp_get(ip, community, SYS_OBJECT, port), item.get("vendor") or "auto")
    payload["snmp_ok"] = True
    payload["reachable"] = True
    payload["vendor"] = vendor
    payload["cpu"] = read_cpu(ip, community, port, vendor)
    payload["memory"] = read_memory(ip, community, port, vendor)
    payload["interfaces"] = read_interfaces(ip, community, port)
    return payload


def main():
    cfg = load_config()
    print(f"[*] Store probe {PROBE_VERSION} on {platform.node()}")
    print(f"[*] Reporting to {cfg['server_url']}")
    while True:
        collected_at = now_text()
        try:
            devices = [
                collect_device(item, collected_at)
                for item in (cfg.get("devices") or [])
                if item.get("ip")
            ]
            heartbeat = {
                "hostname": platform.node(),
                "probe_version": PROBE_VERSION,
                "collected_at": collected_at,
            }
            report = {"collected_at": collected_at, "devices": devices}
            if flush_spool(cfg):
                send_or_spool(cfg, "/api/v1/probe/heartbeat", heartbeat)
                if devices:
                    if send_or_spool(cfg, "/api/v1/probe/report", report):
                        print(f"[*] reported {len(devices)} devices")
                else:
                    print("[*] heartbeat ok, no devices in config.json")
            else:
                spool_append("/api/v1/probe/heartbeat", heartbeat)
                if devices:
                    spool_append("/api/v1/probe/report", report)
                print("[*] 总部不可达，本轮已暂存")
        except Exception as exc:
            print(f"[!] {exc}")
        time.sleep(int(cfg.get("interval_seconds") or 60))


if __name__ == "__main__":
    main()
