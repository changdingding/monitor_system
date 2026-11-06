"""
规则引擎模块
"""
import hashlib
import logging
import re
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.database import db
from app.notifier.feishu import notifier

logger = logging.getLogger(__name__)


class RuleEngine:
    """规则引擎：事件去重 + 入库 + 飞书推送（同一事件只告警一次）"""

    def __init__(self):
        self.alert_cache = {}
        self.dedup_window = 60

    async def _load_debounce(self):
        try:
            await notifier.refresh_config()
            self.dedup_window = notifier.debounce_seconds or 60
        except Exception:
            pass

    @staticmethod
    def _event_key(*parts: Any) -> str:
        raw = "|".join("" if p is None else str(p) for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _extract_created_user(description: str) -> str:
        m = re.search(r"新用户[「\"]([^」\"]+)[」\"]", description or "")
        return (m.group(1).strip() if m else "")

    @staticmethod
    def _is_low_quality_user_create(op: Dict[str, Any]) -> bool:
        """过滤旧 Agent / 错误解析产生的垃圾「创建用户」事件"""
        account = (op.get("account") or "").strip()
        desc = (op.get("description") or "").strip()
        target = (op.get("target_account") or "").strip() or RuleEngine._extract_created_user(desc)
        hostname = (op.get("hostname") or "").strip()

        if desc.count("?") >= 3:
            return True
        # 没有标准中文描述、也解析不出新用户名
        if "「" not in desc and '"' not in desc and not target:
            return True
        # 执行账户像机器名，且不是可读句式
        if account and hostname and account.lower() == hostname.lower() and "「" not in desc:
            return True
        # 云主机名风格（如 IZIR004XEIQ8XIZ）且描述不可读
        if re.fullmatch(r"[A-Za-z0-9\-]{10,}", account or "") and "「" not in desc:
            return True
        return False

    async def _already_alerted(self, event_key: str) -> bool:
        """数据库级去重：同一事件指纹只处理一次，避免每 2 分钟重复推送"""
        try:
            row = await db.fetch_one(
                "SELECT id FROM alert_logs WHERE event_key = $1 LIMIT 1",
                event_key,
            )
            return row is not None
        except Exception as e:
            # 兼容尚未迁移 event_key 字段的情况，回退内存防抖
            logger.warning(f"事件去重查询失败，回退内存防抖：{e}")
            return False

    def _should_alert_memory(self, key: str) -> bool:
        now = datetime.now()
        if key in self.alert_cache:
            last_alert_time = self.alert_cache[key]
            if (now - last_alert_time).total_seconds() < self.dedup_window:
                return False
        self.alert_cache[key] = now
        return True

    async def _save_alert_log(
        self,
        source_type: str,
        source_id: int,
        source_name: str,
        level: str,
        event_type: str,
        event_description: str,
        raw_data: str = "",
        notified: bool = False,
        event_key: Optional[str] = None,
    ):
        try:
            await db.execute(
                """
                INSERT INTO alert_logs
                    (source_type, source_id, source_name, level, event_type, event_description, raw_data, status, notified, event_key)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'new', $8, $9)
                """,
                source_type,
                source_id,
                source_name,
                level,
                event_type,
                event_description,
                raw_data,
                notified,
                event_key,
            )
        except Exception as e:
            # 若无 event_key 列，退回旧插入
            if "event_key" in str(e):
                try:
                    await db.execute(
                        """
                        INSERT INTO alert_logs
                            (source_type, source_id, source_name, level, event_type, event_description, raw_data, status, notified)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, 'new', $8)
                        """,
                        source_type,
                        source_id,
                        source_name,
                        level,
                        event_type,
                        event_description,
                        raw_data,
                        notified,
                    )
                    return
                except Exception as e2:
                    logger.error(f"保存告警日志失败：{e2}")
                    return
            logger.error(f"保存告警日志失败：{e}")

    def _parse_time(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if not value:
            return datetime.now()
        text = str(value).replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%b %d %H:%M:%S",
        ):
            try:
                if fmt == "%b %d %H:%M:%S":
                    dt = datetime.strptime(text[:15], fmt)
                    return dt.replace(year=datetime.now().year)
                return datetime.strptime(text[:26], fmt) if "%f" in fmt else datetime.strptime(text[:19], fmt)
            except Exception:
                continue
        return datetime.now()

    async def process_login_log(self, log: Dict[str, Any]) -> bool:
        """
        按「账号会话状态」处理登录/退出：
        - 登录：仅当该账号当前不是已登录时推送一次，并标记为已登录
        - 退出：仅当该账号当前是已登录时推送一次，并标记为已退出
        - 仍保持登录期间，定时采集到的重复登录事件一律不推送
        不依赖事件时间去重。
        """
        await self._load_debounce()
        account = (log.get("account") or "").strip()
        source_ip = (log.get("source_ip") or "").strip()
        login_time = log.get("login_time", "")
        server_id = int(log.get("server_id") or 0)
        server_name = log.get("server_name", "")
        server_host = log.get("server_host", "")
        os_type = log.get("os_type", "linux")
        action = log.get("action") or "login"
        action_text = log.get("action_text") or ("账号登录" if action == "login" else "账号退出")

        system_accounts = ["sshd", "SYSTEM", "dwm", "umfd", "LOCAL SERVICE", "NETWORK SERVICE", "ANONYMOUS LOGON"]
        if not account or any(account.upper().startswith(sa.upper()) for sa in system_accounts):
            return False
        if account.endswith("$"):
            return False

        # 读取该服务器该账号的会话状态
        session = await self._get_account_session(server_id, account)
        current_state = (session or {}).get("state") or "logged_out"

        if action == "login":
            if current_state == "logged_in":
                logger.info(
                    f"账号仍在登录中，跳过重复登录推送：{account}@{server_host}"
                )
                # 可刷新心跳时间，但不推送
                await self._upsert_account_session(
                    server_id, account, "logged_in", login_time, source_ip
                )
                return False
            # 从未登录/已退出 → 登录：推送一次
            should_notify = True
            new_state = "logged_in"
        elif action == "logout":
            if current_state != "logged_in":
                logger.info(
                    f"账号当前未处于登录态，跳过退出推送：{account}@{server_host}"
                )
                await self._upsert_account_session(
                    server_id, account, "logged_out", login_time, source_ip
                )
                return False
            # 已登录 → 退出：推送一次
            should_notify = True
            new_state = "logged_out"
        else:
            return False

        if not should_notify:
            return False

        # 会话级 event_key：服务器+账号+动作+状态轮次（用状态变更，不用时间）
        # 用「变更序号」：每次真正发生状态切换时记一次
        transition_no = int((session or {}).get("transition_count") or 0) + 1
        event_key = self._event_key(server_id, account, action, transition_no)

        try:
            await db.execute(
                """
                INSERT INTO login_logs (server_id, account, login_time, source_ip, os_type, raw_line)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                server_id,
                account,
                self._parse_time(login_time),
                source_ip,
                os_type,
                str(log),
            )
        except Exception as e:
            logger.error(f"保存登录日志失败：{e}")

        notified = False
        try:
            notified = await notifier.send_login_alert(
                server_name=server_name,
                server_host=server_host,
                account=account,
                login_time=str(login_time),
                source_ip=source_ip,
                action=action,
                action_text=action_text,
            )
        except Exception as e:
            logger.error(f"发送登录/退出告警失败：{e}")

        await self._upsert_account_session(
            server_id, account, new_state, login_time, source_ip, transition_no
        )

        await self._save_alert_log(
            source_type="server",
            source_id=server_id,
            source_name=server_name,
            level="medium",
            event_type=action,
            event_description=f"{action_text}：账户 {account} 来源 {source_ip or '未知'}",
            raw_data=str(log),
            notified=notified,
            event_key=event_key,
        )
        return notified

    async def _get_account_session(self, server_id: int, account: str) -> Optional[dict]:
        try:
            return await db.fetch_one(
                """
                SELECT server_id, account, state, last_event_time, last_source_ip, transition_count
                FROM server_account_sessions
                WHERE server_id = $1 AND account = $2
                """,
                server_id,
                account,
            )
        except Exception as e:
            logger.warning(f"读取会话状态失败：{e}")
            return None

    async def _upsert_account_session(
        self,
        server_id: int,
        account: str,
        state: str,
        event_time: Any,
        source_ip: str,
        transition_count: Optional[int] = None,
    ):
        try:
            existing = await self._get_account_session(server_id, account)
            count = transition_count
            if count is None:
                count = int((existing or {}).get("transition_count") or 0)
            await db.execute(
                """
                INSERT INTO server_account_sessions
                    (server_id, account, state, last_event_time, last_source_ip, transition_count, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (server_id, account) DO UPDATE SET
                    state = EXCLUDED.state,
                    last_event_time = EXCLUDED.last_event_time,
                    last_source_ip = EXCLUDED.last_source_ip,
                    transition_count = EXCLUDED.transition_count,
                    updated_at = NOW()
                """,
                server_id,
                account,
                state,
                self._parse_time(event_time),
                source_ip or "",
                count,
            )
        except Exception as e:
            logger.error(f"更新会话状态失败：{e}")

    async def process_high_risk_op(self, op: Dict[str, Any]) -> bool:
        await self._load_debounce()
        risk_level = op.get("risk_level", "medium")
        if risk_level not in ["high", "critical"]:
            return False

        server_id = int(op.get("server_id") or 0)
        operation_type = op.get("operation_type", "")
        account = op.get("account", "") or ""
        if isinstance(account, dict) or str(account).startswith("{") or "BinaryLength" in str(account):
            account = "未知账户"
        target_account = op.get("target_account", "") or ""
        group_name = (op.get("group_name", "") or "").lower()
        description = op.get("description", "") or ""
        server_name = op.get("server_name", "")
        server_host = op.get("server_host", "")
        os_type = op.get("os_type", "linux")
        operation_time = op.get("operation_time", "")

        # 丢弃旧客户端/错误解析产生的重复垃圾事件
        if operation_type == "user_create" and self._is_low_quality_user_create(op):
            logger.info(f"跳过低质量创建用户事件：account={account} desc={description[:60]}")
            return False

        # 普通 Users 组变更（创建用户连带）不推送
        if operation_type == "group_member_change":
            sensitive = (
                "administrators", "管理员", "remote desktop", "远程桌面",
                "backup", "备份", "power users", "domain admins", "enterprise admins",
            )
            if group_name and not any(s in group_name for s in sensitive):
                logger.info(f"跳过非特权组变更：{group_name}")
                return False
            # 无组名时若描述像普通组，也跳过
            if not group_name and ("Users" in description or "用户" in description) and "管理员" not in description:
                logger.info("跳过普通用户组变更")
                return False

        # 去重键：操作 + 目标用户 + 分钟（创建用户优先用新用户名）
        t = self._parse_time(operation_time) or datetime.now()
        minute_key = t.strftime("%Y%m%d%H%M")
        if operation_type == "user_create":
            focus = (
                target_account
                or self._extract_created_user(description)
                or account
                or "unknown"
            )
        elif operation_type in ("sudo_command", "user_change", "service_change"):
            # Linux SSH 拉取的事件没有目标账户，同一账户同一分钟内的不同命令要区分开
            digest = hashlib.sha1(description.encode("utf-8")).hexdigest()[:12]
            focus = f"{account}|{digest}"
        else:
            focus = target_account or account or description[:40]
        event_key = self._event_key(server_id, operation_type, focus, minute_key)
        if await self._already_alerted(event_key):
            logger.info(f"跳过重复高危事件：{operation_type} {focus}@{server_host}")
            return False
        if not self._should_alert_memory(event_key):
            return False

        # 同一分钟内「创建用户」额外防抖：避免旧+新 Agent 各推一条
        if operation_type == "user_create":
            minute_bucket = self._event_key(server_id, "user_create_bucket", minute_key)
            if not self._should_alert_memory(minute_bucket):
                logger.info(f"跳过同分钟重复创建用户推送：{focus}@{server_host}")
                return False

        try:
            await db.execute(
                """
                INSERT INTO high_risk_operations
                    (server_id, operation_type, risk_level, account, operation_time, description, hostname, os_type, raw_line, alerted)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, TRUE)
                """,
                server_id,
                operation_type,
                risk_level,
                account,
                self._parse_time(operation_time),
                description,
                server_host,
                os_type,
                str(op),
            )
        except Exception as e:
            logger.error(f"保存高危操作失败：{e}")

        notified = False
        try:
            notified = await notifier.send_high_risk_alert(
                server_name=server_name,
                server_host=server_host,
                operation_type=operation_type,
                risk_level=risk_level,
                account=account,
                operation_time=str(operation_time),
                description=description,
                data_source=op.get("collect_source") or "未知",
            )
        except Exception as e:
            logger.error(f"发送高危操作告警失败：{e}")

        await self._save_alert_log(
            source_type="server",
            source_id=server_id,
            source_name=server_name,
            level=risk_level,
            event_type=operation_type,
            event_description=description[:500],
            raw_data=str(op),
            notified=notified,
            event_key=event_key,
        )
        return notified

    async def process_network_alert(self, alert: Dict[str, Any]) -> bool:
        await self._load_debounce()
        device_id = int(alert.get("device_id") or 0)
        device_name = alert.get("device_name", "")
        severity = alert.get("severity", "medium")
        message = alert.get("message", "")
        alert_type = alert.get("alert_type", "")

        # 网络指标类告警：同一设备同类型在防抖窗口内只推一次
        event_key = self._event_key("network", device_id, alert_type, message[:80])
        if await self._already_alerted(event_key):
            return False
        if not self._should_alert_memory(f"network:{device_id}:{alert_type}"):
            return False

        notified = False
        try:
            notified = await notifier.send_network_alert(
                device_name=device_name,
                device_host=alert.get("device_host", ""),
                alert_type=alert_type,
                severity=severity,
                message=message,
            )
        except Exception as e:
            logger.error(f"发送网络告警失败：{e}")

        await self._save_alert_log(
            source_type="network",
            source_id=device_id,
            source_name=device_name,
            level=severity,
            event_type=alert_type,
            event_description=message,
            raw_data=str(alert),
            notified=notified,
            event_key=event_key,
        )
        return notified


rule_engine = RuleEngine()
