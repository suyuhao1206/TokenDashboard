"""命令行入口：scan / report / update-pricing / serve。"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from . import __version__
from .config import ROOT, load_config
from .db import Store
from .pricing import Pricing
from .scan import run_scan


def cmd_scan(args) -> int:
    cfg = load_config(args.config)
    result = run_scan(cfg)
    total = 0
    for name, stats in result.items():
        # note 里可能是「缺少 zstandard 依赖」这类关键提示，不能吞掉
        note = f" | {stats['note']}" if stats.get("note") else ""
        if "rows" in stats:  # 渠道采集器
            print(
                f"- {stats['source']}（渠道视图）: 入库 {stats['rows']} 行 | 渠道 {stats['channels']} 个"
                f"{note}"
            )
        else:
            total += stats["events"]
            print(
                f"- {stats['source']}: 入库 {stats['events']} 条 | 扫描 {stats['files']} 个文件"
                f" | 跳过(未变化) {stats['skipped']} | 解析错误 {stats['errors']}{note}"
            )
    print(f"完成：共处理 {total} 条事件（重复运行不会重复计数）")
    return 0


# 报表列宽：表头与数据行共用同一组常量。以前表头写死 `>14` / `>11`、
# 数据行写 `>12` / `>9`，两列各错位 2 个字符；共用常量杜绝再错位。
# 宽度是「终端显示宽度」而不是字符数——中文表头一个字占两列，用 len() 补空格
# 看起来还是歪的，所以统一走 _pad()。
REPORT_WIDTHS = (12, 13, 15, 13, 14, 11, 15, 10, 7)
REPORT_HEADERS = ("日期", "输入", "缓存读", "缓存写", "输出含推理",
                  "其中推理", "合计", "成本USD", "未定价")


def _disp_width(text: str) -> int:
    """终端显示宽度：东亚宽字符（中文/全角符号）按 2 列计。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int, align: str = ">") -> str:
    pad = " " * max(0, width - _disp_width(text))
    return pad + text if align == ">" else text + pad


def _report_header(widths=REPORT_WIDTHS) -> str:
    """表头对齐数据行：首列左对齐，其余右对齐（同 widths）。"""
    cells = [_pad(REPORT_HEADERS[0], widths[0], "<")]
    cells += [
        _pad(h, w) for h, w in zip(REPORT_HEADERS[1:], widths[1:], strict=True)
    ]
    # 中文表头占 2 列宽，按 widths 补齐后显示宽度就一致了；
    # 「输出含推理」这种 5 字标签需要 10 列，14 列的宽度够用。
    return "".join(cells)


def _report_cells(r) -> list[str]:
    """把一行数据格式化成与表头同序的字符串列表。"""
    # 注意：output_tokens 已含推理（对齐 OpenAI 口径），这里不能再加 reasoning_tokens
    total = sum(
        r[k] or 0
        for k in (
            "input_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "output_tokens",
        )
    )
    cost = r["cost_usd"]
    return [
        r["date"],
        f"{r['input_tokens'] or 0:,}",
        f"{r['cache_read_tokens'] or 0:,}",
        f"{r['cache_write_tokens'] or 0:,}",
        f"{r['output_tokens'] or 0:,}",
        f"{r['reasoning_tokens'] or 0:,}",
        f"{total:,}",
        f"{cost:.4f}" if cost is not None else "-",
        str(r["unpriced"] or 0),
    ]


def _column_widths(rows) -> tuple[int, ...]:
    """列宽 = max(表头显示宽度, 所有数据的显示宽度)，且不小于 REPORT_WIDTHS。

    A4 的核心是「表头和数据行用同一组宽度」。这里再往前一步：宽度按内容自适应，
    这样即使某天 tokens 超过 10 亿（13 位数字 > 12 列宽），也不会串到相邻列。
    REPORT_WIDTHS 作为下限，保证正常数据下列宽稳定、不随内容忽宽忽窄。
    """
    widths = list(REPORT_WIDTHS)
    for i, h in enumerate(REPORT_HEADERS):
        widths[i] = max(widths[i], _disp_width(h))
    for r in rows:
        for i, cell in enumerate(_report_cells(r)):
            widths[i] = max(widths[i], _disp_width(cell))
    return tuple(widths)


def cmd_report(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg["storage"]["db_path"])
    rows = store.daily_report(since=args.since, until=args.until)
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("暂无数据。先运行：token-dashboard scan")
        return 0
    widths = _column_widths(rows)
    print(_report_header(widths))
    for r in rows:
        cells = _report_cells(r)
        line = _pad(cells[0], widths[0], "<")
        line += "".join(_pad(c, w) for c, w in zip(cells[1:], widths[1:], strict=True))
        print(line)
    return 0


