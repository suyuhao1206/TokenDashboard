"""C1-5：A1 环比窗口的纯函数（预设 until 为空、自定义跨月、跨年）。"""

from __future__ import annotations

from datetime import date

import pytest

from token_dashboard.periods import previous_period


@pytest.mark.parametrize(
    ("since", "until", "today", "expected"),
    [
        # 预设范围：until 为空 = 到今天（不是数据的 max_date）
        ("2026-10-18", None, date(2026, 10, 24), ("2026-10-11", "2026-10-17")),
        # 自定义 7 天：紧邻上一段，不重叠也不多算一天
        ("2026-10-01", "2026-10-07", date(2026, 10, 24), ("2026-09-24", "2026-09-30")),
        # 单日（热力图点格子）
        ("2026-10-07", "2026-10-07", date(2026, 10, 24), ("2026-10-06", "2026-10-06")),
        # 跨月
        ("2026-03-01", "2026-03-31", date(2026, 10, 24), ("2026-01-29", "2026-02-28")),
        # 跨年
        ("2026-01-01", "2026-01-07", date(2026, 10, 24), ("2025-12-25", "2025-12-31")),
        # 闰年 2 月
        ("2024-03-01", "2024-03-02", date(2024, 3, 10), ("2024-02-28", "2024-02-29")),
    ],
)
def test_previous_period(since, until, today, expected):
    assert previous_period(since, until, today) == expected


def test_previous_period_without_since():
    assert previous_period(None, None, date(2026, 10, 24)) is None


def test_previous_period_swaps_reversed_dates():
    """起止填反时窗口仍然合法（前后端都做过互换，这里再兜一层）。"""
    assert previous_period("2026-10-07", "2026-10-01", date(2026, 10, 24)) == (
        "2026-09-24", "2026-09-30")


def test_previous_period_periods_do_not_overlap():
    since, until = "2026-10-01", "2026-10-07"
    prev = previous_period(since, until, date(2026, 10, 24))
    assert prev is not None
    assert prev[1] < since, "上一周期必须严格早于当前窗口"
