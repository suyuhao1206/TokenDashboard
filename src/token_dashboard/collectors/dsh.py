"""DSH（DeepSeek Harness）本地会话采集器。

数据位置：`~/.dsh/sessions/<编码项目>/<session-uuid>/session.v4.jsonl.zstd`
（zstd 压缩的 JSONL，依赖 `zstandard`）

用量事件：`type=assistant/message` → `data.usage`
  `{inputTokens, outputTokens, totalTokens, cacheReadTokens?}`
  实测三者**互不重叠**（totalTokens = input + output + cacheRead），直接映射即可。
模型/渠道：`type=model/selection` → `data = {provider, model, reasoningEffort}`
时间：顶层 `time` 为**毫秒时间戳**
去重键：会话 id（= 会话目录名）+ 事件序号 seq（缺 seq 时用行号）。
不用文件完整路径，避免目录被改名/移动后整份用量重新入库。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import UsageEvent, make_dedup_key
from ..normalize import local_date, norm_model, to_utc_iso

SOURCE = "dsh"
HARNESS = "dsh"
DEDUP_VERSION = 2  # 2 = 会话 id + seq（1 = 文件路径 + seq，已废弃）


def _ms_to_dt(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def decode_project(name: str) -> str:
    """把 DSH 的目录编码还原成路径：`--D-code-~5947~601D~5999~60F3--` → `D:\\code\\奇思妙想`。

    `~XXXX` 是 UTF-16 码元的十六进制；`-` 表示路径分隔符（含真实短横线时会有偏差）。
    """
    s = name.strip("-")
    s = re.sub(r"~([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("-", "\\")


def scan(store, cfg: dict, pricing, tz) -> dict:
    paths = cfg.get("paths") or []
    stats = {"source": SOURCE, "files": 0, "skipped": 0, "events": 0, "errors": 0}
    migrated = store.ensure_dedup_version(SOURCE, DEDUP_VERSION)
    if migrated:
        stats["note"] = f"去重方案升级，已清空 {migrated} 条旧记录并重新采集"
    try:
        import zstandard as zstd
    except ImportError:
        stats["note"] = "缺少 zstandard 依赖（uv add zstandard）"
        return stats

    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(p.rglob("*.zstd"))
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

        try:
            with f.open("rb") as fh:
                text = zstd.ZstdDecompressor().stream_reader(fh).read().decode("utf-8", "replace")
        except Exception:
            stats["errors"] += 1
            continue

        events: list[UsageEvent] = []
        current_model: str | None = None
        current_provider: str | None = None
        session_id = f.parent.name
        project = decode_project(f.parent.parent.name)

        for lineno, line in enumerate(text.splitlines(), 1):
            if '"usage"' not in line and "model/selection" not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                stats["errors"] += 1
                continue
            typ = obj.get("type")
            data = obj.get("data") if isinstance(obj.get("data"), dict) else {}

            if typ == "model/selection":
                current_model = data.get("model") or current_model
                current_provider = data.get("provider") or current_provider
                continue
            if typ != "assistant/message":
                continue

            usage = data.get("usage") or {}
            inp = int(usage.get("inputTokens") or 0)
            out = int(usage.get("outputTokens") or 0)
            cr = int(usage.get("cacheReadTokens") or 0)
            if inp + out + cr <= 0:
                continue

            dt = _ms_to_dt(obj.get("time"))
            if dt is None:
                # 无时间戳的事件进不了任何按天统计，直接跳过并计入 errors
                stats["errors"] += 1
                continue
            ts = to_utc_iso(dt)
            model = norm_model(current_model)
            cost = pricing.cost(model, inp, cr, 0, out)
            events.append(
                UsageEvent(
                    source=SOURCE,
                    harness=HARNESS,
                    ts=ts,
                    local_date=local_date(dt, tz),
                    model=model,
                    raw_model=current_model,
                    channel=current_provider,
                    input_tokens=inp,
                    cache_read_tokens=cr,
                    cache_write_tokens=0,
                    output_tokens=out,
                    reasoning_tokens=0,
                    cost_usd=cost,
                    session_id=session_id,
                    request_id=str((data.get("message") or {}).get("id") or ""),
                    project=project,
                    dedup_key=make_dedup_key(
                        SOURCE,
                        session_id,
                        str(obj.get("seq")) if obj.get("seq") is not None else f"L{lineno}",
                    ),
                    raw_ref=f"{f.name}:{lineno}",
                )
            )

        stats["events"] += store.upsert_events(events)
        store.mark_file(SOURCE, str(f), st.st_mtime, st.st_size)
        stats["files"] += 1

    return stats
