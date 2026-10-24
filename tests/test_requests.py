"""C1-4：请求表筛选 / 分页 / CSV 口径（合计不含推理双算）。"""

from __future__ import annotations

import csv
import io

import pytest

from conftest import event


def test_totals_and_filters(store):
    store.upsert_events([
        event(model="claude-sonnet-4", cost_usd=0.01),
        event(model="gpt-5", harness="codex", source="codex",
              ts="2026-10-02T04:00:00.000Z", local_date="2026-10-02",
              input_tokens=200, output_tokens=100, cost_usd=0.02),
        event(model="gpt-5", harness="codex", source="codex",
              ts="2026-10-03T04:00:00.000Z", local_date="2026-10-03",
              input_tokens=300, output_tokens=100, cost_usd=None),
    ])
    assert store.totals()["events"] == 3
    assert store.totals(model="gpt-5")["events"] == 2
    assert store.totals(harness="codex", since="2026-10-03")["input"] == 300
    # 有 cost=None 的行时 unpriced 计数正确
    assert store.totals(model="gpt-5")["unpriced"] == 1


def test_recent_requests_pagination_and_sort(store):
    rows = [
        event(key=f"k{i}", ts=f"2026-10-0{i}T04:00:00.000Z", local_date=f"2026-10-0{i}",
              input_tokens=i * 100, cost_usd=None if i == 3 else 0.001)
        for i in range(1, 6)
    ]
    store.upsert_events(rows)
    page1 = store.recent_requests(page=1, size=2)
    assert page1["total"] == 5 and page1["size"] == 2
    assert len(page1["rows"]) == 2
    # 数值列按 cost 升序：NULL 必须排最后（不能因为 ASC 就跑到最前）
    by_cost = store.recent_requests(sort="cost", order="asc", size=10)["rows"]
    assert by_cost[-1]["cost_usd"] is None
    # 升序时时间最早在前
    by_ts = store.recent_requests(sort="ts", order="asc", size=10)["rows"]
    assert by_ts[0]["ts"].startswith("2026-10-01")


def test_session_filter(store):
    store.upsert_events([
        event(session_id="a", key="a1"),
        event(session_id="b", key="b1", ts="2026-10-02T04:00:00.000Z",
              local_date="2026-10-02"),
    ])
    assert store.recent_requests(session_id="a")["total"] == 1
    daily = store.daily_report(session_id="b")
    assert len(daily) == 1 and daily[0]["date"] == "2026-10-02"


def test_export_total_does_not_double_count_reasoning(store):
    """构造 output=100 / reasoning=40 的事件：合计必须是 input+cache+100，不是 +140。"""
    store.upsert_events([event(input_tokens=1000, cache_read_tokens=0,
                              cache_write_tokens=0, output_tokens=100,
                              reasoning_tokens=40, cost_usd=0.5)])
    rows, truncated = store.export_requests()
    assert not truncated
    assert len(rows) == 1
    r = rows[0]
    total = (
        (r["input_tokens"] or 0) + (r["cache_read_tokens"] or 0)
        + (r["cache_write_tokens"] or 0) + (r["output_tokens"] or 0)
    )
    assert total == 1100
    assert total != 1140


def test_export_limit_marks_truncated(store, monkeypatch):
    import token_dashboard.db as db_mod

    store.upsert_events([event(key=f"k{i}", ts=f"2026-10-{i:02d}T04:00:00.000Z",
                               local_date=f"2026-10-{i:02d}") for i in range(1, 6)])
    monkeypatch.setattr(db_mod, "EXPORT_LIMIT", 3)
    rows, truncated = store.export_requests()
    assert len(rows) == 3 and truncated


def test_local_date_null_rows_are_excluded_everywhere(store):
    """A3：没有 local_date 的行不能出现在 KPI / 请求表 / 活跃时段 / 会话里。"""
    store.upsert_events([
        event(key="ok"),
        event(key="bad", local_date=None, ts="not-a-timestamp"),
    ])
    assert store.totals()["events"] == 1
    assert store.recent_requests()["total"] == 1
    assert store.export_requests()[0].__len__() == 1
    assert sum(r["events"] for r in store.hourly()) == 1
    assert store.summary()["events"] == 1
    assert sum(r["events"] for r in store.by_harness()) == 1
    assert len(store.sessions()) == 1
    assert len(store.daily_report()) == 1


def test_csv_text_guards_formula_injection():
    """A6：以 = + - @ 开头的文本单元格要前置单引号；数字不受影响。"""
    from token_dashboard.server import _csv_text

    assert _csv_text("=1+1") == "'=1+1"
    assert _csv_text("+SUM(A1)") == "'+SUM(A1)"
    assert _csv_text("-2+3") == "'-2+3"
    assert _csv_text("@cmd") == "'@cmd"
    assert _csv_text("normal-project") == "normal-project"
    assert _csv_text(None) == ""
    assert _csv_text(123) == "123"


def test_csv_writer_output_shape(store):
    """整条导出链路（不含 FastAPI）：表头列数 = 数据列数，合计列正确。"""
    from token_dashboard.server import _csv_text

    store.upsert_events([event(project="=1+1", model="gpt-5", cost_usd=0.001)])
    rows, _ = store.export_requests()
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["时间(UTC)", "本地时间", "日期", "Harness", "模型", "原始模型", "渠道", "项目",
              "输入", "缓存读", "缓存写", "输出(含推理)", "其中推理", "合计", "成本USD", "会话"]
    w.writerow(header)
    r = rows[0]
    total = ((r["input_tokens"] or 0) + (r["cache_read_tokens"] or 0)
             + (r["cache_write_tokens"] or 0) + (r["output_tokens"] or 0))
    w.writerow([_csv_text(r["ts"]), "2026-10-01 12:00:00", _csv_text(r["local_date"]),
                _csv_text(r["harness"]), _csv_text(r["model"]), _csv_text(r["raw_model"]),
                _csv_text(r["channel"]), _csv_text(r["project"]),
                r["input_tokens"], r["cache_read_tokens"], r["cache_write_tokens"],
                r["output_tokens"], r["reasoning_tokens"], total, "0.001000",
                _csv_text(r["session_id"])])
    parsed = list(csv.reader(io.StringIO(buf.getvalue())))
    assert len(parsed[0]) == len(parsed[1]) == 16
    assert parsed[1][7] == "'=1+1"
    assert int(parsed[1][13]) == 150


@pytest.mark.parametrize("bad", ["2026-10-01; DROP TABLE usage_events", "1 OR 1=1"])
def test_sort_param_is_not_injectable(store, bad):
    store.upsert_events([event()])
    data = store.recent_requests(sort=bad, order=bad)
    assert data["total"] == 1