def _reprice(store: Store, pricing: Pricing) -> tuple[int, int]:
    """重算成本。返回 (已定价条数, 置空条数)。

    算不出成本的行必须把 cost_usd 置 NULL：否则模型从价格表消失后旧成本会残留，
    报表里「成本合计」和「未定价事件」互相矛盾。

    面板线程（/api 端点）可能同时在读写同一个库：Store 的所有访问都以 _lock 串行化，
    这里必须同样持锁——SELECT 拿快照和 UPDATE 写回都要在锁内完成。
    """
    with store._lock:
        rows = store.conn.execute(
            "SELECT id, model, input_tokens, cache_read_tokens, cache_write_tokens, "
            "output_tokens, reasoning_tokens FROM usage_events"
        ).fetchall()
        priced = cleared = 0
        with store.conn:
            for r in rows:
                c = pricing.cost(
                    r["model"],
                    r["input_tokens"],
                    r["cache_read_tokens"],
                    r["cache_write_tokens"],
                    r["output_tokens"],
                    r["reasoning_tokens"],
                )
                if c is not None:
                    store.conn.execute(
                        "UPDATE usage_events SET cost_usd=? WHERE id=?", (c, r["id"])
                    )
                    priced += 1
                else:
                    cleared += store.conn.execute(
                        "UPDATE usage_events SET cost_usd=NULL WHERE id=? AND cost_usd IS NOT NULL",
                        (r["id"],),
                    ).rowcount
    return priced, cleared


def cmd_update_pricing(args) -> int:
    cfg = load_config(args.config)
    path = Pricing(ROOT).update_from_network()
    print(f"价格表已更新：{path}")
    store = Store(cfg["storage"]["db_path"])
    priced, cleared = _reprice(store, Pricing(ROOT, (cfg.get("pricing") or {}).get("overrides_file")))
    print(f"已重算 {priced} 条事件成本，{cleared} 条无法定价已置为空")
    return 0


def cmd_serve(args) -> int:
    cfg = load_config(args.config)
    if not args.no_scan:
        cmd_scan(argparse.Namespace(config=args.config))
    from .server import run_server

    run_server(cfg, host=args.host, port=args.port)
    return 0


def cmd_widget(args) -> int:
    cfg = load_config(args.config)
    from .widget_web import run_widget

    return run_widget(cfg, debug=args.debug, tray=not args.no_tray, log_path=args.log)


def cmd_doctor(args) -> int:
    """环境自检：系统版本 / WebView2 / 数据库 / 采集路径。

    换机器或「双击没反应」时第一个该跑的命令。所有检查都只读，不改任何东西。
    """
    from .syscheck import check_webview2, check_windows, os_label, webview2_installed

    cfg = load_config(args.config)
    print(f"操作系统   : {os_label()}")
    print(f"Python     : {sys.version.split()[0]} ({'64 位' if sys.maxsize > 2**32 else '32 位'})")
    print(f"运行方式   : {'打包 exe' if getattr(sys, 'frozen', False) else '源码'}")

    problems: list[str] = []
    win_problem = check_windows()
    print(f"系统版本   : {'通过' if not win_problem else '不满足'}")
    if win_problem:
        problems.append(win_problem)

    has_wv = webview2_installed()
    print(f"WebView2   : {'已安装' if has_wv else '未安装（只影响浮窗）'}")
    if not has_wv:
        wv = check_webview2()
        if wv:
            problems.append(wv)

    db = cfg["storage"]["db_path"]
    db_ok = Path(db).exists()
    print(f"数据库     : {db} ({'存在' if db_ok else '不存在，先跑 scan'})")

    enabled = [
        name
        for name, c in (cfg.get("collectors") or {}).items()
        if isinstance(c, dict) and c.get("enabled", True)
    ]
    print(f"启用的采集器: {', '.join(enabled) if enabled else '（无）'}")
    for name in enabled:
        c = cfg["collectors"][name]
        for key in ("paths", "db_path"):
            val = c.get(key)
            if not val:
                continue
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                ok = Path(v).exists()
                print(f"  {name:12s} {key}={v} {'✓' if ok else '✗ 不存在'}")

    if problems:
        print("\n需要处理：")
        for p in problems:
            print("  - " + p.replace("\n", "\n    "))
        return 1
    print("\n环境检查通过。")
    return 0


def _utf8_console() -> None:
    """把 stdout/stderr 切成 UTF-8。

    报表、扫描汇总都是中文，而 Windows 上非中文区域（CI 的 windows-latest 就是
    cp1252）的默认控制台编码直接 UnicodeEncodeError —— `report` 会在打印表头时崩，
    发版流水线的冒烟测试正好会踩到。输出重定向到文件/管道时 reconfigure 同样有效。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv=None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(
        prog="token-dashboard", description="多渠道 × 多 Harness 的本地 Token 用量面板"
    )
    ap.add_argument("--version", action="version", version=f"token-dashboard {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("scan", help="扫描并入库本地日志")
    p.add_argument("--config")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report", help="日维度报表")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--json", action="store_true")
    p.add_argument("--config")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("update-pricing", help="下载 LiteLLM 价格表并重算成本")
    p.add_argument("--config")
    p.set_defaults(func=cmd_update_pricing)

    p = sub.add_parser("serve", help="启动 Web 面板")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--no-scan", action="store_true", help="启动前不自动扫描")
    p.add_argument("--config")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("widget", help="启动桌面小组件（浮窗）")
    p.add_argument("--debug", action="store_true", help="pywebview 调试模式（打开 DevTools）")
    p.add_argument("--no-tray", action="store_true", help="不创建系统托盘图标")
    p.add_argument("--log", help="日志文件（无控制台启动时排查问题用）")
    p.add_argument("--config")
    p.set_defaults(func=cmd_widget)

    p = sub.add_parser("doctor", help="环境自检：系统版本 / WebView2 / 数据库 / 采集路径")
    p.add_argument("--config")
    p.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
