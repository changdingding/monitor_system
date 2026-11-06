"""上报时间。补发的旧样本不能把当前快照改回去。"""
from datetime import datetime, timedelta
from typing import Optional


def naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.replace(tzinfo=None)
    return value


def parse_collected_at(value) -> Optional[datetime]:
    """解析采集时间。太旧（超过 24 小时）返回 None，调用方应丢弃。空值视为现在。"""
    now = datetime.now()
    if value is None or value == "":
        return now
    text = str(value).strip().replace("Z", "")
    if "T" in text:
        text = text.replace("T", " ")
    if "+" in text[10:]:
        text = text[:text.rfind("+")]
    parsed = None
    for fmt, size in (("%Y-%m-%d %H:%M:%S.%f", 26), ("%Y-%m-%d %H:%M:%S", 19)):
        try:
            parsed = datetime.strptime(text[:size], fmt)
            break
        except Exception:
            continue
    if parsed is None:
        return now
    if parsed > now + timedelta(minutes=5):
        return now
    if parsed < now - timedelta(hours=24):
        return None
    return parsed


def is_fresh(when: datetime, seconds: int = 180) -> bool:
    return datetime.now() - when <= timedelta(seconds=seconds)
