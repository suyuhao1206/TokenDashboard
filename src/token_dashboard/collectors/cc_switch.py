"""cc-switch 渠道数据采集器（只读）。

数据位置：`~/.cc-switch/cc-switch.db`（SQLite，只读打开）
- `providers`：渠道注册表。**只读 id / name / app_type / cost_multiplier 等，不读 settings_config（含密钥）**
- `usage_daily_rollups`：按天 × 渠道 × 模型的汇总（首选来源）
- `proxy_request_logs`：逐请求日志（当 rollups 为空时按天聚合兜底）

注意：cc-switch 里 `provider_id` 以 `_` 开头（如 `_session`、`_codex_session`）表示
"由会话日志推算、无法归属到具体渠道"的用量，这里标为 `会话日志·<app_type>`。

结果写入独立的 `channel_daily` 表，**不与 harness 的 usage_events 合并**
（本地日志与渠道数据覆盖同一批请求，合并会重复计数；面板分两个视图呈现）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import BUSY_TIMEOUT_MS, read_only_uri

SOURCE = "cc_switch"
_SOURCES = ["cc_switch.rollup", "cc_switch.proxy"]


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(read_only_uri(db_path), uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def scan(store, cfg: dict, pricing=None, tz=None) -> dict:
    db_path = Path(cfg.get("db_path") or (Path.home() / ".cc-switch" / "cc-switch.db"))
    stats = {"source": SOURCE, "rows": 0, "channels": 0, "note": ""}
    if not db_path.exists():
        stats["note"] = f"未找到 {db_path}"
        return stats
    try:
        conn = _connect(db_path)
    except sqlite3.Error as exc:  # 文件被占用 / 权限问题
        stats["note"] = f"无法只读打开：{exc}"
        return stats

    try:
        providers = {
            str(r["id"]): r["name"] for r in conn.execute("SELECT id, name FROM providers")
        }

        def channel_name(pid, app_type) -> str:
            pid = str(pid or "")
            if pid.startswith("_"):
                return f"会话日志·{app_type or 'unknown'}"
            return providers.get(pid, pid or "unknown")

        rows: list[dict] = []
        rollup_n = conn.execute("SELECT COUNT(*) FROM usage_daily_rollups").fetchone()[0]
        if rollup_n:
            for r in conn.execute(
                "SELECT date, provider_id, app_type, model, request_count, input_tokens, output_tokens, "
                "cache_read_tokens, cache_creation_tokens, total_cost_usd FROM usage_daily_rollups"
            ):
                rows.append(
                    dict(
                        channel=channel_name(r["provider_id"], r["app_type"]),
                        date=r["date"],
                        model=r["model"] or "-",
                        requests=int(r["request_count"] or 0),
                        input_tokens=int(r["input_tokens"] or 0),
                        cache_read_tokens=int(r["cache_read_tokens"] or 0),
                        cache_write_tokens=int(r["cache_creation_tokens"] or 0),
                        output_tokens=int(r["output_tokens"] or 0),
                        cost_usd=r["total_cost_usd"],
                        source="cc_switch.rollup",
                    )
                )
            stats["note"] = f"来源 usage_daily_rollups（{rollup_n} 行）"
        else:
            for r in conn.execute(
                "SELECT substr(created_at,1,10) AS d, provider_id, app_type, model, COUNT(*) AS n, "
                "SUM(input_tokens) AS i, SUM(output_tokens) AS o, SUM(cache_read_tokens) AS cr, "
                "SUM(cache_creation_tokens) AS cw, SUM(total_cost_usd) AS c "
                "FROM proxy_request_logs GROUP BY d, provider_id, app_type, model"
            ):
                rows.append(
                    dict(
                        channel=channel_name(r["provider_id"], r["app_type"]),
                        date=r["d"],
                        model=r["model"] or "-",
                        requests=int(r["n"] or 0),
                        input_tokens=int(r["i"] or 0),
                        cache_read_tokens=int(r["cr"] or 0),
                        cache_write_tokens=int(r["cw"] or 0),
                        output_tokens=int(r["o"] or 0),
                        cost_usd=r["c"],
                        source="cc_switch.proxy",
                    )
                )
            stats["note"] = "来源 proxy_request_logs（按天聚合）"

        # 先清掉本采集器上一次写入的行，避免改名后残留旧行
        store.clear_channel_daily(_SOURCES)
        stats["rows"] = store.upsert_channel_daily(rows)
        stats["channels"] = len({r["channel"] for r in rows})
    finally:
        conn.close()
    return stats
