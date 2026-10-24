"""HTTP 层：/api/summary 与 model 筛选同源（A2）、环比窗口（A1）、CSV 导出（A6/B11）。"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from token_dashboard.config import load_config
from token_dashboard.server import create_app

from conftest import event


def make_cfg(tmp_path, **overrides):
    cfg = load_config(str(tmp_path / "config.yaml"))
    cfg["storage"]["db_path"] = str(tmp_path / "usage.db")
    cfg["scanner"]["interval_minutes"] = 0          # 测试里不要后台扫描线程
    cfg["collectors"] = {}                          # 不碰任何真实日志
    cfg["ui"]["timezone"] = "Asia/Shanghai"
    cfg["_config_path"] = str(tmp_path / "config.yaml")
    cfg.update(overrides)
    return cfg


@pytest.fixture()
def client(tmp_path):
    cfg = make_cfg(tmp_path)
    app = create_app(cfg)
    with TestClient(app) as c:
        store = app.state.store if hasattr(app.state, "store") else None
        yield c, cfg, store


def seed(tmp_path, events):
    from token_dashboard.db import Store

    s = Store(tmp_path / "usage.db")
    s.upsert_events(events)
    s.close()


def test_summary_respects_model_filter(tmp_path):
    """A2：选了模型后 KPI / 本月 / 所选范围都只统计该模型。"""
    cfg = make_cfg(tmp_path)
    seed(tmp_path, [
        event(model="claude-sonnet-4", input_tokens=100, output_tokens=50, cost_usd=0.01),
        event(model="gpt-5", key="g1", input_tokens=900, output_tokens=50, cost_usd=0.02),
    ])
    with TestClient(create_app(cfg)) as c:
        all_models = c.get("/api/summary").json()
        one = c.get("/api/summary", params={"model": "gpt-5"}).json()

    assert all_models["all"]["events"] == 2
    assert one["all"]["events"] == 1
    assert one["all"]["tokens"] == 950
    # by_harness 仍是全量语义（分布不受 model 筛选影响）
    assert sum(h["events"] for h in one["by_harness"]) == 2


def test_summary_previous_period_is_equal_length_and_non_overlapping(tmp_path):
    """A1：上周窗口紧邻当前窗口之前，且与当前窗口完全同长。"""
    cfg = make_cfg(tmp_path)
    seed(tmp_path, [
        event(ts="2026-10-20T04:00:00.000Z", local_date="2026-10-20",
              input_tokens=0, output_tokens=10),
        event(ts="2026-10-21T04:00:00.000Z", local_date="2026-10-21",
              input_tokens=0, output_tokens=20, key="k2"),
        event(ts="2026-10-14T04:00:00.000Z", local_date="2026-10-14",
              input_tokens=0, output_tokens=40, key="k3"),
    ])
    with TestClient(create_app(cfg)) as c:
        data = c.get("/api/summary", params={"since": "2026-10-20", "until": "2026-10-21"}).json()

    assert data["range"]["events"] == 2
    assert data["range"]["tokens"] == 30
    assert data["prev_window"] == {"since": "2026-10-18", "until": "2026-10-19"}
    assert data["prev_range"]["events"] == 0
    assert data["prev_range"]["tokens"] == 0


def test_summary_preset_range_has_previous_window(tmp_path):
    """A1 问题 1：预设范围（until 为空）以前恒为「-」，现在必须算出环比窗口。"""
    cfg = make_cfg(tmp_path)
    with TestClient(create_app(cfg)) as c:
        data = c.get("/api/summary", params={"since": "2026-10-18"}).json()
    assert data["prev_window"] is not None
    assert data["prev_range"] is not None
    assert data["prev_window"]["until"] == "2026-10-17"


def test_csv_export_headers_and_local_time(tmp_path):
    cfg = make_cfg(tmp_path)
    seed(tmp_path, [event(project="=1+1", cost_usd=0.001)])
    with TestClient(create_app(cfg)) as c:
        r = c.get("/api/requests", params={"format": "csv", "since": "2026-10-01",
                                          "until": "2026-10-07"})

    assert r.status_code == 200
    assert r.headers["x-export-limit"] == "200000"
    assert "X-Export-Truncated" not in r.headers
    # B11：文件名带上当前筛选范围
    assert "20261001-20261007" in r.headers["content-disposition"]

    text = r.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][1] == "本地时间"
    assert rows[0].index("本地时间") == 1
    body = rows[1]
    assert body[7] == "'=1+1", "公式注入必须被转义"
    # 本地时间列 = UTC + 8（事件 04:00Z → 12:00）
    assert body[1] == "2026-10-01 12:00:00"


def test_export_truncation_header(tmp_path, monkeypatch):
    import token_dashboard.db as db_mod

    cfg = make_cfg(tmp_path)
    seed(tmp_path, [event(key=f"k{i}", ts=f"2026-10-{i:02d}T04:00:00.000Z",
                          local_date=f"2026-10-{i:02d}") for i in range(1, 5)])
    monkeypatch.setattr(db_mod, "EXPORT_LIMIT", 2)
    with TestClient(create_app(cfg)) as c:
        r = c.get("/api/requests", params={"format": "csv"})
    assert r.headers.get("x-export-truncated") == "1"


def test_budget_can_be_set_without_restart(tmp_path):
    """B3 进阶：POST /api/config/budget 写回 config.yaml 并立即在 /api/summary 生效。"""
    cfg = make_cfg(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "# 用户配置\nserver:\n  port: 8787\n\nbudget:\n  monthly_usd: null\n",
        encoding="utf-8",
    )
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/summary").json()["budget"]["monthly_usd"] is None
        r = c.post("/api/config/budget", json={"monthly_usd": 100})
        assert r.status_code == 200
        assert r.json()["monthly_usd"] == 100
        assert c.get("/api/summary").json()["budget"]["monthly_usd"] == 100

    written = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "monthly_usd: 100" in written
    assert "# 用户配置" in written, "行级编辑必须保留用户注释"

    # 传 null 关闭
    with TestClient(create_app(cfg)) as c:
        assert c.post("/api/config/budget", json={"monthly_usd": None}).json()["monthly_usd"] is None
    assert "monthly_usd: null" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_budget_rejects_non_numeric(tmp_path):
    cfg = make_cfg(tmp_path)
    with TestClient(create_app(cfg)) as c:
        r = c.post("/api/config/budget", json={"monthly_usd": "abc"})
    assert r.status_code == 400


def test_daily_session_filter(tmp_path):
    """B5：/api/daily?session_id= 只返回该会话的按天数据。"""
    cfg = make_cfg(tmp_path)
    seed(tmp_path, [
        event(session_id="a", key="a1", output_tokens=10),
        event(session_id="b", key="b1", output_tokens=20,
              ts="2026-10-02T04:00:00.000Z", local_date="2026-10-02"),
    ])
    with TestClient(create_app(cfg)) as c:
        rows = c.get("/api/daily", params={"dim": "total", "session_id": "a"}).json()
    assert len(rows) == 1
    assert rows[0]["tokens"] == 110


def test_summary_month_is_global_regardless_of_filters(tmp_path):
    """B3：month（预算口径）永远是全量月度成本，不随 harness / model 筛选缩小。

    预算是对整张账单设置的：选一个小模型就把 month 缩小，超支告警会凭空消失。
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    cfg = make_cfg(tmp_path)
    # 用面板时区的「今天」造数，避免跨月边界（UTC 与 Asia/Shanghai 可能差一天）时翻车
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    ts = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    ld = now.strftime("%Y-%m-%d")
    seed(tmp_path, [
        event(ts=ts, local_date=ld, model="gpt-5", key="m1", cost_usd=0.02),
        event(ts=ts, local_date=ld, model="claude-sonnet-4", key="m2", cost_usd=0.01),
    ])
    with TestClient(create_app(cfg)) as c:
        data = c.get("/api/summary", params={"model": "gpt-5"}).json()

    # 模型筛选对 all / today / last7d 生效……
    assert data["all"]["events"] == 1
    # ……但 month 是全局预算口径，两个模型都要算进来
    assert data["month"]["events"] == 2
    assert data["month"]["cost"] == pytest.approx(0.03)
    # B8：环比窗口（昨日 / 前 7 天）由后端按同一时区给出
    assert data["yesterday"]["events"] == 0
    assert data["prev7d"]["events"] == 0


def test_unpriced_models_listed(tmp_path):
    """B2：未定价模型清单来自后端，前端据此生成价格模板。"""
    cfg = make_cfg(tmp_path)
    seed(tmp_path, [
        event(model="mystery-model", cost_usd=None),
        event(model="gpt-5", key="p", cost_usd=0.01),
    ])
    with TestClient(create_app(cfg)) as c:
        data = c.get("/api/summary").json()
    names = [r["model"] for r in data["unpriced_models"]]
    assert names == ["mystery-model"]


def test_hourly_buckets_are_24(tmp_path):
    cfg = make_cfg(tmp_path)
    seed(tmp_path, [event(ts=datetime(2026, 10, 1, 4, tzinfo=ZoneInfo("UTC"))
                         .strftime("%Y-%m-%dT%H:%M:%S.000Z"))])
    with TestClient(create_app(cfg)) as c:
        rows = c.get("/api/hourly").json()
    assert len(rows) == 24
    assert rows[12]["events"] == 1  # 04:00Z → Asia/Shanghai 12:00
