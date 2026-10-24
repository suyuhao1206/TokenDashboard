"""C1-2 / C1-3：排序白名单、去重幂等、去重方案迁移。"""

from __future__ import annotations

from token_dashboard.db import Store

from conftest import event


def test_order_by_falls_back_on_unknown_sort():
    """白名单外的输入必须回退到 ts，绝不能拼进 SQL（注入面）。"""
    assert Store._order_by("nonsense", "desc") == "ts DESC, id DESC"
    assert Store._order_by("ts; DROP TABLE usage_events", "asc") == "ts ASC, id ASC"
    assert Store._order_by(None, None) == "ts DESC, id DESC"


def test_order_by_puts_nulls_last_for_numeric():
    assert Store._order_by("cost", "asc") == "cost_usd IS NULL, cost_usd ASC, id DESC"
    assert Store._order_by("total", "desc").startswith("(input_tokens")


def test_upsert_is_idempotent(store):
    e = event(key="same")
    assert store.upsert_events([e]) == 1
    assert store.upsert_events([e]) == 1  # 同一 dedup_key 不会新增行
    assert store.totals()["events"] == 1
    # 再次 upsert 时以较新的 ts 为准（时间戳守卫），数量仍然只有 1
    newer = event(key="same", ts="2026-10-01T05:00:00.000Z", output_tokens=80)
    store.upsert_events([newer])
    assert store.totals()["events"] == 1
    assert store.totals()["output"] == 80


def test_ensure_dedup_version_clears_and_reingests(store):
    store.upsert_events([event(), event(key="k2", ts="2026-10-02T04:00:00.000Z",
                                        local_date="2026-10-02")])
    assert store.totals()["events"] == 2

    # 首次设版本：没有旧版本 → 视为需要清空重建
    assert store.ensure_dedup_version("claude_code", 2) == 2
    assert store.totals()["events"] == 0

    # 版本相同：空操作（不清数据）
    assert store.ensure_dedup_version("claude_code", 2) == 0
    assert store.totals()["events"] == 0

    # 重新采集后版本再涨：又清一次
    store.upsert_events([event()])
    assert store.ensure_dedup_version("claude_code", 3) == 1
    assert store.totals()["events"] == 0
