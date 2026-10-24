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
# 3 = decode_project 修正盘符冒号与带连字符目录名的还原（2 = 会话 id + seq）。
# 算法升级必须升版本号：旧记录的 project 字段是错误路径，不清空重采的话
# 「项目分布 Top 10」里同一个项目会一直以两个名字存在。
DEDUP_VERSION = 3


def _ms_to_dt(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def decode_project(name: str) -> str:
    """把 DSH 的目录编码还原成路径：`--D-code-~5947~601D~5999~60F3--` → `D:\\code\\奇思妙想`。

    `~XXXX` 是 UTF-16 码元的十六进制；`-` 既是路径分隔符也可能出现在真实目录名里
    （如 token-dashboard），没法从编码本身区分。策略：
    1. 首段是单个字母时按 Windows 盘符处理，补回被吞掉的 `:`（否则 `D:` 丢失冒号）；
    2. 逐段向下拼接时优先尝试「把后续多段用 `-` 合并」，合并后的目录在本机磁盘上
       真实存在就采纳——这样 token-dashboard 不会被拆成 token\\dashboard；
    3. 磁盘上找不到时按单段回退（老行为，目录被删/换机器时仍能给出最接近的路径）。
    """
    s = name.strip("-")
    s = re.sub(r"~([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), s)
    parts = [p for p in s.split("-") if p]
    if not parts:
        return name
    if len(parts[0]) == 1 and parts[0].isalpha():
        cur = Path(parts[0] + ":\\")   # 盘符段：编码时 `D:` 的冒号被替换成了 `-`
    else:
        cur = Path(parts[0])
    idx = 1
    while idx < len(parts):
        matched = None
        for end in range(len(parts), idx, -1):   # 优先最长的合并（贪心）
            candidate = cur / "-".join(parts[idx:end])
            try:
                if candidate.exists():
                    matched = (candidate, end)
                    break
            except OSError:
                pass
        if matched:
            cur, idx = matched
        else:
            cur = cur / parts[idx]
            idx += 1
    return str(cur)


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
