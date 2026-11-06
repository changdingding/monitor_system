"""
网络设备监控模块（总部平台直接轮询的设备：未归属站点，或所属站点采集方式为 central）

SNMP 读取复用门店采集器 probe/ 中的实现（无第三方依赖、支持多厂商 OID），
同步 socket 调用放到线程池执行，避免阻塞事件循环。
目前仅支持 SNMP v1/v2c（团体字），与前端可配置项一致。
"""
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROBE_DIR = Path(__file__).resolve().parents[2] / "probe"
if str(_PROBE_DIR) not in sys.path:
    sys.path.append(str(_PROBE_DIR))  # probe.py 内部使用 `from snmp_client import ...`


def _load_probe_lib():
    spec = importlib.util.spec_from_file_location("_probe_lib", _PROBE_DIR / "probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_probe = _load_probe_lib()


class DeviceUnreachable(Exception):
    """SNMP 无响应（设备离线、团体字错误或端口不通）"""


class NetworkMonitor:
    """网络设备监控器"""

    @staticmethod
    def _collect_sync(host: str, community: str, port: int) -> Dict[str, Any]:
        sys_object = _probe.snmp_get(host, community, _probe.SYS_OBJECT, port)
        sys_descr = _probe.snmp_get(host, community, _probe.SYS_DESCR, port)
        if sys_object is None and sys_descr is None:
            raise DeviceUnreachable(f"{host}:{port} SNMP 无响应")
        vendor = _probe.detect_vendor(sys_object, "auto")
        return {
            "vendor": vendor,
            "cpu": _probe.read_cpu(host, community, port, vendor),
            "memory": _probe.read_memory(host, community, port, vendor),
        }

    async def collect_device_metrics(self, host: str, snmp_config: Dict[str, Any]) -> Dict[str, Any]:
        """采集设备 CPU / 内存（百分比）。设备无响应时抛出 DeviceUnreachable。"""
        version = (snmp_config.get("version") or "v2c").lower()
        if version == "v3":
            logger.warning(f"{host} 配置为 SNMPv3，当前仅支持 v1/v2c，按 v2c 团体字尝试")
        return await asyncio.to_thread(
            self._collect_sync,
            host,
            snmp_config.get("community") or "public",
            int(snmp_config.get("port") or 161),
        )

    async def check_device_health(
        self,
        device_id: int,
        device_name: str,
        host: str,
        snmp_config: Dict[str, Any],
        thresholds: Optional[Dict[str, Any]] = None,
        metrics_out: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        检查设备健康状态，返回资源告警列表；metrics_out 不为空时写入采集到的 vendor / cpu / memory。
        设备不可达时抛出 DeviceUnreachable，由调用方标记离线。
        """
        thresholds = thresholds or {}
        metrics = await self.collect_device_metrics(host, snmp_config)
        if metrics_out is not None:
            metrics_out.update(metrics)
        alerts: List[Dict[str, Any]] = []
        checks = (
            ("cpu", "high_cpu", "CPU 使用率过高", thresholds.get("cpu", 90)),
            ("memory", "high_memory", "内存使用率过高", thresholds.get("memory", 90)),
        )
        for key, alert_type, label, limit in checks:
            value = metrics.get(key)
            if value is not None and value >= float(limit):
                alerts.append({
                    "device_id": device_id,
                    "device_name": device_name,
                    "device_host": host,
                    "alert_type": alert_type,
                    "severity": "high",
                    "message": f"{label}：{value:.1f}%（阈值 {limit}%）",
                })
        return alerts


# 全局监控器实例
monitor = NetworkMonitor()
