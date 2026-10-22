"""FastAPI Web 面板（仅监听本机）。"""

from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import resource_dir, ui_timezone
from .db import Store
from .scan import run_scan, start_scanner

UI_DIR = resource_dir() / "ui"
APP_ID = "token-dashboard"


def create_app(cfg: dict, log=None) -> FastAPI:
    """log: 可选的日志回调（无控制台的浮窗进程用它把错误写进文件）。"""
    scanner_cfg = cfg.get("scanner") or {}
    interval_min = int(scanner_cfg.get("interval_minutes", 3))
    budget_cfg = cfg.get("budget") or {}
    monthly_budget = budget_cfg.get("monthly_usd")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 后台定时扫描：面板自己保持数据新鲜，不需要用户再手动跑 scan。
        # interval_minutes <= 0 表示关闭（适合只想看历史数据的场景）。
        start_scanner(cfg, interval_min, log=log)
        yield

    app = FastAPI(title="token-dashboard", lifespan=lifespan)
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
        month_start = today.replace(day=1).isoformat()
        month = store.totals(since=month_start, harness=harness)
        return {
            "all": store.totals(harness=harness),
            "range": store.totals(since=since, until=until, harness=harness),
            "today": store.totals(since=today.isoformat(), harness=harness),
            "last7d": store.totals(since=d7, harness=harness),
            "last30d": store.totals(since=d30, harness=harness),
            "by_harness": [dict(r) for r in store.by_harness()],
            "timezone": str(getattr(tz, "key", tz)),
            # 预算提醒：本月 1 号至今的累计成本 + 配置的月度预算（未配置时为 null）
            "month": month,
            "budget": {"monthly_usd": monthly_budget},
        }

    @app.get("/api/version")
    def version():
        """端口身份校验用：widget 复用 8787 前先确认对方真的是本工具。"""
        return {"app": APP_ID, "version": __version__}

    @app.get("/api/meta")
    def meta():
        data = store.meta()
        data["version"] = __version__
        return data

    @app.get("/api/daily")
    def daily(
        dim: str = Query("total"),
        harness: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        if dim == "total":
            return [
                dict(r)
                for r in store.daily_report(since=since, until=until, harness=harness, model=model)
            ]
        return [
            dict(r)
            for r in store.daily_by(dim, since=since, until=until, harness=harness, model=model)
        ]

    @app.get("/api/breakdown")
    def breakdown(
        dim: str = Query("model"),
        harness: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 15,
    ):
        return [
            dict(r)
            for r in store.breakdown(
                dim, since=since, until=until, harness=harness, limit=limit, model=model
            )
        ]

    @app.get("/api/sessions")
    def sessions(
        limit: int = 30,
        harness: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        return [
            dict(r)
            for r in store.sessions(limit=limit, since=since, until=until, harness=harness, model=model)
        ]

    @app.get("/api/channels")
    def channels(
        since: str | None = None,
        until: str | None = None,
        channel: str | None = None,
    ):
        selected = [channel] if channel else None
        return {
            "summary": [dict(r) for r in store.channel_summary(since=since, until=until, channels=selected)],
            "daily": [dict(r) for r in store.channel_daily(since=since, until=until, channels=selected)],
            # 渠道 × 模型明细：前端据此做「按渠道筛选表格 + 模型分解」
            "by_model": [dict(r) for r in store.channel_daily_by_model(since=since, until=until, channels=selected)],
        }

    @app.get("/api/requests")
    def requests(
        page: int = 1,
        size: int = 20,
        harness: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
        sort: str = "ts",
        order: str = "desc",
        format: str | None = None,
    ):
        if format == "csv":
            # 导出：不分页、带 UTF-8 BOM（Excel 直接双击打开不乱码）
            rows = store.export_requests(
                since=since, until=until, harness=harness, model=model,
                sort=sort, order=order,
            )
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                ["时间(UTC)", "日期", "Harness", "模型", "原始模型", "渠道", "项目",
                 "输入", "缓存读", "缓存写", "输出(含推理)", "其中推理", "合计", "成本USD", "会话"]
            )
            for r in rows:
                total = (
                    (r["input_tokens"] or 0) + (r["cache_read_tokens"] or 0)
                    + (r["cache_write_tokens"] or 0) + (r["output_tokens"] or 0)
                )
                writer.writerow([
                    r["ts"], r["local_date"] or "", r["harness"] or "", r["model"] or "",
                    r["raw_model"] or "", r["channel"] or "", r["project"] or "",
                    r["input_tokens"] or 0, r["cache_read_tokens"] or 0,
                    r["cache_write_tokens"] or 0, r["output_tokens"] or 0,
                    r["reasoning_tokens"] or 0, total,
                    "" if r["cost_usd"] is None else f"{r['cost_usd']:.6f}",
                    r["session_id"] or "",
                ])
            stamp = datetime.now(tz).strftime("%Y%m%d-%H%M")
            return Response(
                content=("\ufeff" + buf.getvalue()).encode("utf-8"),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="token-dashboard-{stamp}.csv"'},
            )
        return store.recent_requests(
            page=page, size=size, since=since, until=until,
            harness=harness, model=model, sort=sort, order=order,
        )

    @app.get("/api/hourly")
    def hourly(
        harness: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        # ts 存的是 UTC ISO 文本，SQL 里按 UTC 小时聚合后再平移到面板时区。
        # 对整小时偏移的时区（如 Asia/Shanghai +8）精确；半/刻钟偏移的时区会取整。
        offset_h = round((datetime.now(tz).utcoffset() or timedelta()).total_seconds() / 3600)
        buckets = [{"hour": h, "events": 0, "tokens": 0} for h in range(24)]
        for r in store.hourly(since=since, until=until, harness=harness, model=model):
            local_h = (r["utc_hour"] + offset_h) % 24
            buckets[local_h]["events"] += r["events"]
            buckets[local_h]["tokens"] += r["tokens"] or 0
        return buckets

    @app.post("/api/scan")
    def scan_now():
        """手动触发一轮扫描（面板的"增量扫描"按钮用）。

        run_scan 内部有互斥锁，和定时线程撞车时这里会排队而不是并发扫。
        同步端点跑在 FastAPI 的线程池里，不会卡住 event loop。
        """
        started = datetime.now().isoformat(timespec="seconds")
        result = run_scan(cfg)
        events = sum(s.get("events", 0) for s in result.values())
        rows = sum(s.get("rows", 0) for s in result.values())
        errors = sum(s.get("errors", 0) for s in result.values())
        return {
            "started": started,
            "finished": datetime.now().isoformat(timespec="seconds"),
            "events": events,
            "channel_rows": rows,
            "errors": errors,
            "sources": {k: {kk: vv for kk, vv in v.items() if kk != "note"} for k, v in result.items()},
        }

    @app.get("/")
    def index():
        return FileResponse(UI_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")
    return app


def run_server(cfg: dict, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    srv = cfg.get("server") or {}
    host = host or srv.get("host", "127.0.0.1")
    # 不能写 `port or ...`：显式传 0（让系统分配随机端口）会被 or 吞掉
    port = port if port is not None else int(srv.get("port", 8787))
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")
