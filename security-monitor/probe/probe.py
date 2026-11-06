#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
边缘采集器（门店 / 分支 / 总部机房通用）。

- 设备清单、团体字、自动发现网段在总部网页上集中配置，采集器每轮从 /api/v1/probe/config 拉取；
  本地 config.json 只需要 server_url 和 probe_token（安装向导里可修改）
- 本地 config.json 的 devices 仍然有效（同 IP 时以本地为准），方便临时加设备
- 按厂商模板读 SNMP，端口走标准 IF-MIB；多台设备并发采集
- 开启自动发现后，按网段扫描 SNMP 设备，发现的新设备自动上报建档
- 总部暂时连不上时，样本先落在 spool/，恢复后按时间补报；集中配置缓存在 remote_config.json

命令行：
  python probe.py          运行采集
  python probe.py --test   测试与总部的连接并打印下发的配置，然后退出
"""
from __future__ import annotations

import json
import platform
import socket
import subprocess
import sys
import time
import ipaddress
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from snmp_client import snmp_get, snmp_walk

PROBE_VERSION = "1.2.0"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
REMOTE_CACHE = BASE_DIR / "remote_config.json"
SPOOL_DIR = BASE_DIR / "spool"
MAX_SPOOL = 480
MAX_WORKERS = 16  # 并发采集的设备数
SCAN_WORKERS = 64
SCAN_MAX_HOSTS = 4096
SYS_NAME = "1.3.6.1.2.1.1.5.0"

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


def api_get(cfg: dict, path: str) -> dict:
    return api_post(cfg, path, None, method="GET")


def api_post(cfg: dict, path: str, payload, method: str = "POST") -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['server_url']}{path}",
        data=data,
        method=method,
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


def fetch_remote_config(cfg: dict) -> dict:
    """拉取总部下发的配置；失败时使用上次缓存，保证断网时也能继续采集"""
    try:
        remote = api_get(cfg, "/api/v1/probe/config")
        try:
            REMOTE_CACHE.write_text(json.dumps(remote, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return remote
    except ProbeApiError as exc:
        if not exc.retryable:
            print(f"[!] 拉取集中配置被拒绝：{exc}")
        if REMOTE_CACHE.exists():
            try:
                return json.loads(REMOTE_CACHE.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def merge_devices(remote: dict, cfg: dict) -> list:
    """总部下发 + 本地 config.json，同 IP 以本地为准"""
    merged = {}
    for item in remote.get("devices") or []:
        if item.get("ip"):
            merged[str(item["ip"]).strip()] = dict(item)
    for item in cfg.get("devices") or []:
        if item.get("ip"):
            ip = str(item["ip"]).strip()
            merged[ip] = {**merged.get(ip, {}), **item}
    return list(merged.values())


def scan_hosts(cidrs) -> list:
    hosts = []
    for cidr in cidrs or []:
        try:
            net = ipaddress.ip_network(str(cidr).strip(), strict=False)
        except ValueError:
            print(f"[!] 忽略无效网段 {cidr}")
            continue
        if net.version != 4 or not net.is_private or net.prefixlen < 22:
            print(f"[!] 忽略网段 {cidr}（仅允许 /22 及更小的内网网段）")
            continue
        hosts.extend(str(h) for h in net.hosts())
        if len(hosts) >= SCAN_MAX_HOSTS:
            break
    return list(dict.fromkeys(hosts))[:SCAN_MAX_HOSTS]


def discover(scan: dict, known_ips: set) -> list:
    """按网段扫描 SNMP 设备，返回未建档的设备（name / ip / community）"""
    community = str(scan.get("community") or "public")
    port = int(scan.get("snmp_port") or 161)
    targets = [ip for ip in scan_hosts(scan.get("cidrs")) if ip not in known_ips]
    if not targets:
        return []

    def probe_one(ip):
        descr = snmp_get(ip, community, SYS_DESCR, port, timeout=1.0)
        if descr is None:
            return None
        name = snmp_get(ip, community, SYS_NAME, port, timeout=1.0)
        text = str(descr or "").lower()
        if "firewall" in text or "usg" in text or "fortigate" in text:
            device_type = "firewall"
        elif "router" in text or " ar" in text or "isr" in text:
            device_type = "router"
        elif "switch" in text or "s57" in text or "s56" in text or "catalyst" in text:
            device_type = "switch"
        else:
            device_type = "other"
        return {
            "name": str(name or ip).strip()[:100] or ip,
            "ip": ip,
            "device_type": device_type,
            "community": community,
            "snmp_port": port,
            "discovered": True,
            "sys_name": str(name).strip()[:100] if name else None,
        }

    print(f"[*] 自动发现：扫描 {len(targets)} 个地址…")
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        found = [item for item in pool.map(probe_one, targets) if item]
    print(f"[*] 自动发现：新发现 {len(found)} 台 SNMP 设备")
    return found


def collect_all(devices: list, collected_at: str) -> list:
    def one(item):
        try:
            result = collect_device(item, collected_at)
        except Exception as exc:  # 单台设备异常不影响其他设备
            print(f"[!] 采集 {item.get('ip')} 失败：{exc}")
            result = {"name": item.get("name") or item.get("ip"), "ip": item.get("ip"),
                      "device_type": item.get("device_type") or "other", "reachable": False,
                      "snmp_ok": False, "collected_at": collected_at, "interfaces": []}
        if item.get("discovered"):
            result["discovered"] = True
            result["sys_name"] = item.get("sys_name")
        return result

    if not devices:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(devices))) as pool:
        return list(pool.map(one, devices))


def run_test() -> int:
    cfg = load_config()
    print(f"[*] 总部地址：{cfg['server_url']}")
    try:
        result = api_post(cfg, "/api/v1/probe/heartbeat", {
            "hostname": platform.node(), "probe_version": PROBE_VERSION, "collected_at": now_text(),
        })
        print(f"[OK] 心跳成功，站点：{result.get('site_name')}（ID {result.get('site_id')}）")
        remote = api_get(cfg, "/api/v1/probe/config")
    except ProbeApiError as exc:
        print(f"[失败] {exc}")
        return 1
    devices = remote.get("devices") or []
    print(f"[OK] 总部下发 {len(devices)} 台设备，采集间隔 {remote.get('interval_seconds')} 秒")
    for item in devices[:20]:
        print(f"     - {item.get('name')}  {item.get('ip')}")
    scan = remote.get("scan") or {}
    if scan.get("enabled"):
        print(f"[OK] 自动发现网段：{', '.join(scan.get('cidrs') or [])}")
    return 0


LOG_PATH = BASE_DIR / "probe.log"
LOG_MAX_BYTES = 5 * 1024 * 1024


def _open_service_log():
    """以服务方式运行时输出写入 probe.log（超过 5MB 轮转一份 probe.log.1）"""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        stream = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    except OSError:
        pass


def main():
    if "--test" in sys.argv[1:]:
        sys.exit(run_test())
    service = "--service" in sys.argv[1:]
    if service:
        _open_service_log()
    cfg = load_config()
    print(f"[*] Edge probe {PROBE_VERSION} on {platform.node()}")
    print(f"[*] Reporting to {cfg['server_url']}")
    last_scan = 0.0
    while True:
        collected_at = now_text()
        remote = {}
        interval = int(cfg.get("interval_seconds") or 60)
        try:
            remote = fetch_remote_config(cfg)
            interval = int(remote.get("interval_seconds") or interval)
            device_list = [item for item in merge_devices(remote, cfg) if item.get("ip")]
            scan = remote.get("scan") or {}
            if scan.get("enabled") and time.time() - last_scan >= int(scan.get("interval_minutes") or 60) * 60:
                last_scan = time.time()
                device_list += discover(scan, {str(item["ip"]).strip() for item in device_list})
            devices = collect_all(device_list, collected_at)
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
                        print(f"[*] {now_text()} reported {len(devices)} devices")
                else:
                    print("[*] 心跳正常；总部尚未为本站点配置设备（请在「网络设备」中添加或开启自动发现）")
            else:
                spool_append("/api/v1/probe/heartbeat", heartbeat)
                if devices:
                    spool_append("/api/v1/probe/report", report)
                print("[*] 总部不可达，本轮已暂存")
        except Exception as exc:
            print(f"[!] {exc}")
        if service and LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            _open_service_log()
        time.sleep(max(30, interval))


if __name__ == "__main__":
    main()
