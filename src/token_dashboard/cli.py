"""命令行入口：scan / report / update-pricing / serve。"""

from __future__ import annotations

import argparse
import json

from . import collectors
from .config import ROOT, load_config, ui_timezone
from .db import Store
from .pricing import Pricing


def cmd_scan(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg["storage"]["db_path"])
    pricing = Pricing(ROOT, (cfg.get("pricing") or {}).get("overrides_file"))
    tz = ui_timezone(cfg)
    total = 0
    for name, mod in collectors.ALL.items():
        c = (cfg.get("collectors") or {}).get(name) or {}
        if not c.get("enabled", True):
            print(f"- {name}: 已禁用，跳过")
            continue
        stats = mod.scan(store, c, pricing, tz)
        total += stats["events"]
        # note 里可能是「缺少 zstandard 依赖」这类关键提示，不能吞掉
        note = f" | {stats['note']}" if stats.get("note") else ""
        print(
            f"- {stats['source']}: 入库 {stats['events']} 条 | 扫描 {stats['files']} 个文件"
            f" | 跳过(未变化) {stats['skipped']} | 解析错误 {stats['errors']}{note}"
        )
    for name, mod in collectors.CHANNEL.items():
        c = (cfg.get("collectors") or {}).get(name) or {}
        # 与 config._builtin_defaults 保持一致（内置默认 enabled=True）
        if not c.get("enabled", True):
            continue
        st = mod.scan(store, c, pricing, tz)
        print(
            f"- {st['source']}（渠道视图）: 入库 {st['rows']} 行 | 渠道 {st['channels']} 个"
            f" | {st['note']}"
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
        f"{'日期':<12}{'输入':>13}{'缓存读':>15}{'缓存写':>13}{'输出':>12}{'推理':>9}{'合计':>15}{'成本USD':>10}{'未定价':>7}"
    )
    for r in rows:
        total = sum(
            r[k] or 0
            for k in (
                "input_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_tokens",
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
    from .widget import run_widget

    cfg = load_config(args.config)
    return run_widget(
        cfg,
        interval=args.interval,
        topmost=not args.no_topmost,
        auto_scan=args.auto_scan,
        self_test=args.self_test,
        log_path=args.log,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="token-dashboard", description="多渠道 × 多 Harness 的本地 Token 用量面板"
    )
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
    p.add_argument("--interval", type=int, default=60, help="刷新间隔（秒，默认 60）")
    p.add_argument("--auto-scan", type=int, default=15, help="自动扫描间隔（分钟，0=关闭）")
    p.add_argument("--no-topmost", action="store_true", help="不置顶")
    p.add_argument("--self-test", action="store_true", help="自检：取一次数后自动退出")
    p.add_argument("--log", help="把运行日志写到该文件（pythonw 无控制台时排查用）")
    p.add_argument("--config")
    p.set_defaults(func=cmd_widget)

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
