"""SQLite 存储层：幂等入库 + 查询。

FastAPI 的同步端点跑在线程池里，因此连接使用 check_same_thread=False，
并用一把互斥锁串行化访问（本地单用户面板，性能足够）。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .models import COLUMNS, UsageEvent

BUSY_TIMEOUT_MS = 5000


class DatabaseMissing(FileNotFoundError):
    """只读打开时数据库文件不存在（调用方需要给出友好提示）。"""


def read_only_uri(path: str | Path) -> str:
    """构造只读 URI。路径必须做百分号转义，否则 `#` / `?` / `%` 会被当成 URI 语法。

    （实测：未转义时 `~/.dsh/a#b/usage.db` 会被截断成另一个路径，而且 `?mode=ro`
    一起被吞掉，SQLite 会以读写方式新建一个空库——静默指向错误的数据文件。）
    """
    return "file:" + quote(str(path).replace("\\", "/")) + "?mode=ro"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  harness TEXT,
  channel TEXT,
  model TEXT,
  raw_model TEXT,
  ts TEXT NOT NULL,
  local_date TEXT,
  input_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cache_write_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  reasoning_tokens INTEGER DEFAULT 0,
  cost_usd REAL,
  session_id TEXT,
  request_id TEXT,
  project TEXT,
  dedup_key TEXT UNIQUE,
  raw_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_events(local_date);
CREATE INDEX IF NOT EXISTS idx_usage_harness ON usage_events(harness);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_events(model);
CREATE TABLE IF NOT EXISTS ingest_state (
  source TEXT PRIMARY KEY,
  cursor TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS scanned_files (
  source TEXT NOT NULL,
  path TEXT NOT NULL,
  mtime REAL,
  size INTEGER,
  PRIMARY KEY (source, path)
);
CREATE TABLE IF NOT EXISTS collector_meta (
  source TEXT PRIMARY KEY,
  dedup_version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS channel_daily (
  channel TEXT NOT NULL,
  date TEXT NOT NULL,
  model TEXT,
  requests INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cache_write_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cost_usd REAL,
  source TEXT,
  PRIMARY KEY (channel, date, model)
);
"""

_UPSERT = f"""
INSERT INTO usage_events ({COLUMNS}) VALUES ({", ".join(["?"] * 18)})
ON CONFLICT(dedup_key) DO UPDATE SET
  input_tokens=excluded.input_tokens,
  cache_read_tokens=excluded.cache_read_tokens,
  cache_write_tokens=excluded.cache_write_tokens,
  output_tokens=excluded.output_tokens,
  reasoning_tokens=excluded.reasoning_tokens,
  cost_usd=excluded.cost_usd,
  model=excluded.model,
  raw_model=excluded.raw_model,
  channel=excluded.channel,
  project=excluded.project,
  ts=excluded.ts,
  local_date=excluded.local_date
WHERE excluded.ts >= usage_events.ts
"""

_DIMS = {
    "harness": "harness",
    "model": "model",
    "project": "project",
    "channel": "channel",
    "source": "source",
}

_TOKENS = (
    "SUM(input_tokens+cache_read_tokens+cache_write_tokens+output_tokens+reasoning_tokens)"
)


