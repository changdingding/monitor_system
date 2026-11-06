"""
日志采集器模块
"""
import asyncio
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import paramiko
from app.core.config import settings
from app.notifier.feishu import format_event_time

logger = logging.getLogger(__name__)

_ISO_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
_BSD_PREFIX = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")


class LogCollector:
    """日志采集器"""
    
    def __init__(self):
        # SSH 拉取时回看的日志时间窗口（分钟）。须大于采集间隔，否则两轮之间会漏事件；
        # 窗口重叠产生的重复事件由规则引擎 event_key 去重。调度器按采集间隔自动调整。
        self.lookback_minutes = 5

    def set_interval(self, interval_seconds: int) -> None:
        """根据采集间隔调整回看窗口：至少 5 分钟，且不小于 2 倍间隔"""
        self.lookback_minutes = max(5, -(-int(interval_seconds) * 2 // 60))

    def _with_lookback(self, command: str) -> str:
        n = int(self.lookback_minutes)
        return command.replace("'5 min ago'", f"'{n} min ago'").replace("AddMinutes(-5)", f"AddMinutes(-{n})")
    
    @staticmethod
    def _line_time(line: str) -> Optional[str]:
        """
        取 syslog 行首时间，返回 'YYYY-MM-DD HH:MM:SS'（本地时间）；取不到返回 None。
        支持传统格式 'Nov  6 11:42:00 host ...' 与 rsyslog/journalctl ISO 格式 '2026-11-06T11:42:00...'。
        SSH 拉取的是一个时间窗口内的重复内容，事件时间必须取自日志本身，否则去重键每轮都会变化，导致重复告警。
        """
        m = _ISO_PREFIX.match(line)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        m = _BSD_PREFIX.match(line)
        if m:
            now = datetime.now()
            try:
                dt = datetime.strptime(" ".join(m.group(1).split()), "%b %d %H:%M:%S").replace(year=now.year)
            except ValueError:
                return None
            if dt > now + timedelta(days=1):  # 跨年：12 月的日志在 1 月被读到
                dt = dt.replace(year=now.year - 1)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return None

    @staticmethod
    def _host_entry(host: str, port: int) -> str:
        return host if int(port) == 22 else f"[{host}]:{port}"

    def _get_ssh_client(
        self,
        host: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        key: Optional[str] = None,
        host_key: Optional[str] = None,
    ) -> paramiko.SSHClient:
        """
        创建 SSH 客户端。
        主机密钥采用首次信任（TOFU）：host_key 为空时接受并由调用方记录；
        已记录时严格校验，不一致抛出 BadHostKeyException（可能是中间人攻击或服务器重装）。
        """
        client = paramiko.SSHClient()
        if host_key:
            entry_name = self._host_entry(host, port)
            entry = paramiko.hostkeys.HostKeyEntry.from_line(f"{entry_name} {host_key}")
            if entry is None:
                raise ValueError("已记录的主机密钥格式无效，请在服务器管理中重置主机密钥")
            client.get_host_keys().add(entry_name, entry.key.get_name(), entry.key)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": 10,
        }
        
        if key:
            from io import StringIO
            private_key = paramiko.RSAKey.from_private_key(StringIO(key))
            connect_kwargs["pkey"] = private_key
        elif password:
            connect_kwargs["password"] = password
        
        client.connect(**connect_kwargs)
        return client
    
    def _execute_command(self, client: paramiko.SSHClient, command: str) -> str:
        """执行 SSH 命令"""
        stdin, stdout, stderr = client.exec_command(command, timeout=60)
        raw = stdout.read()
        for enc in ("utf-8", "gbk", "cp936"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="ignore")

    @staticmethod
    def _remote_host_key(client: paramiko.SSHClient) -> str:
        k = client.get_transport().get_remote_server_key()
        return f"{k.get_name()} {k.get_base64()}"

    @staticmethod
    def _error_text(e: Exception) -> str:
        if isinstance(e, paramiko.BadHostKeyException):
            return ("主机密钥与首次连接时记录的不一致，已拒绝连接（可能存在中间人攻击）。"
                    "如确认服务器已重装系统，请在服务器管理中重置主机密钥")
        return str(e)

    async def _load_host_key(self, server_id: Optional[int]) -> Optional[str]:
        if not server_id:
            return None
        from app.core.database import db
        row = await db.fetch_one("SELECT ssh_host_key FROM servers WHERE id=$1", server_id)
        return (row or {}).get("ssh_host_key")

    async def _remember_host_key(self, server_id: Optional[int], known: Optional[str], seen: str) -> None:
        if not server_id or known:
            return
        from app.core.database import db
        await db.execute(
            "UPDATE servers SET ssh_host_key=$1 WHERE id=$2 AND ssh_host_key IS NULL", seen, server_id
        )
        logger.info(f"服务器 {server_id} 首次连接，已记录主机密钥 {seen.split(' ')[0]}")

    async def connect(self, server_id, host, port, username, password=None, key=None) -> paramiko.SSHClient:
        """在线程中建立 SSH 连接，并完成主机密钥校验 / 首次记录"""
        known = await self._load_host_key(server_id)
        client = await asyncio.to_thread(self._get_ssh_client, host, port, username, password, key, known)
        try:
            await self._remember_host_key(server_id, known, self._remote_host_key(client))
        except Exception:
            client.close()
            raise
        return client

    async def test_connection(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: Optional[str] = None,
        key: Optional[str] = None,
        server_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """测试 SSH 连通性（异步，不阻塞事件循环）"""
        try:
            client = await self.connect(server_id, host, port, username, password, key)
        except Exception as e:
            return {"success": False, "message": self._error_text(e)}
        try:
            await asyncio.to_thread(client.exec_command, "echo ok", 10)
            return {"success": True, "message": "连接成功"}
        except Exception as e:
            return {"success": False, "message": self._error_text(e)}
        finally:
            client.close()

    def get_login_command(self, os_type: str) -> str:
        """获取登录/退出日志；Windows 带 LogonType，只关心交互/远程登录"""
        if os_type == "windows":
            # LogonType: 2交互 7解锁 10远程桌面 11缓存交互；排除 3网络 4批处理 5服务 等噪音
            return r'''powershell -Command "$events = Get-WinEvent -FilterHashtable @{LogName='Security'; ID=4624,4634,4647; StartTime=(Get-Date).AddMinutes(-5)} -ErrorAction SilentlyContinue; if (-not $events) { '' } else { $events | Sort-Object TimeCreated | Select-Object TimeCreated, Id, @{N='Account';E={ if ($_.Id -eq 4624) { $_.Properties[5].Value } else { $_.Properties[1].Value } }}, @{N='IpAddress';E={ if ($_.Id -eq 4624) { $_.Properties[18].Value } else { '' } }}, @{N='LogonType';E={ if ($_.Id -eq 4624) { $_.Properties[8].Value } else { 0 } }} | ConvertTo-Json -Compress }"'''
        else:
            return r"""
(grep -E 'Accepted (password|publickey)|session opened for user|session closed for user|Disconnected from user' /var/log/auth.log 2>/dev/null || \
 grep -E 'Accepted (password|publickey)|session opened for user|session closed for user|Disconnected from user' /var/log/secure 2>/dev/null || \
 journalctl -u ssh --since '5 min ago' --no-pager 2>/dev/null | grep -E 'Accepted|session opened|session closed|Disconnected from user' || true) | tail -50
"""

    def get_high_risk_command(self, os_type: str) -> str:
        """获取高危操作采集命令（Windows 输出 UTF-8 Base64 JSON，避免中文乱码）"""
        if os_type == "windows":
            import base64
            ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8
$start = (Get-Date).AddMinutes(-5)
$raw = Get-WinEvent -FilterHashtable @{ LogName='Security'; ID=4697,4698,4720,4732,1102; StartTime=$start }
if (-not $raw) {
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('[]'))
  return
}
$sensitive = @(
  'administrators','管理员','remote desktop users','远程桌面用户',
  'backup operators','备份操作员','power users','domain admins','enterprise admins'
)
$list = @()
foreach ($e in @($raw | Sort-Object TimeCreated)) {
  $xml = [xml]$e.ToXml()
  $d = @{}
  foreach ($n in @($xml.Event.EventData.Data)) {
    if ($n.Name) { $d[$n.Name] = [string]$n.'#text' }
  }
  $op = $d['SubjectUserName']
  if ($op -and $op.EndsWith('$')) { continue }
  $target = ''
  $group = ''
  $desc = ''
  $otype = ''
  $level = 'high'
  switch ([int]$e.Id) {
    4720 {
      $target = $d['TargetUserName']
      if (-not $target) { $target = $d['SamAccountName'] }
      $who = if ($op) { $op } else { '未知账户' }
      $nu = if ($target) { $target } else { '未知' }
      $desc = "账户「$who」创建了新用户「$nu」"
      $otype = 'user_create'
      $level = 'critical'
    }
    4732 {
      $group = $d['TargetUserName']
      $gl = ([string]$group).ToLower()
      $ok = $false
      foreach ($s in $sensitive) {
        if ($gl -eq $s -or $gl.Contains($s)) { $ok = $true; break }
      }
      if (-not $ok) { continue }
      $member = $d['MemberName']
      if (-not $member) { $member = $d['MemberSid'] }
      $who = if ($op) { $op } else { '未知账户' }
      $desc = "账户「$who」将「$member」加入了特权组「$group」"
      $otype = 'group_member_change'
      $target = $member
    }
    4697 {
      $svc = $d['ServiceName']
      if (-not $svc) { $svc = '未知服务' }
      $who = if ($op) { $op } else { '未知账户' }
      $desc = "账户「$who」安装了系统服务「$svc」"
      $otype = 'service_install'
    }
    4698 {
      $task = $d['TaskName']
      if (-not $task) { $task = '未知任务' }
      $who = if ($op) { $op } else { '未知账户' }
      $desc = "账户「$who」创建了计划任务「$task」"
      $otype = 'scheduled_task'
    }
    1102 {
      $who = if ($op) { $op } else { '未知账户' }
      $desc = "账户「$who」清空了 Windows 安全日志"
      $otype = 'log_clear'
      $level = 'critical'
    }
    default { continue }
  }
  if (-not $desc) { continue }
  $list += [pscustomobject]@{
    TimeCreated = $e.TimeCreated
    Id = [int]$e.Id
    Account = $(if ($op) { $op } else { '未知账户' })
    TargetAccount = $target
    GroupName = $group
    Description = $desc
    OperationType = $otype
    RiskLevel = $level
  }
}
if ($list.Count -eq 0) { $json = '[]' }
elseif ($list.Count -eq 1) { $json = '[' + ($list | ConvertTo-Json -Compress -Depth 4) + ']' }
else { $json = ($list | ConvertTo-Json -Compress -Depth 4) }
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
            encoded = base64.b64encode(ps.encode("utf-16le")).decode("ascii")
            return f"powershell -NoProfile -EncodedCommand {encoded}"
        else:
            return """
(grep -E 'sudo:|COMMAND=' /var/log/auth.log 2>/dev/null || grep -E 'sudo:|COMMAND=' /var/log/secure 2>/dev/null || journalctl --since '5 min ago' --no-pager 2>/dev/null | grep -i sudo || true) | tail -30
(grep -E 'useradd|userdel|usermod|passwd:' /var/log/auth.log 2>/dev/null || grep -E 'useradd|userdel|usermod|passwd:' /var/log/secure 2>/dev/null || true) | tail -20
(grep -E 'systemctl (start|stop|restart|enable|disable)' /var/log/messages 2>/dev/null || journalctl --since '5 min ago' --no-pager 2>/dev/null | grep -E 'systemctl (start|stop|restart)' || true) | tail -20
"""
    
    async def collect_logs(
        self,
        server_id: int,
        host: str,
        os_type: str,
        port: int = 22,
        username: str = "root",
        password: Optional[str] = None,
        key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        采集服务器日志

        Returns:
            Dict: success / error / login_logs / high_risk_ops
        """
        result = {
            "success": False,
            "error": None,
            "login_logs": [],
            "high_risk_ops": []
        }

        client = None
        try:
            # SSH 连接放在线程中，避免阻塞事件循环；含主机密钥校验
            client = await self.connect(server_id, host, port, username, password, key)

            login_cmd = self._with_lookback(self.get_login_command(os_type))
            login_output = await asyncio.to_thread(self._execute_command, client, login_cmd)
            result["login_logs"] = self._parse_login_logs(login_output, os_type)

            high_risk_cmd = self._with_lookback(self.get_high_risk_command(os_type))
            high_risk_output = await asyncio.to_thread(self._execute_command, client, high_risk_cmd)
            result["high_risk_ops"] = self._parse_high_risk_ops(high_risk_output, os_type)

            result["success"] = True
            logger.info(
                f"日志采集成功：{host}，登录={len(result['login_logs'])}，高危={len(result['high_risk_ops'])}"
            )
        except Exception as e:
            result["error"] = self._error_text(e)
            logger.error(f"日志采集失败 {host}: {result['error']}")
        finally:
            if client is not None:
                client.close()

        return result
    
    def _parse_login_logs(self, output: str, os_type: str) -> List[Dict]:
        """解析登录/退出日志，附带 action=login|logout"""
        logs = []

        if os_type == "windows":
            try:
                import json
                events = json.loads(output) if output.strip() else []
                if isinstance(events, dict):
                    events = [events]

                for event in events:
                    event_id = int(event.get("Id") or 0)
                    if event_id == 4624:
                        # 仅保留真人交互/远程登录，过滤网络共享等噪音
                        logon_type = str(event.get("LogonType") or "")
                        if logon_type not in ("2", "7", "10", "11"):
                            continue
                        action = "login"
                        action_text = "账号登录"
                    elif event_id in (4634, 4647):
                        action = "logout"
                        action_text = "账号退出"
                    else:
                        continue

                    account = (event.get("Account") or "").strip()
                    if not account or account.endswith("$"):
                        continue

                    logs.append({
                        "action": action,
                        "action_text": action_text,
                        "event_id": event_id,
                        "account": account,
                        "login_time": format_event_time(event.get("TimeCreated", "")),
                        "source_ip": event.get("IpAddress", "") or "",
                        "logon_type": str(event.get("LogonType") or ""),
                    })

                # 按时间正序，便于会话状态机正确处理
                logs.sort(key=lambda x: x.get("login_time") or "")
            except Exception as e:
                logger.error(f"解析 Windows 登录日志失败：{e}")
        else:
            for line in output.split("\n"):
                line = line.strip()
                if not line:
                    continue
                lower = line.lower()
                action = None
                action_text = None
                if "accepted password" in lower or "accepted publickey" in lower or "session opened for user" in lower:
                    action, action_text = "login", "账号登录"
                elif "session closed for user" in lower or "disconnected from" in lower:
                    action, action_text = "logout", "账号退出"
                else:
                    continue

                account = ""
                parts = line.split()
                for i, p in enumerate(parts):
                    pl = p.lower()
                    if pl == "user" and i + 1 < len(parts):
                        account = parts[i + 1].strip("()")
                        break
                    if "for" == pl and i + 1 < len(parts) and parts[i + 1].lower() != "user":
                        account = parts[i + 1]
                        break
                if not account:
                    # Accepted ... for root from ...
                    m = re.search(r"\bfor\s+(\S+)", line, re.I)
                    if m:
                        account = m.group(1)

                source_ip = ""
                m_ip = re.search(r"\bfrom\s+(\d+\.\d+\.\d+\.\d+|\S+)", line, re.I)
                if m_ip:
                    source_ip = m_ip.group(1)

                # 取日志行前缀时间（兼容传统 / ISO 两种格式）
                login_time = self._line_time(line) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                logs.append({
                    "action": action,
                    "action_text": action_text,
                    "event_id": 0,
                    "account": account or "unknown",
                    "login_time": login_time,
                    "source_ip": source_ip,
                    "raw_line": line[:300],
                })

        return logs
    
    def _parse_high_risk_ops(self, output: str, os_type: str) -> List[Dict]:
        """解析高危操作日志"""
        ops = []

        if os_type == "windows":
            try:
                import json
                import base64

                text = (output or "").strip()
                if not text:
                    return ops
                # 新格式：Base64(UTF-8 JSON)；兼容旧版纯 JSON
                try:
                    decoded = base64.b64decode(text, validate=False).decode("utf-8")
                    if decoded.lstrip().startswith(("[", "{")):
                        text = decoded
                except Exception:
                    pass

                events = json.loads(text) if text else []
                if isinstance(events, dict):
                    events = [events]

                created_users = set()
                for event in events:
                    if int(event.get("Id") or 0) == 4720:
                        t = (event.get("TargetAccount") or "").strip().lower()
                        if t:
                            created_users.add(t)

                for event in events:
                    event_id = int(event.get("Id") or 0)
                    op_type = (event.get("OperationType") or "").strip()
                    risk_level = (event.get("RiskLevel") or "high").strip().lower()
                    account = str(event.get("Account") or "").strip()
                    target = str(event.get("TargetAccount") or "").strip()
                    group = str(event.get("GroupName") or "").strip()
                    desc = str(event.get("Description") or "").strip()

                    # 兼容旧字段
                    if not op_type:
                        legacy = {
                            4697: ("service_install", "high"),
                            4698: ("scheduled_task", "high"),
                            4720: ("user_create", "critical"),
                            4732: ("group_member_change", "high"),
                            1102: ("log_clear", "critical"),
                        }
                        if event_id not in legacy:
                            continue
                        op_type, risk_level = legacy[event_id]
                    if not desc:
                        msg = str(event.get("Message") or "")
                        # 旧 Message 常乱码，生成兜底文案
                        if op_type == "user_create":
                            desc = f"检测到创建用户账户（执行账户：{account or '未知'}）"
                        else:
                            desc = f"检测到高危操作 {op_type}（执行账户：{account or '未知'}）"
                        if msg.count("?") < 3 and len(msg) > 10 and "S-1-5" not in msg[:40]:
                            desc = msg[:200]

                    # 创建用户后加入普通组：跳过
                    if op_type == "group_member_change":
                        gl = group.lower()
                        sensitive = (
                            "administrators", "管理员", "remote desktop", "远程桌面",
                            "backup", "备份", "power users", "domain admins",
                        )
                        if group and not any(s in gl for s in sensitive):
                            continue
                        if target and target.lower() in created_users:
                            continue
                        # SID 对象字符串不当账户
                        if account.startswith("{") or "BinaryLength" in account:
                            account = "未知账户"
                        if target.startswith("S-1-") or target.startswith("{"):
                            # 保留 SID 不如跳过难读告警；特权组仍提示
                            desc = f"检测到特权组成员变更（组：{group or '未知'}）"

                    if isinstance(account, dict) or str(account).startswith("{"):
                        account = "未知账户"

                    ops.append({
                        "operation_type": op_type,
                        "risk_level": risk_level,
                        "account": account or "未知账户",
                        "target_account": target,
                        "group_name": group,
                        "operation_time": format_event_time(event.get("TimeCreated", "")),
                        "description": desc[:300],
                        "collect_source": "ssh",
                    })
            except Exception as e:
                logger.error(f"解析 Windows 高危操作日志失败：{e}")
        else:
            for line in output.split("\n"):
                line = line.strip()
                if not line:
                    continue

                lower = line.lower()
                op_time = self._line_time(line) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if "sudo" in lower and ("command" in lower or "session" in lower or "tty=" in lower):
                    account = "root"
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "sudo:" and i + 1 < len(parts):
                            account = parts[i + 1]
                            break
                    ops.append({
                        "operation_type": "sudo_command",
                        "risk_level": "high",
                        "account": account,
                        "operation_time": op_time,
                        "description": line[:300],
                        "collect_source": "ssh",
                    })
                elif any(k in lower for k in ("useradd", "userdel", "usermod", "passwd:")):
                    ops.append({
                        "operation_type": "user_change",
                        "risk_level": "critical",
                        "account": "root",
                        "operation_time": op_time,
                        "description": line[:300],
                        "collect_source": "ssh",
                    })
                elif "systemctl" in lower and any(k in lower for k in ("start", "stop", "restart", "enable", "disable")):
                    ops.append({
                        "operation_type": "service_change",
                        "risk_level": "high",
                        "account": "root",
                        "operation_time": op_time,
                        "description": line[:300],
                        "collect_source": "ssh",
                    })

        return ops


# 全局采集器实例
collector = LogCollector()
