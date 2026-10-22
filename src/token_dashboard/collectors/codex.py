"""Codex 本地日志采集器。

数据位置：~/.codex/sessions/**/rollout-*.jsonl（+ archived_sessions）

两种事件格式（同一文件优先新格式，避免双计）：
1. 旧格式：type=event_msg / payload.type=token_count
   - payload.info.total_token_usage  会话累计
   - payload.info.last_token_usage   本次增量
2. 新格式：type=token_usage_record
   - payload.thread_token_usage      线程累计
   - payload.usage                   本次增量

计数口径（与 ccusage 对齐；已对 2026-05-01 ~ 2026-09-30 全量逐日核对）：
- 文件内按事件顺序推进"累计基线"，增量 = 本次累计 − 上次累计；
- 文件首条事件没有前序基线，用其 last_token_usage / usage
  （续写文件的首条事件累计包含上一段用量，直接用累计会重复计入）；
- 累计出现回退时按单调化处理（取 max）；
- 拆分：非缓存输入 = max(0, Δinput − Δcached)，缓存读取 = min(Δinput, Δcached)。

模型与项目：turn_context（payload.model / payload.cwd）与 session_meta 顺序关联。
去重键：**文件名（不含目录）+ 行号**。Codex 会把会话从 `sessions/` 移到
`archived_sessions/`，用完整路径做键会让同一份用量按新路径再入一次（数据翻倍），
所以键必须与文件的物理位置解耦（rollout 文件名自带时间戳 + uuid，足够唯一）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import UsageEvent, make_dedup_key
from ..normalize import local_date, norm_model, normalize_output, parse_iso, to_utc_iso

SOURCE = "codex"
HARNESS = "codex"
# 3 = output_tokens 归一为「含推理」（此前存的是纯文本输出，总量与成本双算推理）
DEDUP_VERSION = 3

_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "cache_write_input_tokens",
    "total_tokens",
)


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

        rows: list[tuple] = []
        current_model: str | None = None
        session_id: str | None = None
        cwd: str | None = None

        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if (
                    "token_count" not in line
                    and "token_usage_record" not in line
                    and "turn_context" not in line
                    and '"session_meta"' not in line
                ):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats["errors"] += 1
                    continue

                typ = obj.get("type")
                pl = obj.get("payload") or {}

                if typ == "session_meta":
                    session_id = pl.get("session_id") or session_id
                    cwd = pl.get("cwd") or cwd
                    continue
                if typ == "turn_context":
                    if pl.get("model"):
                        current_model = pl.get("model")
                    cwd = pl.get("cwd") or cwd
                    continue
                if typ == "event_msg" and pl.get("type") == "token_count":
                    info = pl.get("info") or {}
                    rows.append(
                        (
                            "old", lineno, obj.get("timestamp"),
                            info.get("total_token_usage") or {},
                            info.get("last_token_usage") or {},
                            current_model, cwd, session_id,
                        )
                    )
                elif typ == "token_usage_record":
                    rows.append(
                        (
                            "new", lineno, obj.get("timestamp"),
                            pl.get("thread_token_usage") or {},
                            pl.get("usage") or {},
                            current_model, cwd, pl.get("session_id") or session_id,
                        )
                    )

        if not rows:
            store.mark_file(SOURCE, str(f), st.st_mtime, st.st_size)
            stats["files"] += 1
            continue

        if any(r[0] == "new" for r in rows):
            rows = [r for r in rows if r[0] == "new"]

        events: list[UsageEvent] = []
        prev: list[int] | None = None
        for kind, lineno, ts_raw, total, last, model_raw, cwd_v, sid in rows:
            cur = [int(total.get(k) or 0) for k in _FIELDS]
            if prev is None:
                delta = [int(last.get(k) or 0) for k in _FIELDS]
            else:
                cur = [max(prev[k], cur[k]) for k in range(len(_FIELDS))]
                delta = [cur[k] - prev[k] for k in range(len(_FIELDS))]
            prev = cur

            inp = max(0, delta[0] - delta[1])
            cr = min(delta[0], delta[1])
            o = max(0, delta[2])
            rr = max(0, delta[3])
            cw = max(0, delta[4])
            if inp + cr + cw + o + rr <= 0:
                continue

            # OpenAI 口径的 output 已含推理；gemini 等第三方会在 total 里另加一份。
            # 统一归一成「含推理的输出」，下游 _TOKENS / pricing 直接相加即可。
            # 注意 total_tokens 的口径是 input（含缓存）+ output，所以 prompt 取 delta[0]。
            out = normalize_output(o, rr, delta[5], delta[0])

            dt = parse_iso(ts_raw)
            if dt is None:
                # 没有时间戳的事件无法进入日报/KPI/热力图，入库只会让各区块对不上账
                stats["errors"] += 1
                continue
            ts = to_utc_iso(dt)
            model = norm_model(model_raw)
            cost = pricing.cost(model, inp, cr, cw, out)
            events.append(
                UsageEvent(
                    source=SOURCE,
                    harness=HARNESS,
                    ts=ts,
                    local_date=local_date(dt, tz),
                    model=model,
                    raw_model=model_raw,
                    input_tokens=inp,
                    cache_read_tokens=cr,
                    cache_write_tokens=cw,
                    output_tokens=out,
                    reasoning_tokens=rr,
                    cost_usd=cost,
                    session_id=sid,
                    request_id=None,
                    project=cwd_v,
                    dedup_key=make_dedup_key(SOURCE, f.stem, str(lineno)),
                    raw_ref=f"{f.name}:{lineno}",
                )
            )

        stats["events"] += store.upsert_events(events)
        store.mark_file(SOURCE, str(f), st.st_mtime, st.st_size)
        stats["files"] += 1

    return stats
