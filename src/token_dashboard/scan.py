"""扫描编排：CLI（打印）与 serve 后台任务（静默）共用同一套逻辑。

这里只负责"跑一轮全源扫描并返回统计"，**不做任何打印**——
打印是 CLI 的职责，后台线程打印会污染服务日志。
"""

from __future__ import annotations

import sys
import threading
import time
import traceback

from . import collectors
from .config import ROOT, ui_timezone
from .db import Store
from .pricing import Pricing

# 同一进程内只允许一个扫描在跑：手动刷新触发的扫描和定时扫描可能撞车，
# 两个 scan 同时读日志虽然幂等，但会白白翻倍 IO，还容易撞上 SQLite 写锁。
_scan_lock = threading.Lock()


def run_scan(cfg: dict) -> dict:
    """执行一轮全源扫描，返回 {source: stats}。

    每次新建 Store（新连接）：扫描结束连接即释放，不和 FastAPI 的请求连接共用，
    避免把长事务 / 线程亲和性问题带进服务进程。
    """
    with _scan_lock:
        store = Store(cfg["storage"]["db_path"])
        try:
            pricing = Pricing(ROOT, (cfg.get("pricing") or {}).get("overrides_file"))
            tz = ui_timezone(cfg)
            result: dict[str, dict] = {}

            # harness 维度：写 usage_events
            for name, mod in collectors.ALL.items():
                c = (cfg.get("collectors") or {}).get(name) or {}
                if c.get("enabled", True):
                    result[name] = mod.scan(store, c, pricing, tz)

            # 渠道维度：写 channel_daily（内置默认关闭，与 config._builtin_defaults 对齐）
            for name, mod in collectors.CHANNEL.items():
                c = (cfg.get("collectors") or {}).get(name) or {}
                if c.get("enabled", True):
                    result[name] = mod.scan(store, c, pricing, tz)

            return result
        finally:
            store.close()


def start_scanner(cfg: dict, interval_minutes: int, log=None) -> threading.Thread | None:
    """起一个守护线程，每 interval_minutes 分钟扫一轮。

    - 用线程而不是 asyncio task：扫描是同步阻塞 IO（读日志文件 + SQLite），
      放进 event loop 会卡住所有 API 响应。
    - daemon=True：不阻止进程退出，Ctrl+C 能干净关掉。
    - 先睡再扫：调用方（cmd_serve / widget）在启动时已经扫过一轮，立刻再扫纯属浪费。

    log: 可选的日志回调。无控制台启动（pythonw / TokenWidget.exe）时
    sys.stderr 是 None，`traceback.print_exc()` 会抛 AttributeError 把线程打死，
    所以异常必须走调用方给的日志函数，而不是直接 print。
    """
    if interval_minutes <= 0:
        return None

    def report(exc: BaseException) -> None:
        text = "扫描失败:\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if log is not None:
            log(text)
            return
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass  # stderr 为 None 的窗口进程里不能再炸一次

    def loop() -> None:
        while True:
            time.sleep(interval_minutes * 60)
            try:
                run_scan(cfg)
            except Exception as exc:  # 单轮失败不能让线程死掉
                report(exc)

    t = threading.Thread(target=loop, name="token-scanner", daemon=True)
    t.start()
    return t
