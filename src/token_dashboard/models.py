"""标准用量事件模型。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def make_dedup_key(*parts: str | None) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()


@dataclass
class UsageEvent:
    source: str
    harness: str
    ts: str                      # UTC ISO8601
    local_date: str | None       # 面板时区下的日期（YYYY-MM-DD）
    model: str | None = None
    raw_model: str | None = None
    channel: str | None = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    session_id: str | None = None
    request_id: str | None = None
    project: str | None = None
    dedup_key: str = ""
    raw_ref: str | None = None

    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )

    def to_row(self) -> tuple:
        return (
            self.source, self.harness, self.channel, self.model, self.raw_model,
            self.ts, self.local_date,
            self.input_tokens, self.cache_read_tokens, self.cache_write_tokens,
            self.output_tokens, self.reasoning_tokens, self.cost_usd,
            self.session_id, self.request_id, self.project,
            self.dedup_key, self.raw_ref,
        )


COLUMNS = (
    "source, harness, channel, model, raw_model, ts, local_date, "
    "input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, "
    "reasoning_tokens, cost_usd, session_id, request_id, project, dedup_key, raw_ref"
)
