"""Claude Code 本地日志采集器。

数据位置：~/.claude/projects/<编码路径>/<session-uuid>.jsonl
每条 assistant 消息的 message.usage 含 input / output / cache_read / cache_creation。
去重键：message.id + requestId（回退：文件名 + 行号）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import UsageEvent, make_dedup_key
from ..normalize import local_date, norm_model, parse_iso, to_utc_iso

SOURCE = "claude_code"
HARNESS = "claude-code"


def scan(store, cfg: dict, pricing, tz) -> dict:
    paths = cfg.get("paths") or []
    stats = {"source": SOURCE, "files": 0, "skipped": 0, "events": 0, "errors": 0}

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
                if '"usage"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats["errors"] += 1
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage") or {}
                if not usage:
                    continue

                inp = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                cr = int(usage.get("cache_read_input_tokens") or 0)
                # cache_creation 明细（5m/1h）比标量更准确，优先采用（与 ccusage 口径一致）
                cc_detail = usage.get("cache_creation")
                if isinstance(cc_detail, dict):
                    cw = int(cc_detail.get("ephemeral_5m_input_tokens") or 0) + int(
                        cc_detail.get("ephemeral_1h_input_tokens") or 0
                    )
                else:
                    cw = int(usage.get("cache_creation_input_tokens") or 0)
                if inp + out + cr + cw <= 0:
                    continue

                raw_model = msg.get("model")
                if raw_model == "<synthetic>":
                    continue

                dt = parse_iso(obj.get("timestamp"))
                if dt is None:
                    # 无时间戳的事件进不了任何按天统计，直接跳过并计入 errors
                    stats["errors"] += 1
                    continue
                ts = to_utc_iso(dt)
                model = norm_model(raw_model)
                cost = pricing.cost(model, inp, cr, cw, out)
                # 去重规则（与 ccusage 对齐）：
                # - 有 requestId：按 (message.id, requestId) 去重，保留时间戳最新一条
                # - 无 requestId：逐条计数（按 uuid 保证唯一）
                req_id = obj.get("requestId")
                if req_id:
                    key = make_dedup_key(SOURCE, str(msg.get("id") or ""), str(req_id))
                else:
                    key = make_dedup_key(SOURCE, str(obj.get("uuid") or f"{f.name}:{lineno}"))

                events.append(
                    UsageEvent(
                        source=SOURCE,
                        harness=HARNESS,
                        ts=ts,
                        local_date=local_date(dt, tz),
                        model=model,
                        raw_model=raw_model,
                        input_tokens=inp,
                        cache_read_tokens=cr,
                        cache_write_tokens=cw,
                        output_tokens=out,
                        reasoning_tokens=0,
                        cost_usd=cost,
                        session_id=obj.get("sessionId"),
                        request_id=obj.get("requestId"),
                        project=obj.get("cwd"),
                        dedup_key=key,
                        raw_ref=f"{f.name}:{lineno}",
                    )
                )

        stats["events"] += store.upsert_events(events)
        store.mark_file(SOURCE, str(f), st.st_mtime, st.st_size)
        stats["files"] += 1

    return stats
