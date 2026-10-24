"""时间窗口计算（纯函数，便于单元测试）。

A1 的「较前一周期」必须与卡片主值同源：KPI 卡的主值来自 /api/summary 的
`range`，环比也应来自同一个接口。把窗口计算放在后端有个额外好处——边界
（月末、跨年、空 until）只用 Python date 算一次，不用在 JS 里和 Invalid Date
搏斗，也就能写测试锁住。
"""

from __future__ import annotations

from datetime import date, timedelta


def previous_period(
    since: str | None, until: str | None, today: date
) -> tuple[str, str] | None:
    """返回「上一个等长周期」的 (开始日期, 结束日期)。

    规则：
    - 当前窗口 = [since, until]，until 为空表示「到今天」，不再用数据里的
      max_date（周末/隔天没数据时两者不同，用 max_date 会让窗口整体偏移）；
    - 跨度含首尾（10-01 ~ 10-07 是 7 天），上一周期紧邻当前窗口之前，
      即 09-24 ~ 09-30，两段不重叠、不多算一天；
    - since 为空时没有可比较的窗口，返回 None。
    """
    if not since:
        return None
    try:
        start = date.fromisoformat(since)
        end = date.fromisoformat(until) if until else today
    except (TypeError, ValueError):
        return None
    if end < start:
        start, end = end, start
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return prev_start.isoformat(), prev_end.isoformat()
