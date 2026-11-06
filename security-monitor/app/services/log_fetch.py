"""
通过 SSH 拉取服务器系统日志（供 AI 分析）

- 只拉取预定义的日志类型；自定义服务名 / 文件路径做严格白名单校验后再 shlex.quote，防命令注入
- Windows 用 PowerShell EncodedCommand，输出 Base64(UTF-8) 避免中文乱码
"""
import asyncio
import base64
import re
import shlex
from typing import Optional

from app.collector.log_collector import collector

# 日志类型：key -> (适用系统, 中文名, 说明)
LOG_TYPES = {
    "overview": (("linux", "windows"), "综合巡检", "系统错误/警告 + 内核 + 资源概况，适合定期体检"),
    "system": (("linux", "windows"), "系统日志", "Linux: journalctl 警告以上 / syslog；Windows: System 日志错误与警告"),
    "auth": (("linux",), "认证日志", "SSH 登录、sudo、账号变更（auth.log / secure）"),
    "kernel": (("linux",), "内核日志", "dmesg / journalctl -k：OOM、磁盘 I/O、驱动错误"),
    "service": (("linux",), "指定服务", "journalctl -u <服务名>，如 nginx、docker、mysql"),
    "file": (("linux",), "指定文件", "tail 指定日志文件，如 /var/log/nginx/error.log"),
    "application": (("windows",), "应用程序日志", "Application 日志错误与警告"),
    "security": (("windows",), "安全日志", "登录失败、账户锁定、创建用户、清空日志等安全事件"),
}

MAX_LINES = 2000
MAX_SINCE_MINUTES = 7 * 24 * 60

_UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,128}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./@+-]{1,250}$")
_FILE_SUFFIXES = (".log", ".out", ".err", ".txt")


class LogFetchError(ValueError):
    """参数不合法 / 拉取失败（消息可直接展示给用户）"""


def log_types_for(os_type: str) -> list:
    os_type = (os_type or "linux").lower()
    return [
        {"key": k, "label": label, "description": desc}
        for k, (systems, label, desc) in LOG_TYPES.items()
        if os_type in systems
    ]


def validate_unit(unit: Optional[str]) -> str:
    unit = (unit or "").strip()
    if not _UNIT_RE.match(unit):
        raise LogFetchError("服务名不合法（只允许字母、数字和 @ . _ : -），例如 nginx 或 docker.service")
    return unit


def validate_path(path: Optional[str]) -> str:
    """只允许读取 /var/log 下的文件，或其他目录下以 .log/.out/.err/.txt 结尾的文件"""
    path = (path or "").strip()
    if not _PATH_RE.match(path) or "/../" in path + "/" or "/./" in path + "/" or "//" in path:
        raise LogFetchError("文件路径不合法：必须是绝对路径，且不能包含 .. 或特殊字符")
    if not (path.startswith("/var/log/") or path.lower().endswith(_FILE_SUFFIXES)):
        raise LogFetchError("只允许读取 /var/log/ 下的文件，或以 .log / .out / .err / .txt 结尾的日志文件")
    return path


def _clamp(lines: int, since_minutes: int) -> tuple:
    lines = max(10, min(int(lines or 300), MAX_LINES))
    since = max(5, min(int(since_minutes or 1440), MAX_SINCE_MINUTES))
    return lines, since


