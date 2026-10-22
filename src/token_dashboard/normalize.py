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


def normalize_output(output: int, reasoning: int, total: int | None, prompt: int) -> int:
    """把「输出」归一成**含推理**的总输出（OpenAI 口径）。

    不同供应商对 output/completion 的定义并不一致：
    - OpenAI / Codex(gpt-*)：`total_tokens == prompt + output`，推理已含在 output 内
      （`reasoning_output_tokens` 只是它的子集），再加一次就是重复计数；
    - 部分第三方（实测 Codex 里的 gemini、WorkBuddy 里的 gemini）：会在同一条里
      单独上报推理，`total_tokens == prompt + output + reasoning`，需要相加。

    入参 total 缺失时退化为启发式（reasoning > output 说明显然没有包含）。
    返回值统一为「含推理的输出」，报表里的「推理」列仍然保留，作为信息量展示。
    """
    if reasoning <= 0:
        return output
    if total:
        if total > prompt + output and abs(total - (prompt + output + reasoning)) <= 1:
            return output + reasoning
        if abs(total - (prompt + output)) <= 1:
            return output
    return output + reasoning if reasoning > output else output


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
