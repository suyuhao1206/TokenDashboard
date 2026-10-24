"""FastAPI Web 面板（仅监听本机）。"""

from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import DEFAULT_CONFIG, ROOT, resource_dir, save_budget, ui_timezone
from .db import EXPORT_LIMIT, Store
from .normalize import parse_iso
from .periods import previous_period
from .scan import run_scan, start_scanner

UI_DIR = resource_dir() / "ui"
APP_ID = "token-dashboard"

# CSV 公式注入防护：Excel 会把以这些字符开头的单元格当公式执行。
# 只处理文本列，数字列保持原样（否则会被写成文本、无法求和）。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(v) -> str:
    """文本单元格转义：以公式字符开头时前置单引号，让 Excel 按文本解析。"""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in _FORMULA_PREFIXES else s


def create_app(cfg: dict, log=None) -> FastAPI:
    """log: 可选的日志回调（无控制台的浮窗进程用它把错误写进文件）。"""
    scanner_cfg = cfg.get("scanner") or {}
    interval_min = int(scanner_cfg.get("interval_minutes", 3))
    budget_cfg = cfg.get("budget") or {}
    # 用 dict 而不是局部变量：POST /api/config/budget 改完要立即生效（不重启）
    budget_state = {"monthly_usd": budget_cfg.get("monthly_usd")}

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
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        today = datetime.now(tz).date()
        d7 = (today - timedelta(days=6)).isoformat()
        d30 = (today - timedelta(days=29)).isoformat()
        month_start = today.replace(day=1).isoformat()
        # B8：环比数据源统一到后端。「今日 vs 昨日」「近 7 天 vs 前 7 天」的对比窗口
        # 之前由前端拿浏览器本地时间自己推，与卡片主值（遵循 ui.timezone）可能跨天错位；
        # 现在两段窗口都在这里按同一时区算好，前端只负责显示。
        yesterday = (today - timedelta(days=1)).isoformat()
        prev7_start = (today - timedelta(days=13)).isoformat()
        prev7_end = (today - timedelta(days=7)).isoformat()
        # 所有 totals 都带 model：否则选了模型后 KPI / 所选范围仍是全模型总量，
        # 与同屏的图表、请求表对不上账。by_harness 保持全量语义（它本身就是分布）。
        cur = store.totals(since=since, until=until, harness=harness, model=model)
        # A1：环比窗口在后端算，前端只负责显示。两段严格等长且不重叠，
        # 当前值与对比值都走 totals()，「较前一周期」与卡片主值因此严格同源。
        window = previous_period(since, until, today)
        prev = (
            store.totals(since=window[0], until=window[1], harness=harness, model=model)
            if window else None
        )
        return {
            "all": store.totals(harness=harness, model=model),
            "range": cur,
            "prev_range": prev,
            "prev_window": {"since": window[0], "until": window[1]} if window else None,
            "today": store.totals(since=today.isoformat(), harness=harness, model=model),
            "yesterday": store.totals(since=yesterday, until=yesterday,
                                      harness=harness, model=model),
            "last7d": store.totals(since=d7, harness=harness, model=model),
            "prev7d": store.totals(since=prev7_start, until=prev7_end,
                                   harness=harness, model=model),
            "last30d": store.totals(since=d30, harness=harness, model=model),
            "by_harness": [dict(r) for r in store.by_harness()],
            "timezone": str(getattr(tz, "key", tz)),
            # 预算提醒（B3）：本月 1 号至今的**全量**成本（不带 harness / model 筛选）。
            # 预算是对整个账单设置的，若跟随下拉框筛选，选一个小模型就会让超支告警
            # 凭空消失，预算 chip 与「累计成本」卡也对不上账。
            "month": store.totals(since=month_start),
            "budget": {"monthly_usd": budget_state["monthly_usd"]},
            # 未定价模型清单：前端 chip 展开后给每个模型一键复制价格模板
            "unpriced_models": [dict(r) for r in store.unpriced_models()],
        }

    @app.get("/api/config")
    def get_config():
        """面板「设置预算」用：当前预算 + 实际写回的配置文件路径（便于排查）。"""
        return {
            "budget": {"monthly_usd": budget_state["monthly_usd"]},
            "config_path": str(cfg.get("_config_path") or DEFAULT_CONFIG),
        }

    @app.post("/api/config/budget")
    def set_budget(payload: dict = Body(...)):
        """只改 budget.monthly_usd 一个键，写回 config/config.yaml 并更新内存。

        传 null / 0 / 负数表示关闭预算提醒。返回写入后的值与配置文件路径。
        """
        raw = (payload or {}).get("monthly_usd")
        value: float | None
        if raw is None or raw == "":
            value = None
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="monthly_usd 必须是数字")
            if value <= 0:
                value = None
            else:
                value = round(value, 2)
        try:
            path = save_budget(cfg, value)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"写入配置失败：{exc}") from exc
        budget_state["monthly_usd"] = value
        return {"monthly_usd": value, "config_path": str(path)}

    @app.get("/api/version")
    def version():
        """端口身份校验用：widget 复用 8787 前先确认对方真的是本工具。"""
        return {"app": APP_ID, "version": __version__}

    @app.post("/api/pricing/update")
    def update_pricing_api():
        """面板「未定价」面板的一键更新：下载最新 LiteLLM 价格表并全量重算成本。

        与 CLI 的 `token-dashboard update-pricing` 同一套逻辑（U5）：
        出现未定价模型时用户不必离开面板去敲命令行，点一下按钮即可闭环。
        """
        from .cli import _reprice
        from .pricing import Pricing

        try:
            path = Pricing(ROOT).update_from_network()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"下载价格表失败：{exc}") from exc
        overrides = (cfg.get("pricing") or {}).get("overrides_file")
        priced, cleared = _reprice(store, Pricing(ROOT, overrides))
        return {"path": path, "priced": priced, "cleared": cleared}

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
        session_id: str | None = None,
    ):
        if dim == "total":
            return [
                dict(r)
                for r in store.daily_report(
                    since=since, until=until, harness=harness, model=model, session_id=session_id
                )
            ]
        return [
            dict(r)
            for r in store.daily_by(
                dim, since=since, until=until, harness=harness, model=model, session_id=session_id
            )
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
        session_id: str | None = None,
        sort: str = "ts",
        order: str = "desc",
        format: str | None = None,
    ):
        if format == "csv":
            # 导出：不分页、带 UTF-8 BOM（Excel 直接双击打开不乱码）
            rows, truncated = store.export_requests(
                since=since, until=until, harness=harness, model=model,
                sort=sort, order=order, session_id=session_id,
            )
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                ["时间(UTC)", "本地时间", "日期", "Harness", "模型", "原始模型", "渠道", "项目",
                 "输入", "缓存读", "缓存写", "输出(含推理)", "其中推理", "合计", "成本USD", "会话"]
            )
            for r in rows:
                total = (
                    (r["input_tokens"] or 0) + (r["cache_read_tokens"] or 0)
                    + (r["cache_write_tokens"] or 0) + (r["output_tokens"] or 0)
                )
                # 本地时间：与面板请求表显示一致（UTC 列保留，便于和原始日志对账）
                dt = parse_iso(r["ts"])
                local_ts = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if dt else ""
                writer.writerow([
                    _csv_text(r["ts"]), local_ts, _csv_text(r["local_date"] or ""),
                    _csv_text(r["harness"] or ""), _csv_text(r["model"] or ""),
                    _csv_text(r["raw_model"] or ""), _csv_text(r["channel"] or ""),
                    _csv_text(r["project"] or ""),
                    r["input_tokens"] or 0, r["cache_read_tokens"] or 0,
                    r["cache_write_tokens"] or 0, r["output_tokens"] or 0,
                    r["reasoning_tokens"] or 0, total,
                    "" if r["cost_usd"] is None else f"{r['cost_usd']:.6f}",
                    _csv_text(r["session_id"] or ""),
                ])
            stamp = datetime.now(tz).strftime("%Y%m%d-%H%M")
            # 文件名带上当前筛选范围，导出多份时能区分。只给了一端时用
            # from/to 前缀，避免和末尾的时间戳日期看起来重复。
            scope = ""
            if since and until:
                scope = f"-{since.replace('-', '')}-{until.replace('-', '')}"
            elif since:
                scope = f"-from{since.replace('-', '')}"
            elif until:
                scope = f"-to{until.replace('-', '')}"
            headers = {
                "Content-Disposition": f'attachment; filename="token-dashboard{scope}-{stamp}.csv"',
                # 前端据此提示「已截断，请先加筛选再导出」
                "X-Export-Limit": str(EXPORT_LIMIT),
            }
            if truncated:
                headers["X-Export-Truncated"] = "1"
            return Response(
                content=("\ufeff" + buf.getvalue()).encode("utf-8"),
                media_type="text/csv; charset=utf-8",
                headers=headers,
            )
        return store.recent_requests(
            page=page, size=size, since=since, until=until,
            harness=harness, model=model, sort=sort, order=order, session_id=session_id,
        )

    @app.get("/api/hourly")
    def hourly(
        harness: str | None = None,
        model: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        # ts 存的是 UTC ISO 文本，SQL 里按 UTC 小时聚合后再平移到面板时区。
        # 口径说明（A7）：offset_h 用的是**当前时刻**的 UTC 偏移，因此
        # - 固定偏移时区（Asia/Shanghai +8 等）：历史数据完全精确；
        # - 有夏令时的时区（America/New_York 等）：跨越 DST 切换的历史小时会偏 1 小时。
        # 要做到逐条精确，得把每个 ts 转成 datetime 再 astimezone(tz)（按事件各自
        # 的偏移归桶），收益与成本不匹配，这里保持「按当前偏移平移」并在 README 注明。
        # 半/刻钟偏移的时区（Asia/Kolkata +5:30）会取整到小时。
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
