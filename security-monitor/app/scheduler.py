"""
定时任务调度器
"""
import asyncio
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.core.database import db
from app.core.crypto import decrypt
from app.collector.log_collector import collector
from app.collector.network_monitor import monitor, DeviceUnreachable
from app.engine.rule_engine import rule_engine
from app.services.health import get_thresholds, purge_old_metrics, scan_offline

# 单轮采集的最大并发数（SSH / SNMP）
MAX_CONCURRENCY = 10

# 默认间隔（秒），可在「系统设置」中修改，保存后立即生效
DEFAULT_COLLECTOR_INTERVAL = 120
DEFAULT_MONITOR_INTERVAL = 300
MIN_INTERVAL = 10

logger = logging.getLogger(__name__)


class Scheduler:
    """任务调度器"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    async def _set_server_status(self, server_id: int, status: str):
        if status == "online":
            await db.execute(
                """
                UPDATE servers
                SET status = $1, last_heartbeat = NOW(), updated_at = NOW()
                WHERE id = $2
                """,
                status,
                server_id,
            )
        else:
            await db.execute(
                """
                UPDATE servers
                SET status = $1, updated_at = NOW()
                WHERE id = $2
                """,
                status,
                server_id,
            )

    async def _collect_server(self, server: dict):
        """采集单台服务器（Agent 心跳判断 / SSH 拉取 → 规则引擎）"""
        server_id = server["id"]
        name = server["name"]
        host = server["ip"]
        os_type = (server["os_type"] or "linux").lower()
        port = int(server["port"] or 22)
        username = server["username"] or ("Administrator" if os_type == "windows" else "root")
        password = decrypt(server.get("password"))
        collect_mode = (server.get("collect_mode") or "agent").lower()

        # Agent 模式：有近期心跳则跳过 SSH 拉取（由客户端主动上报）
        if collect_mode in ("agent", "both"):
            seen = server.get("agent_last_seen")
            agent_alive = False
            if seen:
                try:
                    from datetime import datetime as dt
                    delta = dt.utcnow() - seen.replace(tzinfo=None)
                    agent_alive = delta.total_seconds() < 300
                except Exception:
                    agent_alive = False
            if collect_mode == "agent" and agent_alive:
                logger.info(f"服务器 {name} 使用 Agent 上报中，跳过 SSH 拉取")
                return
            if collect_mode == "agent" and not agent_alive:
                logger.warning(f"服务器 {name} 配置为 Agent 模式但无心跳，标记离线（请安装客户端）")
                await self._set_server_status(server_id, "offline")
                return

        if collect_mode == "agent":
            return

        if not password:
            logger.warning(f"服务器 {name} ({host}) 未配置密码，标记为离线")
            await self._set_server_status(server_id, "offline")
            return

        logger.info(f"SSH 采集服务器：{name} ({host}:{port})")
        logs = await collector.collect_logs(
            server_id=server_id,
            host=host,
            os_type=os_type,
            port=port,
            username=username,
            password=password,
        )

        if logs.get("success"):
            await self._set_server_status(server_id, "online")
        else:
            await self._set_server_status(server_id, "error" if logs.get("error") else "offline")
            logger.error(f"服务器 {name} 采集失败：{logs.get('error')}")
            return

        login_logs = logs.get("login_logs", [])
        # 按时间正序处理，保证登录→退出状态机正确
        login_logs = sorted(login_logs, key=lambda x: str(x.get("login_time") or ""))
        for log in login_logs:
            log["server_id"] = server_id
            log["server_name"] = name
            log["server_host"] = host
            log["os_type"] = os_type
            await rule_engine.process_login_log(log)

        for op in logs.get("high_risk_ops", []):
            op["server_id"] = server_id
            op["server_name"] = name
            op["server_host"] = host
            op["os_type"] = os_type
            op["collect_source"] = op.get("collect_source") or "ssh"
            await rule_engine.process_high_risk_op(op)

    async def fetch_and_process_logs(self):
        """采集并处理服务器日志（高危操作 → 飞书告警）"""
        logger.info("开始采集服务器日志...")
        try:
            server_list = await db.fetch_all(
                """
                SELECT id, name, ip, os_type, port, username, password,
                       collect_mode, agent_last_seen, agent_token
                FROM servers
                ORDER BY id
                """
            )
            if not server_list:
                logger.info("暂无服务器，跳过采集")
                return

            sem = asyncio.Semaphore(MAX_CONCURRENCY)

            async def _guarded(server):
                async with sem:
                    try:
                        await self._collect_server(server)
                    except Exception as e:
                        logger.error(f"服务器 {server.get('name')} 采集异常：{e}")

            await asyncio.gather(*(_guarded(dict(srv)) for srv in server_list))

            logger.info(f"日志采集完成，共处理 {len(server_list)} 台服务器")
        except Exception as e:
            logger.error(f"日志采集失败：{e}")

    async def _check_device(self, device: dict, thresholds: dict):
        """巡检单台总部网络设备：不可达 → offline；超阈值 → 告警并标记 error"""
        device_id = device["id"]
        name = device["name"]
        host = device["ip"]
        snmp_config = {
            "version": device.get("snmp_version") or "v2c",
            "community": decrypt(device.get("snmp_community")) or "public",
            "port": int(device.get("snmp_port") or 161),
        }
        try:
            alerts = await monitor.check_device_health(
                device_id=device_id,
                device_name=name,
                host=host,
                snmp_config=snmp_config,
                thresholds=thresholds,
            )
        except DeviceUnreachable as e:
            logger.warning(f"设备 {name} 不可达：{e}")
            status = "offline"
        except Exception as e:
            logger.error(f"设备 {name} 检查失败：{e}")
            status = "error"
        else:
            status = "online"
            for alert in alerts:
                await rule_engine.process_network_alert(alert)
                if alert.get("severity") in ("critical", "high"):
                    status = "error"
        await db.execute(
            "UPDATE network_devices SET status=$1, updated_at=NOW() WHERE id=$2",
            status,
            device_id,
        )

    async def check_network_devices(self):
        """检查网络设备状态"""
        logger.info("开始检查网络设备...")
        try:
            device_list = await db.fetch_all(
                """
                SELECT id, name, ip, snmp_version, snmp_community, snmp_port
                FROM network_devices
                WHERE site_id IS NULL
                ORDER BY id
                """
            )
            thresholds = await get_thresholds()
            sem = asyncio.Semaphore(MAX_CONCURRENCY)

            async def _guarded(device):
                async with sem:
                    try:
                        await self._check_device(device, thresholds)
                    except Exception as e:
                        logger.error(f"设备 {device.get('name')} 巡检异常：{e}")

            await asyncio.gather(*(_guarded(dict(dev)) for dev in device_list))
            logger.info(f"网络设备检查完成，共检查 {len(device_list)} 台设备")
        except Exception as e:
            logger.error(f"网络设备检查失败：{e}")

    def start(self):
        """启动调度器"""
        self.scheduler.add_job(
            self.fetch_and_process_logs,
            trigger=IntervalTrigger(seconds=DEFAULT_COLLECTOR_INTERVAL),
            id="log_collection",
            name="日志采集",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.check_network_devices,
            trigger=IntervalTrigger(seconds=DEFAULT_MONITOR_INTERVAL),
            id="network_monitor",
            name="网络设备监控",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.fetch_and_process_logs,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=8)),
            id="log_collection_startup",
            name="启动后首次日志采集",
            replace_existing=True,
        )
        self.scheduler.add_job(
            scan_offline,
            trigger=IntervalTrigger(minutes=1),
            id="health_offline_scan",
            name="健康离线扫描",
            replace_existing=True,
        )
        self.scheduler.add_job(
            purge_old_metrics,
            trigger=IntervalTrigger(hours=6),
            id="metrics_retention",
            name="指标过期清理",
            replace_existing=True,
        )
        from app.core.audit import purge_old_audit_logs
        self.scheduler.add_job(
            purge_old_audit_logs,
            trigger=IntervalTrigger(hours=24),
            id="audit_retention",
            name="审计日志过期清理",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("调度器已启动")

    def apply_intervals(self, collector_seconds=None, monitor_seconds=None):
        """按系统设置调整日志采集 / 网络巡检间隔（运行中立即生效）"""
        changes = (
            ("log_collection", collector_seconds),
            ("network_monitor", monitor_seconds),
        )
        for job_id, seconds in changes:
            if not seconds:
                continue
            seconds = max(MIN_INTERVAL, int(seconds))
            job = self.scheduler.get_job(job_id)
            if job is None:
                continue
            current = getattr(job.trigger, "interval", None)
            if current is not None and int(current.total_seconds()) == seconds:
                continue
            self.scheduler.reschedule_job(job_id, trigger=IntervalTrigger(seconds=seconds))
            logger.info(f"任务 {job.name} 间隔调整为 {seconds} 秒")
            if job_id == "log_collection":
                collector.set_interval(seconds)
                logger.info(f"SSH 日志回看窗口调整为 {collector.lookback_minutes} 分钟")

    async def load_intervals(self):
        """启动时从 system_settings 读取采集间隔"""
        try:
            row = await db.fetch_one(
                "SELECT collector_interval_seconds, monitor_interval_seconds FROM system_settings ORDER BY id LIMIT 1"
            )
        except Exception as e:
            logger.warning(f"读取采集间隔失败，使用默认值：{e}")
            return
        if row:
            self.apply_intervals(row.get("collector_interval_seconds"), row.get("monitor_interval_seconds"))

    def stop(self):
        self.scheduler.shutdown(wait=False)
        logger.info("调度器已停止")


scheduler = Scheduler()
