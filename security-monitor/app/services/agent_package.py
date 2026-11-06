"""
生成预配置的 Agent 安装包（ZIP）
Windows：图形向导（双击安装Agent.bat）
Linux：一键 install.sh
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent"


def _safe_name(name: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (name or "server").strip())
    return (text[:40] or "server").strip("_")


def _config_bytes(server_url: str, agent_token: str) -> bytes:
    payload = {
        "server_url": server_url.rstrip("/"),
        "agent_token": agent_token,
        "poll_interval_seconds": 30,
        "heartbeat_interval_seconds": 60,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _add_file(zf: zipfile.ZipFile, arcname: str, data: bytes, unix_exec: bool = False):
    info = zipfile.ZipInfo(arcname)
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    if unix_exec:
        info.create_system = 3  # Unix
        info.external_attr = 0o755 << 16
    zf.writestr(info, data)


def build_windows_package(server_name: str, server_url: str, agent_token: str) -> tuple[bytes, str]:
    folder = f"SecurityMonitorAgent-Windows-{_safe_name(server_name)}"
    win_dir = AGENT_ROOT / "windows"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        mapping = {
            "安装Agent.bat": win_dir / "安装Agent.bat",
            "wizard.ps1": win_dir / "wizard.ps1",
            "agent.ps1": win_dir / "agent.ps1",
            "test_connection.ps1": win_dir / "test_connection.ps1",
            "测试连接.bat": win_dir / "测试连接.bat",
            "请先阅读.txt": win_dir / "请先阅读.txt",
        }
        for name, path in mapping.items():
            if not path.exists():
                raise FileNotFoundError(f"缺少安装文件: {path}")
            data = path.read_bytes()
            # bat/ps1 用 CRLF，避免记事本/CMD 异常
            if name.endswith((".bat", ".ps1", ".txt")):
                text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\n", "\r\n")
                if name.endswith(".bat"):
                    data = ("\ufeff" + text).encode("utf-8")  # BOM 便于中文路径
                else:
                    data = text.encode("utf-8-sig")
            _add_file(zf, f"{folder}/{name}", data)
        _add_file(zf, f"{folder}/config.json", _config_bytes(server_url, agent_token))
    return buf.getvalue(), f"{folder}.zip"


def build_linux_package(server_name: str, server_url: str, agent_token: str) -> tuple[bytes, str]:
    folder = f"SecurityMonitorAgent-Linux-{_safe_name(server_name)}"
    linux_dir = AGENT_ROOT / "linux"
    agent_py = AGENT_ROOT / "agent.py"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if not agent_py.exists():
            raise FileNotFoundError(f"缺少 {agent_py}")
        _add_file(zf, f"{folder}/agent.py", agent_py.read_bytes())
        install_sh = linux_dir / "install.sh"
        readme = linux_dir / "请先阅读.txt"
        if not install_sh.exists():
            raise FileNotFoundError(f"缺少 {install_sh}")
        _add_file(zf, f"{folder}/install.sh", install_sh.read_bytes(), unix_exec=True)
        if readme.exists():
            _add_file(zf, f"{folder}/请先阅读.txt", readme.read_bytes())
        _add_file(zf, f"{folder}/config.json", _config_bytes(server_url, agent_token))
    return buf.getvalue(), f"{folder}.zip"


def build_agent_package(os_type: str, server_name: str, server_url: str, agent_token: str) -> tuple[bytes, str]:
    os_type = (os_type or "").lower().strip()
    if os_type in ("windows", "win"):
        return build_windows_package(server_name, server_url, agent_token)
    if os_type in ("linux", "unix"):
        return build_linux_package(server_name, server_url, agent_token)
    raise ValueError("os 参数仅支持 windows 或 linux")
