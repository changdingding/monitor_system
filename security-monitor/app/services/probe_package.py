"""生成写好总部地址和 Token 的边缘采集器安装包（Windows 图形向导 / Linux systemd）。"""
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


# (包内路径, 源文件相对 probe/ 的路径)
PACKAGE_FILES = [
    ("probe.py", "probe.py"),
    ("snmp_client.py", "snmp_client.py"),
    ("请先阅读.txt", "请先阅读.txt"),
    ("安装采集器.bat", "安装采集器.bat"),
    ("启动采集器.bat", "启动采集器.bat"),
    ("run.sh", "run.sh"),
    ("windows/wizard.ps1", "windows/wizard.ps1"),
    ("linux/install.sh", "linux/install.sh"),
]


def _normalize(name: str, data: bytes) -> bytes:
    """Windows 脚本用 CRLF + BOM（中文路径 / 记事本友好），其余用 LF"""
    if not name.endswith((".bat", ".txt", ".py", ".sh", ".ps1")):
        return data
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    if name.endswith((".bat", ".txt", ".ps1")):
        return text.replace("\n", "\r\n").encode("utf-8-sig")
    return text.encode("utf-8")


def build_probe_package(site_name: str, server_url: str, probe_token: str) -> tuple[bytes, str]:
    """生成安装包：解压后 Windows 双击「安装采集器.bat」走图形向导，Linux 执行 linux/install.sh。
    设备清单不写进包里，由采集器从总部拉取。"""
    folder = f"EdgeProbe-{_safe_name(site_name)}"
    config = {
        "server_url": server_url.rstrip("/"),
        "probe_token": probe_token,
        "interval_seconds": 60,
        "devices": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, rel in PACKAGE_FILES:
            path = PROBE_ROOT / rel
            if not path.exists():
                raise FileNotFoundError(f"缺少采集器文件: {path}")
            _add_file(zf, f"{folder}/{arcname}", _normalize(arcname, path.read_bytes()),
                      unix_exec=arcname.endswith(".sh"))
        _add_file(
            zf,
            f"{folder}/config.json",
            json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return buf.getvalue(), f"{folder}.zip"