def _linux_command(log_type: str, lines: int, since: int, unit: str = "", path: str = "") -> str:
    j_since = f"--since '{since} min ago' --no-pager -o short-iso"
    if log_type == "system":
        return (f"(journalctl {j_since} -p warning -n {lines} 2>/dev/null"
                f" || tail -n {lines} /var/log/syslog 2>/dev/null"
                f" || tail -n {lines} /var/log/messages 2>/dev/null)")
    if log_type == "auth":
        return (f"(tail -n {lines} /var/log/auth.log 2>/dev/null"
                f" || tail -n {lines} /var/log/secure 2>/dev/null"
                f" || journalctl {j_since} SYSLOG_FACILITY=4 SYSLOG_FACILITY=10 -n {lines} 2>/dev/null)")
    if log_type == "kernel":
        return (f"(journalctl -k {j_since} -n {lines} 2>/dev/null"
                f" || dmesg -T 2>/dev/null | tail -n {lines})")
    if log_type == "service":
        return f"journalctl -u {shlex.quote(unit)} {j_since} -n {lines} 2>&1"
    if log_type == "file":
        return f"tail -n {lines} -- {shlex.quote(path)} 2>&1"
    if log_type == "overview":
        half = max(10, lines // 2)
        return f"""
echo '===== 主机概况 ====='; hostname; uptime
echo '===== 磁盘 ====='; df -hP -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | head -30
echo '===== inode ====='; df -iP -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | awk 'NR==1 || $5+0 >= 80' | head -20
echo '===== 内存 ====='; free -m 2>/dev/null
echo '===== 内存占用 Top 5 ====='; ps aux --sort=-%mem 2>/dev/null | head -6
echo '===== CPU 占用 Top 5 ====='; ps aux --sort=-%cpu 2>/dev/null | head -6
echo '===== 失败的服务 ====='; systemctl --failed --no-legend --no-pager 2>/dev/null | head -30
echo '===== 系统错误/警告 ====='
(journalctl {j_since} -p warning -n {lines} 2>/dev/null || tail -n {lines} /var/log/syslog 2>/dev/null || tail -n {lines} /var/log/messages 2>/dev/null)
echo '===== 内核 ====='
(journalctl -k {j_since} -p warning -n {half} 2>/dev/null || dmesg -T 2>/dev/null | tail -n {half})
"""
    raise LogFetchError(f"Linux 不支持的日志类型：{log_type}")


_PS_EVENT_FORMAT = r"""
function Fmt($evs) {
  foreach ($e in @($evs)) {
    if (-not $e) { continue }
    $msg = ([string]$e.Message) -replace '\s+', ' '
    if ($msg.Length -gt 600) { $msg = $msg.Substring(0, 600) + '...' }
    '{0:yyyy-MM-dd HH:mm:ss} [{1}] {2} (ID {3}): {4}' -f $e.TimeCreated, $e.LevelDisplayName, $e.ProviderName, $e.Id, $msg
  }
}
"""


def _windows_script(log_type: str, lines: int, since: int) -> str:
    head = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8\n"
        f"$start = (Get-Date).AddMinutes(-{since})\n"
        f"$max = {lines}\n" + _PS_EVENT_FORMAT
    )
    if log_type in ("system", "application"):
        name = "System" if log_type == "system" else "Application"
        body = (f"$out = Fmt (Get-WinEvent -FilterHashtable @{{LogName='{name}'; Level=1,2,3; StartTime=$start}} "
                f"-MaxEvents $max | Sort-Object TimeCreated)\n")
    elif log_type == "security":
        body = ("$out = Fmt (Get-WinEvent -FilterHashtable @{LogName='Security'; "
                "ID=4625,4740,4720,4722,4724,4726,4732,4728,4756,1102,4697,4698,4719; StartTime=$start} "
                "-MaxEvents $max | Sort-Object TimeCreated)\n")
    elif log_type == "overview":
        half = max(10, lines // 2)
        body = f"""
$out = @()
$out += '===== 主机概况 ====='
$os = Get-CimInstance Win32_OperatingSystem
$out += ('{{0}} {{1}} 启动于 {{2}}' -f $env:COMPUTERNAME, $os.Caption, $os.LastBootUpTime)
$out += ('内存：总 {{0:N0}} MB，可用 {{1:N0}} MB' -f ($os.TotalVisibleMemorySize/1KB), ($os.FreePhysicalMemory/1KB))
$out += '===== 磁盘 ====='
foreach ($d in @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {{
  $out += ('{{0}} 总 {{1:N1}} GB，可用 {{2:N1}} GB ({{3:N0}}%)' -f $d.DeviceID, ($d.Size/1GB), ($d.FreeSpace/1GB), ($d.FreeSpace*100/[math]::Max($d.Size,1)))
}}
$out += '===== 已停止的自动启动服务 ====='
$out += @(Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" | Select-Object -First 30 | ForEach-Object {{ '{{0}} ({{1}})' -f $_.Name, $_.State }})
$out += '===== System 错误/警告 ====='
$out += Fmt (Get-WinEvent -FilterHashtable @{{LogName='System'; Level=1,2,3; StartTime=$start}} -MaxEvents {half} | Sort-Object TimeCreated)
$out += '===== Application 错误/警告 ====='
$out += Fmt (Get-WinEvent -FilterHashtable @{{LogName='Application'; Level=1,2,3; StartTime=$start}} -MaxEvents {half} | Sort-Object TimeCreated)
"""
    else:
        raise LogFetchError(f"Windows 不支持的日志类型：{log_type}")
    tail = "$text = (@($out) -join \"`n\")\n[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$text))\n"
    return head + body + tail


def build_command(os_type: str, log_type: str, lines: int = 300, since_minutes: int = 1440,
                  unit: Optional[str] = None, path: Optional[str] = None) -> str:
    os_type = (os_type or "linux").lower()
    if log_type not in LOG_TYPES or os_type not in LOG_TYPES[log_type][0]:
        raise LogFetchError(f"{os_type} 服务器不支持日志类型：{log_type}")
    lines, since = _clamp(lines, since_minutes)
    if os_type == "windows":
        script = _windows_script(log_type, lines, since)
        return "powershell -NoProfile -EncodedCommand " + base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return _linux_command(
        log_type, lines, since,
        unit=validate_unit(unit) if log_type == "service" else "",
        path=validate_path(path) if log_type == "file" else "",
    )


def decode_output(os_type: str, raw: str) -> str:
    if (os_type or "").lower() != "windows":
        return raw or ""
    text = (raw or "").strip()
    try:
        return base64.b64decode(text, validate=False).decode("utf-8")
    except Exception:
        return text


async def fetch_server_logs(server: dict, log_type: str, lines: int = 300, since_minutes: int = 1440,
                            unit: Optional[str] = None, path: Optional[str] = None) -> str:
    """server 为 servers 表一行（password 已解密）"""
    os_type = (server.get("os_type") or "linux").lower()
    command = build_command(os_type, log_type, lines, since_minutes, unit, path)
    if not server.get("password"):
        raise LogFetchError("该服务器未配置 SSH 密码，无法远程拉取日志；可改用「粘贴日志」方式分析")
    username = server.get("username") or ("Administrator" if os_type == "windows" else "root")
    try:
        client = await collector.connect(
            server.get("id"), server["ip"], int(server.get("port") or 22), username, server.get("password"),
        )
    except Exception as e:
        raise LogFetchError(f"SSH 连接失败：{collector._error_text(e)}")
    try:
        raw = await asyncio.to_thread(collector._execute_command, client, command)
    except Exception as e:
        raise LogFetchError(f"执行日志命令失败：{e}")
    finally:
        client.close()
    return decode_output(os_type, raw)
