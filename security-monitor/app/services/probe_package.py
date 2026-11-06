"""生成写好总部地址和 Token 的门店采集器压缩包。"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

PROBE_ROOT = Path(__file__).resolve().parents[2] / "probe"


def _safe_name(name: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (name or "site").strip())
    return (text[:40] or "site").strip("_")


def _add_file(zf: zipfile.ZipFile, arcname: str, data: bytes, unix_exec: bool = False):
    info = zipfile.ZipInfo(arcname)
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    if unix_exec:
        info.create_system = 3
        info.external_attr = 0o755 << 16
    zf.writestr(info, data)


def build_probe_package(site_name: str, server_url: str, probe_token: str) -> tuple[bytes, str]:
    folder = f"StoreProbe-{_safe_name(site_name)}"
    config = {
        "server_url": server_url.rstrip("/"),
        "probe_token": probe_token,
        "interval_seconds": 60,
        "devices": [],
    }
    names = [
        "probe.py",
        "snmp_client.py",
        "请先阅读.txt",
        "启动采集器.bat",
        "run.sh",
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            path = PROBE_ROOT / name
            if not path.exists():
                raise FileNotFoundError(f"缺少采集器文件: {path}")
            data = path.read_bytes()
            if name.endswith((".bat", ".txt", ".py", ".sh")):
                text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
                if name.endswith(".bat") or name.endswith(".txt"):
                    data = text.replace("\n", "\r\n").encode("utf-8-sig")
                else:
                    data = text.encode("utf-8")
            _add_file(zf, f"{folder}/{name}", data, unix_exec=name.endswith(".sh"))
        _add_file(
            zf,
            f"{folder}/config.json",
            json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return buf.getvalue(), f"{folder}.zip"
