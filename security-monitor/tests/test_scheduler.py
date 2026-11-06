"""调度间隔与 SSH 回看窗口"""
from app.collector.log_collector import LogCollector
import app.scheduler as sched_mod


def test_lookback_covers_interval():
    c = LogCollector()
    for os_type in ("linux", "windows"):
        for cmd in (c.get_login_command(os_type), c.get_high_risk_command(os_type)):
            c.set_interval(300)  # 5 分钟采集 → 回看 10 分钟
            out = c._with_lookback(cmd)
            assert "'5 min ago'" not in out and "AddMinutes(-5)" not in out
    c.set_interval(60)
    assert c.lookback_minutes == 5
    c.set_interval(301)
    assert c.lookback_minutes == 11


def test_apply_intervals_reschedules(client):
    async def run():
        s = sched_mod.Scheduler()
        type(s).start(s)  # 全局实例的 start 在测试中被替换，这里用类方法
        try:
            s.apply_intervals(240, 45)
            got = {j: int(s.scheduler.get_job(j).trigger.interval.total_seconds())
                   for j in ("log_collection", "network_monitor")}
            s.apply_intervals(1, None)  # 低于下限会被抬到 MIN_INTERVAL
            low = int(s.scheduler.get_job("log_collection").trigger.interval.total_seconds())
            return got, low
        finally:
            s.scheduler.shutdown(wait=False)
    got, low = client.portal.call(run)
    assert got == {"log_collection": 240, "network_monitor": 45}
    assert low == sched_mod.MIN_INTERVAL
