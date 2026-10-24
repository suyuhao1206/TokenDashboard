"""pytest 公共夹具：临时 SQLite 库 + 构造事件的辅助函数。

所有用例都不碰真实日志/真实库，全部落在 pytest 的 tmp_path 里。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from token_dashboard.db import Store  # noqa: E402
from token_dashboard.models import UsageEvent  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "usage.db")
    yield s
    s.close()


def event(
    ts: str = "2026-10-01T04:00:00.000Z",
    local_date: str | None = "2026-10-01",
    *,
    source: str = "claude_code",
    harness: str = "claude-code",
    model: str | None = "claude-sonnet-4",
    raw_model: str | None = None,
    channel: str | None = None,
    input_tokens: int = 100,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 50,
    reasoning_tokens: int = 0,
    cost_usd: float | None = None,
    session_id: str | None = "sess-1",
    project: str | None = "/repo",
    key: str | None = None,
) -> UsageEvent:
    """构造一条用量事件；dedup_key 默认唯一（同一天多条事件要用不同 key）。"""
    key = key or f"{source}|{ts}|{model}|{session_id}|{input_tokens}|{output_tokens}"
    return UsageEvent(
        source=source,
        harness=harness,
        ts=ts,
        local_date=local_date,
        model=model,
        raw_model=raw_model or model,
        channel=channel,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost_usd,
        session_id=session_id,
        project=project,
        dedup_key=key,
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
