"""时间与模型名归一化工具。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def parse_iso(ts: str | None) -> datetime | None:
    """解析 ISO8601 时间戳；无时区信息按 UTC 处理。"""
    if not ts:
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def to_utc_iso(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local_date(dt: datetime | None, tz: ZoneInfo) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(tz).date().isoformat()


def norm_model(raw: str | None) -> str | None:
    """模型名粗归一：去常见前缀（保留原始名另存）。"""
    if not raw:
        return None
    name = raw.strip()
    for prefix in ("anthropic/", "openai/", "google/", "models/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
    return name


def fmt_int(n: int | float | None) -> str:
    if n is None:
        return "-"
    return f"{int(n):,}"
