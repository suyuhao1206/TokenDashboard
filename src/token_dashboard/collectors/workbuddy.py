"""WorkBuddy 本地会话采集器。

数据位置：`~/.workbuddy/projects/<编码项目>/<session-uuid>.jsonl`（UTF-8 JSONL）

用量字段：`providerData.usage`（应用归一化后的形式，首选）
  `{requests, inputTokens, outputTokens, totalTokens, inputTokensDetails:[{cached_tokens}],
    outputTokensDetails:[{reasoning_tokens}]}`
  回退：`providerData.rawUsage`（OpenAI 风格 prompt_tokens / completion_tokens / *_details）
口径：`非缓存输入 = inputTokens − cached_tokens`、`缓存读取 = cached_tokens`
（OpenAI 语义里 inputTokens/prompt_tokens 已包含缓存部分，与 Codex 同款处理）

模型：`providerData.requestModelName`（回退 `model`）；时间：顶层 `timestamp`（毫秒）
去重键：会话 id + messageId（实测每条用量行的 messageId 唯一）；不用文件路径，
避免项目目录改名后整份用量重新入库。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import UsageEvent, make_dedup_key
from ..normalize import local_date, norm_model, normalize_output, to_utc_iso

SOURCE = "workbuddy"
HARNESS = "workbuddy"
# 3 = output_tokens 归一为「含推理」（此前存的是纯文本输出，总量与成本双算推理）
DEDUP_VERSION = 3


def _ms_to_dt(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _cached_tokens(usage: dict) -> int:
    details = usage.get("inputTokensDetails")
    if isinstance(details, list):
        return sum(int(d.get("cached_tokens") or 0) for d in details if isinstance(d, dict))
    det = usage.get("prompt_tokens_details")
    if isinstance(det, dict):
        return int(det.get("cached_tokens") or 0)
    return 0


def _reasoning_tokens(usage: dict) -> int:
    details = usage.get("outputTokensDetails")
    if isinstance(details, list):
        return sum(int(d.get("reasoning_tokens") or 0) for d in details if isinstance(d, dict))
    det = usage.get("completion_tokens_details")
    if isinstance(det, dict):
        return int(det.get("reasoning_tokens") or 0)
    return 0


def scan(store, cfg: dict, pricing, tz) -> dict:
    paths = cfg.get("paths") or []
    stats = {"source": SOURCE, "files": 0, "skipped": 0, "events": 0, "errors": 0}
    migrated = store.ensure_dedup_version(SOURCE, DEDUP_VERSION)
    if migrated:
        stats["note"] = f"去重方案升级，已清空 {migrated} 条旧记录并重新采集"

    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(p.rglob("*.jsonl"))
        elif p.exists():
            files.append(p)

    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        if store.file_unchanged(SOURCE, str(f), st.st_mtime, st.st_size):
            stats["skipped"] += 1
            continue

        events: list[UsageEvent] = []
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if '"providerData"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats["errors"] += 1
                    continue
                pd = obj.get("providerData")
                if not isinstance(pd, dict):
                    continue
                usage = pd.get("usage") if isinstance(pd.get("usage"), dict) else None
                raw = pd.get("rawUsage") if isinstance(pd.get("rawUsage"), dict) else None
                src = usage if usage and "inputTokens" in usage else raw
                if not isinstance(src, dict):
                    continue

                total_in = int(src.get("inputTokens") or src.get("prompt_tokens") or 0)
                out = int(src.get("outputTokens") or src.get("completion_tokens") or 0)
                cached = _cached_tokens(src)
                reasoning = _reasoning_tokens(src)
                inp = max(0, total_in - cached)
                cr = min(total_in, cached)
                if inp + cr + out + reasoning <= 0:
                    continue

                # 同 Codex：OpenAI/gemini 有的把推理算在 output 里，有的单独加。
                # 用 totalTokens 判定，统一成「含推理的输出」。
                total_raw = src.get("totalTokens") or src.get("total_tokens")
                try:
                    total_raw = int(total_raw) if total_raw is not None else None
                except (TypeError, ValueError):
                    total_raw = None
                out_norm = normalize_output(out, reasoning, total_raw, total_in)

                dt = _ms_to_dt(obj.get("timestamp"))
                if dt is None:
                    # 无时间戳的事件进不了任何按天统计，直接跳过并计入 errors
                    stats["errors"] += 1
                    continue
                ts = to_utc_iso(dt)
                raw_model = pd.get("requestModelName") or pd.get("model")
                model = norm_model(raw_model)
                cost = pricing.cost(model, inp, cr, 0, out_norm)
                events.append(
                    UsageEvent(
                        source=SOURCE,
                        harness=HARNESS,
                        ts=ts,
                        local_date=local_date(dt, tz),
                        model=model,
                        raw_model=raw_model,
                        channel=None,
                        input_tokens=inp,
                        cache_read_tokens=cr,
                        cache_write_tokens=0,
                        output_tokens=out_norm,
                        reasoning_tokens=reasoning,
                        cost_usd=cost,
                        session_id=obj.get("sessionId") or f.stem,
                        request_id=str(pd.get("messageId") or ""),
                        project=obj.get("cwd"),
                        dedup_key=make_dedup_key(
                            SOURCE,
                            obj.get("sessionId") or f.stem,
                            str(pd.get("messageId")) if pd.get("messageId") is not None else f"L{lineno}",
                        ),
                        raw_ref=f"{f.name}:{lineno}",
                    )
                )

        stats["events"] += store.upsert_events(events)
        store.mark_file(SOURCE, str(f), st.st_mtime, st.st_size)
        stats["files"] += 1

    return stats
