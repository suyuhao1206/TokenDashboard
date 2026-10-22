"""命令行入口：scan / report / update-pricing / serve。"""

from __future__ import annotations

import argparse
import json

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
    print(
        f"{'日期':<12}{'输入':>13}{'缓存读':>15}{'缓存写':>13}{'输出含推理':>14}{'其中推理':>11}{'合计':>15}{'成本USD':>10}{'未定价':>7}"
    )
    for r in rows:
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
        cost_s = f"{cost:.4f}" if cost is not None else "-"
        print(
            f"{r['date']:<12}{r['input_tokens'] or 0:>13,}{r['cache_read_tokens'] or 0:>15,}"
            f"{r['cache_write_tokens'] or 0:>13,}{r['output_tokens'] or 0:>12,}"
            f"{r['reasoning_tokens'] or 0:>9,}{total:>15,}{cost_s:>10}{r['unpriced'] or 0:>7}"
        )
    return 0


def _reprice(store: Store, pricing: Pricing) -> tuple[int, int]:
    """重算成本。返回 (已定价条数, 置空条数)。

    算不出成本的行必须把 cost_usd 置 NULL：否则模型从价格表消失后旧成本会残留，
    报表里「成本合计」和「未定价事件」互相矛盾。
    """
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


def main(argv=None) -> int:
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

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
