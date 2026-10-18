"""FastAPI Web 面板（仅监听本机）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import resource_dir, ui_timezone
from .db import Store

UI_DIR = resource_dir() / "ui"


def create_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="token-dashboard")
    store = Store(cfg["storage"]["db_path"])
    tz = ui_timezone(cfg)

    @app.get("/api/summary")
    def summary(
        harness: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        today = datetime.now(tz).date()
        d7 = (today - timedelta(days=6)).isoformat()
        d30 = (today - timedelta(days=29)).isoformat()
        return {
            "all": store.totals(harness=harness),
            "range": store.totals(since=since, until=until, harness=harness),
            "today": store.totals(since=today.isoformat(), harness=harness),
            "last7d": store.totals(since=d7, harness=harness),
            "last30d": store.totals(since=d30, harness=harness),
            "by_harness": [dict(r) for r in store.by_harness()],
            "timezone": str(getattr(tz, "key", tz)),
        }

    @app.get("/api/meta")
    def meta():
        return store.meta()

    @app.get("/api/daily")
    def daily(
        dim: str = Query("total"),
        harness: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        if dim == "total":
            return [dict(r) for r in store.daily_report(since=since, until=until, harness=harness)]
        return [dict(r) for r in store.daily_by(dim, since=since, until=until, harness=harness)]

    @app.get("/api/breakdown")
    def breakdown(
        dim: str = Query("model"),
        harness: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 15,
    ):
        return [
            dict(r)
            for r in store.breakdown(dim, since=since, until=until, harness=harness, limit=limit)
        ]

    @app.get("/api/sessions")
    def sessions(
        limit: int = 30,
        harness: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        return [
            dict(r)
            for r in store.sessions(limit=limit, since=since, until=until, harness=harness)
        ]

    @app.get("/api/channels")
    def channels(since: str | None = None, until: str | None = None):
        return {
            "summary": [dict(r) for r in store.channel_summary(since=since, until=until)],
            "daily": [dict(r) for r in store.channel_daily(since=since, until=until)],
        }

    @app.get("/api/requests")
    def requests(
        page: int = 1,
        size: int = 20,
        harness: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        return store.recent_requests(page=page, size=size, since=since, until=until, harness=harness)

    @app.get("/api/hourly")
    def hourly(
        harness: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        # ts 存的是 UTC ISO 文本，SQL 里按 UTC 小时聚合后再平移到面板时区。
        # 对整小时偏移的时区（如 Asia/Shanghai +8）精确；半/刻钟偏移的时区会取整。
        offset_h = round((datetime.now(tz).utcoffset() or timedelta()).total_seconds() / 3600)
        buckets = [{"hour": h, "events": 0, "tokens": 0} for h in range(24)]
        for r in store.hourly(since=since, until=until, harness=harness):
            local_h = (r["utc_hour"] + offset_h) % 24
            buckets[local_h]["events"] += r["events"]
            buckets[local_h]["tokens"] += r["tokens"] or 0
        return buckets

    @app.get("/")
    def index():
        return FileResponse(UI_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")
    return app


def run_server(cfg: dict, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    srv = cfg.get("server") or {}
    host = host or srv.get("host", "127.0.0.1")
    port = port or int(srv.get("port", 8787))
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")