class Store:
    def __init__(self, db_path: str | Path, read_only: bool = False):
        p = Path(db_path)
        self._lock = threading.Lock()
        self.path = p
        if read_only:
            # 只读打开（供小组件等消费方使用，不会创建/修改表）
            # sqlite 的 mode=ro 对不存在的文件直接抛 OperationalError，
            # 这里换成明确的异常，调用方才能给出「请先运行 scan」这类提示。
            if not p.exists():
                raise DatabaseMissing(f"数据库不存在：{p}")
            self.conn = sqlite3.connect(read_only_uri(p), uri=True, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            # 大 GROUP BY（如会话明细）需要临时 b-tree；默认会落到磁盘临时文件，
            # 而数据库路径含中文时 SQLite 的临时文件名解析会失败（unable to open database file）。
            # 本地库只有几万行，临时结果放内存即可，顺便更快。
            self.conn.execute("PRAGMA temp_store=MEMORY")
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 面板服务、小组件自动扫描可能同时读写同一个文件，开 WAL + busy_timeout 兜住锁冲突
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # ---------- 去重方案迁移 ----------
    def ensure_dedup_version(self, source: str, version: int) -> int:
        """去重键算法升级时清空该来源的旧行，返回删除条数。

        旧行的 dedup_key 由旧算法生成，新算法无法与之匹配：如果不清空，同一份用量会
        按新键再入一次（数据翻倍）。这些数据都能从本地日志重建，所以直接删除 + 清空
        scanned_files 强制重扫。版本号一致时是空操作（只多一次 SELECT）。
        """
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT dedup_version FROM collector_meta WHERE source=?", (source,)
            ).fetchone()
            if row is not None and int(row["dedup_version"]) == int(version):
                return 0
            removed = self.conn.execute(
                "DELETE FROM usage_events WHERE source=?", (source,)
            ).rowcount
            self.conn.execute("DELETE FROM scanned_files WHERE source=?", (source,))
            self.conn.execute(
                "INSERT INTO collector_meta (source, dedup_version, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(source) DO UPDATE SET dedup_version=excluded.dedup_version, "
                "updated_at=excluded.updated_at",
                (source, int(version), datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            return max(0, removed)

    # ---------- 写入 ----------
    def upsert_events(self, events: list[UsageEvent]) -> int:
        if not events:
            return 0
        with self._lock, self.conn:
            self.conn.executemany(_UPSERT, [e.to_row() for e in events])
        return len(events)

    def file_unchanged(self, source: str, path: str, mtime: float, size: int) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT mtime, size FROM scanned_files WHERE source=? AND path=?",
                (source, path),
            ).fetchone()
        return bool(row and row["mtime"] == mtime and row["size"] == size)

    def mark_file(self, source: str, path: str, mtime: float, size: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO scanned_files (source, path, mtime, size) VALUES (?,?,?,?) "
                "ON CONFLICT(source, path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size",
                (source, path, mtime, size),
            )

    # ---------- 通用过滤 ----------
    @staticmethod
    def _filter(since=None, until=None, harness=None, channel=None):
        frag = ""
        args: list = []
        if since:
            frag += " AND local_date >= ?"
            args.append(since)
        if until:
            frag += " AND local_date <= ?"
            args.append(until)
        if harness:
            frag += " AND harness = ?"
            args.append(harness)
        if channel:
            frag += " AND channel = ?"
            args.append(channel)
        return frag, args

    # ---------- 查询 ----------
    def daily_report(self, since=None, until=None, harness=None) -> list[sqlite3.Row]:
        frag, args = self._filter(since, until, harness)
        sql = f"""
        SELECT local_date AS date,
               SUM(input_tokens) AS input_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(cache_write_tokens) AS cache_write_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(reasoning_tokens) AS reasoning_tokens,
               {_TOKENS} AS tokens,
               SUM(cost_usd) AS cost_usd,
               COUNT(*) AS events,
               SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced
        FROM usage_events
        WHERE local_date IS NOT NULL{frag}
        GROUP BY local_date ORDER BY local_date
        """
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def daily_by(self, dim="harness", since=None, until=None, harness=None) -> list[sqlite3.Row]:
        col = _DIMS.get(dim, "harness")
        frag, args = self._filter(since, until, harness)
        sql = f"""
        SELECT local_date AS date, COALESCE({col}, '-') AS key,
               {_TOKENS} AS tokens, SUM(cost_usd) AS cost
        FROM usage_events
        WHERE local_date IS NOT NULL{frag}
        GROUP BY local_date, key ORDER BY local_date
        """
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def breakdown(self, dim="model", since=None, until=None, harness=None, limit=20) -> list[sqlite3.Row]:
        col = _DIMS.get(dim, "model")
        frag, args = self._filter(since, until, harness)
        sql = f"""
        SELECT COALESCE({col}, '-') AS key, COUNT(*) AS events,
               {_TOKENS} AS tokens, SUM(cost_usd) AS cost
        FROM usage_events
        WHERE local_date IS NOT NULL{frag}
        GROUP BY key ORDER BY tokens DESC LIMIT ?
        """
        args = args + [limit]
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def sessions(self, limit=50, since=None, until=None, harness=None) -> list[sqlite3.Row]:
        frag, args = self._filter(since, until, harness)
        # 没有 session_id 的事件按「来源 + 文件」聚合，避免每个单事件伪会话独占一行
        # （注意：GROUP BY 里必须写完整表达式——SQLite 会把同名的 session_id 解析成
        #  输入列，导致所有 NULL 会话被并成一行）
        sid = (
            "CASE WHEN session_id IS NOT NULL THEN session_id "
            "WHEN instr(raw_ref, ':') > 0 "
            "THEN source || '·' || substr(raw_ref, 1, instr(raw_ref, ':') - 1) "
            "ELSE COALESCE(raw_ref, '-') END"
        )
        sql = f"""
        SELECT {sid} AS session_id,
               CASE WHEN session_id IS NULL THEN 1 ELSE 0 END AS pseudo,
               harness, MAX(project) AS project,
               MIN(ts) AS started, MAX(ts) AS ended, COUNT(*) AS events,
               {_TOKENS} AS tokens, SUM(cost_usd) AS cost,
               SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced
        FROM usage_events
        WHERE 1=1{frag}
        GROUP BY {sid}, harness ORDER BY MAX(ts) DESC LIMIT ?
        """
        args = args + [limit]
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def summary(self) -> dict:
        with self._lock:
            row = self.conn.execute(
                f"SELECT COUNT(*) AS events, {_TOKENS} AS tokens, "
                "SUM(cost_usd) AS cost FROM usage_events"
            ).fetchone()
        return dict(row) if row else {}

    def by_harness(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                f"SELECT harness, COUNT(*) AS events, {_TOKENS} AS tokens, "
                "SUM(cost_usd) AS cost FROM usage_events GROUP BY harness ORDER BY tokens DESC"
            ).fetchall()

    def totals(self, since=None, until=None, harness=None) -> dict:
        frag, args = self._filter(since, until, harness)
        sql = (
            "SELECT COUNT(*) AS events, "
            f"{_TOKENS} AS tokens, "
            "SUM(input_tokens) AS input, "
            "SUM(cache_read_tokens) AS cache_read, "
            "SUM(cache_write_tokens) AS cache_write, "
            "SUM(output_tokens) AS output, "
            "SUM(reasoning_tokens) AS reasoning, "
            "SUM(cost_usd) AS cost, "
            "SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced "
            "FROM usage_events WHERE 1=1" + frag
        )
        with self._lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else {}

    # ---------- 最近请求（分页） ----------
    def recent_requests(self, page: int = 1, size: int = 20,
                        since=None, until=None, harness=None) -> dict:
        frag, args = self._filter(since, until, harness)
        page = max(1, int(page))
        size = min(200, max(1, int(size)))
        with self._lock:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE 1=1" + frag, args
            ).fetchone()[0]
            rows = self.conn.execute(
                "SELECT ts, local_date, harness, model, raw_model, channel, project, "
                "input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, "
                "reasoning_tokens, cost_usd, session_id "
                "FROM usage_events WHERE 1=1" + frag +
                " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                args + [size, (page - 1) * size],
            ).fetchall()
        return {"total": total, "page": page, "size": size,
                "rows": [dict(r) for r in rows]}

    # ---------- 活跃时段（按 UTC 小时聚合，调用方负责换算到本地时区） ----------
    def hourly(self, since=None, until=None, harness=None) -> list[sqlite3.Row]:
        frag, args = self._filter(since, until, harness)
        sql = f"""
        SELECT CAST(substr(ts, 12, 2) AS INTEGER) AS utc_hour,
               COUNT(*) AS events, {_TOKENS} AS tokens
        FROM usage_events
        WHERE length(ts) >= 13{frag}
        GROUP BY utc_hour
        """
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def meta(self) -> dict:
        with self._lock:
            harnesses = [
                r[0]
                for r in self.conn.execute(
                    "SELECT DISTINCT harness FROM usage_events WHERE harness IS NOT NULL ORDER BY harness"
                )
            ]
            channels = [
                r[0]
                for r in self.conn.execute(
                    "SELECT DISTINCT channel FROM channel_daily WHERE channel IS NOT NULL ORDER BY channel"
                )
            ]
            row = self.conn.execute(
                "SELECT MIN(local_date) AS min_date, MAX(local_date) AS max_date FROM usage_events"
            ).fetchone()
            ch = self.conn.execute(
                "SELECT COUNT(*) AS n, SUM(cost_usd) AS cost FROM channel_daily"
            ).fetchone()
        return {
            "harnesses": harnesses,
            "channels": channels,
            "min_date": row["min_date"],
            "max_date": row["max_date"],
            "channel_rows": ch["n"] or 0,
            "channel_cost": ch["cost"],
        }

    # ---------- 渠道视图（来自 cc-switch 等，独立存储，不与 harness 总量合并） ----------
    def upsert_channel_daily(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT INTO channel_daily (channel, date, model, requests, input_tokens, "
                "cache_read_tokens, cache_write_tokens, output_tokens, cost_usd, source) "
                "VALUES (:channel, :date, :model, :requests, :input_tokens, :cache_read_tokens, "
                ":cache_write_tokens, :output_tokens, :cost_usd, :source) "
                "ON CONFLICT(channel, date, model) DO UPDATE SET "
                "requests=excluded.requests, input_tokens=excluded.input_tokens, "
                "cache_read_tokens=excluded.cache_read_tokens, cache_write_tokens=excluded.cache_write_tokens, "
                "output_tokens=excluded.output_tokens, cost_usd=excluded.cost_usd, source=excluded.source",
                rows,
            )
        return len(rows)

    def channel_daily(self, since=None, until=None) -> list[sqlite3.Row]:
        frag = ""
        args: list = []
        if since:
            frag += " AND date >= ?"
            args.append(since)
        if until:
            frag += " AND date <= ?"
            args.append(until)
        sql = (
            "SELECT channel, date, SUM(requests) AS requests, "
            "SUM(input_tokens) AS input_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
            "SUM(cache_write_tokens) AS cache_write_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(cost_usd) AS cost_usd "
            "FROM channel_daily WHERE 1=1" + frag + " GROUP BY channel, date ORDER BY date"
        )
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def clear_channel_daily(self, sources: list[str]) -> int:
        """删除指定来源的渠道行（重新采集前调用，避免改名后残留旧行）。"""
        if not sources:
            return 0
        placeholders = ",".join(["?"] * len(sources))
        with self._lock, self.conn:
            cur = self.conn.execute(
                f"DELETE FROM channel_daily WHERE source IN ({placeholders})", sources
            )
            return cur.rowcount

    def channel_summary(self, since=None, until=None) -> list[sqlite3.Row]:
        frag = ""
        args: list = []
        if since:
            frag += " AND date >= ?"
            args.append(since)
        if until:
            frag += " AND date <= ?"
            args.append(until)
        sql = (
            "SELECT channel, SUM(requests) AS requests, "
            "SUM(input_tokens) AS input_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
            "SUM(cache_write_tokens) AS cache_write_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(cost_usd) AS cost_usd "
            "FROM channel_daily WHERE 1=1" + frag
            + " GROUP BY channel ORDER BY cost_usd IS NULL, cost_usd DESC"
        )
        with self._lock:
            return self.conn.execute(sql, args).fetchall()
