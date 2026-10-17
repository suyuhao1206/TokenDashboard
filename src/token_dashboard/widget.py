"""桌面小组件（Windows 无边框浮窗，支持皮肤 / 透明度 / 点击穿透 / 设置面板）。

功能
- 显示：今日 / 近 7 天 / 近 30 天 / 全部 的 tokens + 成本，底部 7 日迷你柱状图 + 各 harness 用量
- 皮肤：背景图片（png/jpg/webp/gif）、动态背景（GIF/WebP 动图循环播放）、圆角、文字/强调色、透明度
- 交互：拖动移动 · 双击切置顶 · 右键菜单（刷新 / 立即扫描 / 设置 / 打开面板 / 置顶 / 退出）· Esc 退出
- 快捷键：Ctrl+Alt+S 打开设置；Ctrl+Alt+T 切换鼠标穿透（装饰模式，可再用快捷键切回）
- 数据：直读本地 SQLite（只读），不需要常驻面板服务；可选自动扫描（后台线程）
- 位置与外观保存在 config/widget.json；日志可写 `--log`

用法：
    uv run token-dashboard widget
    uv run token-dashboard widget --self-test        # 自检（含皮肤加载）
"""

from __future__ import annotations

import ctypes
import json
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import colorchooser, filedialog

from .config import ROOT, ui_timezone
from .db import Store
from .pricing import Pricing
from .skins import MAGIC, load_frames

CFG_PATH = ROOT / "config" / "widget.json"
PANEL_W = 246
PANEL_H = 176

DEFAULTS: dict = {
    "x": None,
    "y": None,
    "topmost": True,
    "opacity": 0.94,
    "bg_image": "",
    "bg_animate": True,
    "bg_fit": "cover",
    "radius": 14,
    "text_color": "#e8eaf0",
    "accent": "#3b82f6",
    "muted_color": "#8b93a7",
    "font_scale": 1.0,
    "click_through": False,
    "interval": 60,
    "auto_scan": 15,
}

_FIT_LABELS = {"cover": "裁剪填满", "contain": "完整显示", "stretch": "拉伸铺满"}

# 设置窗口里的输入控件统一深色外观（tkinter 默认是白底，和深色面板格格不入）
_ENTRY_KW = {
    "bg": "#1b2029",
    "fg": "#e8eaf0",
    "insertbackground": "#e8eaf0",
    "relief": "flat",
    "highlightthickness": 1,
    "highlightbackground": "#2d3442",
    "highlightcolor": "#3b82f6",
}
_BTN_KW = {"bg": "#232a36", "fg": "#e8eaf0", "activebackground": "#2d3442",
           "activeforeground": "#ffffff", "relief": "flat", "bd": 0,
           "highlightthickness": 0, "padx": 10, "pady": 3}


def _blend(c1: str, c2: str, t: float) -> str:
    """按比例混合两个 #rrggbb 颜色（tkinter 画布没有透明度，用混色模拟层次）。"""

    def rgb(c: str) -> tuple[int, int, int]:
        c = (c or "").lstrip("#")
        if len(c) != 6:
            c = "000000"
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    a, b = rgb(c1), rgb(c2)
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _parse_int(raw: str, default: int, lo: int, hi: int) -> tuple[int | None, str]:
    """把输入框内容解析成范围内的整数；非法输入返回 (None, 提示文案)。"""
    s = (raw or "").strip()
    if s == "":
        return default, ""
    try:
        v = int(float(s))
    except ValueError:
        return None, f"“{s}” 不是数字"
    if v < lo or v > hi:
        return None, f"{v} 超出范围（{lo} ~ {hi}）"
    return v, ""


def _fmt(n) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def _money(v) -> str:
    return "-" if v is None else f"${v:,.2f}"


class Widget:
    def __init__(self, cfg: dict, self_test: bool = False, log_path: str | None = None):
        self.cfg = dict(DEFAULTS)
        self.cfg.update(self._load_saved())
        self.self_test = self_test
        self.log_file = Path(log_path) if log_path else None
        self.stop = threading.Event()
        self.dirty = threading.Event()
        self._app_cfg = cfg
        self._db_path = cfg["storage"]["db_path"]
        self.store = None
        self.store_error = None
        self._connect_store()
        self.tz = ui_timezone(cfg)

        self.root = tk.Tk()
        self.root.title("Token Dashboard Widget")
        self.root.overrideredirect(True)
        self.root.configure(bg=MAGIC)
        try:
            self.root.wm_attributes("-transparentcolor", MAGIC)
        except tk.TclError:
            pass
        self.root.geometry(self._initial_geometry())

        self.canvas = tk.Canvas(self.root, width=PANEL_W, height=PANEL_H, bg=MAGIC,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._bg_frames: list = []       # PhotoImage 列表
        self._bg_durations: list[int] = []
        self._bg_index = 0
        self._anim_gen = 0               # 动画链代数：重建背景时递增，作废旧回调
        self._anim_after = None          # 当前待触发的动画 after 句柄
        self._settings_win = None
        self._hot_t = self._hot_s = False
        self._drag = None

        self._build_menu()
        self._bind_events()
        self._apply_alpha()
        self.root.attributes("-topmost", bool(self.cfg.get("topmost", True)))
        self._build_background()
        self._build_text_items()
        if self.cfg.get("click_through"):
            self._set_click_through(True)
        self._poll_keys()
        self.refresh()

    # ---------- 数据库连接 ----------
    def _connect_store(self) -> bool:
        """连上只读库。库还不存在（从没跑过 scan）不是致命错误，给提示即可。"""
        try:
            if self.store is not None:
                self.store.close()
            self.store = Store(self._db_path, read_only=True)
            self.store_error = None
            return True
        except Exception as exc:
            self.store = None
            self.store_error = str(exc)
            self._log(f"只读打开数据库失败：{exc}")
            return False

    # ---------- 窗口/几何 ----------
    def _initial_geometry(self) -> str:
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            x = self.root.winfo_screenwidth() - PANEL_W - 28
            y = 64
        return f"{PANEL_W}x{PANEL_H}+{int(x)}+{int(y)}"

    def _apply_alpha(self) -> None:
        try:
            self.root.attributes("-alpha", float(self.cfg.get("opacity", 0.94)))
        except tk.TclError:
            pass

    def _scale(self, px: int) -> int:
        return max(7, int(round(px * float(self.cfg.get("font_scale", 1.0)))))

    # ---------- 背景（皮肤） ----------
    def _cancel_anim(self) -> None:
        """取消尚未触发的动画回调（after 不会因为重建背景而自动失效）。"""
        if self._anim_after is not None:
            try:
                self.root.after_cancel(self._anim_after)
            except Exception:
                pass
            self._anim_after = None

    def _build_background(self) -> None:
        self._anim_gen += 1              # 作废旧的动画链，避免多次应用设置后叠加
        gen = self._anim_gen
        self._cancel_anim()
        self.canvas.delete("bg")
        self.canvas.delete("card")
        self._bg_frames, self._bg_durations = [], []
        self._bg_animated = False
        self._bg_index = 0
        path = (self.cfg.get("bg_image") or "").strip()
        loaded = False
        if path and Path(path).exists():
            try:
                from PIL import ImageTk

                frames, durations, animated = load_frames(
                    path, PANEL_W, PANEL_H,
                    radius=int(self.cfg.get("radius", 14)),
                    fit=str(self.cfg.get("bg_fit", "cover")),
                    animate=bool(self.cfg.get("bg_animate", True)),
                )
                self._bg_frames = [ImageTk.PhotoImage(f) for f in frames]
                self._bg_durations = durations
                self._bg_animated = animated
                self._bg_item = self.canvas.create_image(0, 0, anchor="nw",
                                                         image=self._bg_frames[0], tags="bg")
                self.canvas.tag_lower("bg")
                loaded = True
                self._log(f"皮肤已加载：{path}（{len(self._bg_frames)} 帧，动图={animated}）")
            except Exception:
                self._log("皮肤加载失败:\n" + traceback.format_exc())
        if not loaded:
            self._draw_card()
        if loaded and getattr(self, "_bg_animated", False) and len(self._bg_frames) > 1:
            self._animate_bg(gen)
        else:
            self._raise_text()

    def _draw_card(self) -> None:
        """默认卡片：外层描边（强调色混合）+ 内层底色 + 顶部高光线，做出浮起层次。"""
        r = max(0, int(self.cfg.get("radius", 14)))
        accent = self.cfg.get("accent", "#3b82f6")
        bg = "#101319"
        self._round_rect(1, 1, PANEL_W - 1, PANEL_H - 1, r,
                         fill=_blend(bg, accent, 0.45), outline="", tags="card")
        self._round_rect(2, 2, PANEL_W - 2, PANEL_H - 2, max(0, r - 1),
                         fill=bg, outline="", tags="card")
        # 顶部高光：一条 1px 的浅色线，模拟环境光
        self.canvas.create_line(8 + r, 2, PANEL_W - 8 - r, 2,
                                fill=_blend(bg, "#ffffff", 0.10), tags="card")

    def _animate_bg(self, gen: int | None = None) -> None:
        if gen is None:
            gen = self._anim_gen
        if gen != self._anim_gen or not self._bg_frames or self.stop.is_set():
            self._anim_after = None
            return
        delay = self._bg_durations[self._bg_index % len(self._bg_durations)] or 100
        self._bg_index = (self._bg_index + 1) % len(self._bg_frames)
        try:
            self.canvas.itemconfigure("bg", image=self._bg_frames[self._bg_index])
            self._raise_text()
        except tk.TclError:
            self._anim_after = None
            return
        self._anim_after = self.root.after(max(30, int(delay)), lambda: self._animate_bg(gen))

    def _raise_text(self) -> None:
        for tag in ("t_title", "t_time", "t_rows", "t_bars", "t_harness", "t_hint"):
            self.canvas.tag_raise(tag)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    # ---------- 文字层 ----------
    def _build_text_items(self) -> None:
        tc = self.cfg.get("text_color", "#e8eaf0")
        mc = self.cfg.get("muted_color", "#8b93a7")
        accent = self.cfg.get("accent", "#3b82f6")
        self.canvas.delete("t_title", "t_time", "t_rows", "t_bars", "t_harness", "t_hint")
        self.canvas.create_text(12, 11, anchor="w", text="Token", fill=tc,
                                font=("Segoe UI", self._scale(10), "bold"), tags="t_title")
        self.canvas.create_text(PANEL_W - 12, 11, anchor="e", text="", fill=mc,
                                font=("Segoe UI", self._scale(8)), tags="t_time")
        self._row_items = {}
        labels = (("today", "今日"), ("d7", "近 7 天"), ("d30", "近 30 天"), ("all", "全部"))
        for i, (key, label) in enumerate(labels):
            y = 38 + i * 22
            self.canvas.create_text(12, y, anchor="w", text=label, fill=mc,
                                    font=("Segoe UI", self._scale(9)), tags="t_rows")
            # 「今日」用强调色，形成视觉锚点
            val = self.canvas.create_text(PANEL_W - 74, y, anchor="e", text="-",
                                          fill=(accent if key == "today" else tc),
                                          font=("Consolas", self._scale(11), "bold" if key == "today" else "normal"),
                                          tags="t_rows")
            cost = self.canvas.create_text(PANEL_W - 12, y, anchor="e", text="-", fill=mc,
                                           font=("Consolas", self._scale(9)), tags="t_rows")
            self._row_items[key] = (val, cost)
        self.canvas.create_text(12, PANEL_H - 12, anchor="w", text="", fill=mc,
                                font=("Segoe UI", self._scale(8)), tags="t_harness")

    # ---------- 数据 ----------
    def refresh(self) -> None:
        if self.store is None and not self._connect_store():
            self._show_hint()
            return
        today = datetime.now(self.tz).date()
        since7 = (today - timedelta(days=6)).isoformat()
        try:
            t = self.store.totals(since=today.isoformat())
            d7 = self.store.totals(since=since7)
            d30 = self.store.totals(since=(today - timedelta(days=29)).isoformat())
            allt = self.store.totals()
            series = [dict(r) for r in self.store.daily_report(since=since7)]
            if len(series) < 2:
                series = [dict(r) for r in self.store.daily_report()][-7:]
            harness = [dict(r) for r in self.store.by_harness()]
        except Exception:
            self._log("refresh 失败:\n" + traceback.format_exc())
            self.canvas.itemconfigure("t_time", text="读取失败")
            return

        self.canvas.delete("t_hint")
        for key, data in (("today", t), ("d7", d7), ("d30", d30), ("all", allt)):
            val, cost = self._row_items[key]
            if not data.get("events"):
                self.canvas.itemconfigure(val, text="0")
                self.canvas.itemconfigure(cost, text="$0.00")
            else:
                self.canvas.itemconfigure(val, text=_fmt(data.get("tokens")))
                self.canvas.itemconfigure(cost, text=_money(data.get("cost")))
        self._draw_series(series)
        self.canvas.itemconfigure(
            "t_harness",
            text="  ".join(f"{h['harness'] or '-'} {_fmt(h['tokens'])}" for h in harness[:2]),
        )
        self.canvas.itemconfigure("t_time", text=datetime.now().strftime("%H:%M"))

        if self.self_test:
            self.root.update_idletasks()
            print("[self-test] 窗口:", self.root.geometry(),
                  "| alpha:", self.root.attributes("-alpha"),
                  "| 置顶:", self.root.attributes("-topmost"))
            print("[self-test] 画布元素:", len(self.canvas.find_all()),
                  "| 背景图:", len(self.canvas.find_withtag("bg")),
                  "| 圆角卡:", len(self.canvas.find_withtag("card")),
                  "| 文字项:", len(self.canvas.find_withtag("t_rows")))
            print("[self-test] 四行:", {k: (self.canvas.itemcget(v, "text"), self.canvas.itemcget(c, "text"))
                                       for k, (v, c) in self._row_items.items()})
            print("[self-test] 趋势条:", len(self.canvas.find_withtag("t_bars")),
                  "| harness:", self.canvas.itemcget("t_harness", "text"))
            print("[self-test] 今日:", t, "| 全部:", allt)
            print("[self-test] 数据库:", self._db_path, "| 只读连接:", self.store is not None)
            try:
                self.open_settings()
                self.root.update_idletasks()
                wins = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
                print("[self-test] 设置窗口:", len(wins), "个")
                self.open_settings()   # 单例检查：应当不会新增窗口
                self.root.update_idletasks()
                wins2 = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
                print("[self-test] 重复打开设置窗口后:", len(wins2), "个")
                for w in wins2:
                    w.destroy()
                self._settings_win = None
            except Exception:
                print("[self-test] 设置窗口创建失败:\n" + traceback.format_exc())

    def _show_hint(self) -> None:
        """数据库还不存在（从没跑过 scan）时给出可读提示，而不是崩掉窗口。"""
        self.canvas.delete("t_hint")
        self._draw_series([])
        for val, cost in self._row_items.values():
            self.canvas.itemconfigure(val, text="-")
            self.canvas.itemconfigure(cost, text="-")
        self.canvas.itemconfigure("t_harness", text="")
        self.canvas.itemconfigure("t_time", text="无数据")
        tc = self.cfg.get("text_color", "#e8eaf0")
        mc = self.cfg.get("muted_color", "#8b93a7")
        self.canvas.create_text(PANEL_W / 2, PANEL_H / 2 - 12, text="还没有采集数据",
                                fill=tc, font=("Segoe UI", self._scale(10), "bold"), tags="t_hint")
        self.canvas.create_text(PANEL_W / 2, PANEL_H / 2 + 8,
                                text="右键「立即扫描」或运行 scan",
                                fill=mc, font=("Segoe UI", self._scale(8)), tags="t_hint")
        self.canvas.tag_raise("t_hint")
        if self.self_test:
            self.root.update_idletasks()
            print("[self-test] 无数据库提示:", self.store_error,
                  "| 窗口:", self.root.geometry(),
                  "| 提示元素:", len(self.canvas.find_withtag("t_hint")))

    def _draw_series(self, rows: list[dict]) -> None:
        self.canvas.delete("t_bars")
        if not rows:
            return
        x0, x1 = 12, PANEL_W - 12
        top, bottom = PANEL_H - 44, PANEL_H - 20
        peak = max((r.get("tokens") or 0) for r in rows) or 1
        gap = 3
        bar_w = max(3, (x1 - x0 - gap * (len(rows) - 1)) / max(1, len(rows)))
        for i, r in enumerate(rows):
            tokens = r.get("tokens") or 0
            bh = max(1, int((tokens / peak) * (bottom - top)))
            bx = x0 + i * (bar_w + gap)
            color = self.cfg.get("accent", "#3b82f6") if i == len(rows) - 1 else "#3f4a60"
            self.canvas.create_rectangle(bx, bottom - bh, bx + bar_w - 1, bottom,
                                         fill=color, outline="", tags="t_bars")
        # 7 日平均线：虚线 + 右侧小标注，比一串等宽柱子信息量大
        avg = sum((r.get("tokens") or 0) for r in rows) / len(rows)
        ay = bottom - int((avg / peak) * (bottom - top))
        self.canvas.create_line(x0, ay, x1, ay, fill=self.cfg.get("muted_color", "#8b93a7"),
                                dash=(3, 3), tags="t_bars")
        self.canvas.create_text(x1, ay - 7, anchor="e", text=f"均 {_fmt(avg)}",
                                fill=self.cfg.get("muted_color", "#8b93a7"),
                                font=("Segoe UI", self._scale(7)), tags="t_bars")
        self._raise_text()

    # ---------- 自动扫描 ----------
    def _do_scan(self) -> None:
        rw = None
        try:
            from . import collectors

            rw = Store(self.cfg_storage_db())
            pricing = Pricing(ROOT, (self._app_cfg.get("pricing") or {}).get("overrides_file"))
            summary = []
            for name, mod in collectors.ALL.items():
                c = (self._app_cfg.get("collectors") or {}).get(name) or {}
                if c.get("enabled", True):
                    st = mod.scan(rw, c, pricing, self.tz)
                    summary.append(f"{name}:{st['events']}")
            cc = (self._app_cfg.get("collectors") or {}).get("cc_switch") or {}
            if cc.get("enabled", True):
                st = collectors.CHANNEL["cc_switch"].scan(rw, cc, pricing, self.tz)
                summary.append(f"cc_switch:{st['rows']}")
            self._log("扫描完成 " + " ".join(summary))
            self.dirty.set()
        except Exception:
            self._log("扫描失败:\n" + traceback.format_exc())
        finally:
            if rw is not None:
                rw.close()

    def _scan_loop(self) -> None:
        minutes = int(self.cfg.get("auto_scan", 15))
        if minutes <= 0:
            return
        self._do_scan()
        while not self.stop.wait(minutes * 60):
            self._do_scan()

    def _tick(self) -> None:
        self.refresh()
        self.root.after(max(5, int(self.cfg.get("interval", 60))) * 1000, self._tick)

    def _poll_dirty(self) -> None:
        if self.dirty.is_set():
            self.dirty.clear()
            self.refresh()
        self.root.after(2000, self._poll_dirty)

    # ---------- 交互 ----------
    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg="#171a21", fg="#e8eaf0",
                            activebackground="#3b82f6", activeforeground="#ffffff", bd=0)
        self.menu.add_command(label="刷新", command=self.refresh)
        self.menu.add_command(label="立即扫描",
                              command=lambda: threading.Thread(target=self._do_scan, daemon=True).start())
        self.menu.add_command(label="设置…（Ctrl+Alt+S）", command=self.open_settings)
        self.menu.add_separator()
        self.menu.add_command(label="打开面板", command=lambda: webbrowser.open("http://127.0.0.1:8787"))
        self.top_var = tk.BooleanVar(value=bool(self.cfg.get("topmost", True)))
        self.menu.add_checkbutton(label="置顶", variable=self.top_var, command=self._apply_top)
        self.ct_var = tk.BooleanVar(value=bool(self.cfg.get("click_through", False)))
        self.menu.add_checkbutton(label="鼠标穿透（Ctrl+Alt+T）", variable=self.ct_var,
                                  command=lambda: self._set_click_through(self.ct_var.get()))
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._quit)

    def _bind_events(self) -> None:
        for widget in (self.root, self.canvas):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)
            widget.bind("<Double-Button-1>", lambda _e: self._toggle_top())
            widget.bind("<Button-3>", self._popup)
        self.root.bind("<Escape>", lambda _e: self._quit())

    def _start_drag(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag(self, event) -> None:
        if self._drag:
            dx, dy = self._drag
            self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _end_drag(self, _event) -> None:
        self._drag = None

    def _toggle_top(self) -> None:
        self.top_var.set(not self.top_var.get())
        self._apply_top()

    def _apply_top(self) -> None:
        self.cfg["topmost"] = bool(self.top_var.get())
        self.root.attributes("-topmost", self.cfg["topmost"])
        self._save_cfg()

    def _popup(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _quit(self) -> None:
        self.stop.set()
        self._save_pos()
        self.root.destroy()

    # ---------- 点击穿透 / 全局快捷键 ----------
    def _set_click_through(self, on: bool) -> None:
        self.cfg["click_through"] = bool(on)
        if self.ct_var.get() != bool(on):
            self.ct_var.set(bool(on))
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            self._log("点击穿透设置失败:\n" + traceback.format_exc())
        self._save_cfg()

    def _poll_keys(self) -> None:
        try:
            u = ctypes.windll.user32
            ctrl = bool(u.GetAsyncKeyState(0x11) & 0x8000)
            alt = bool(u.GetAsyncKeyState(0x12) & 0x8000)
            t = bool(u.GetAsyncKeyState(0x54) & 0x8000)
            s = bool(u.GetAsyncKeyState(0x53) & 0x8000)
            if ctrl and alt and t and not self._hot_t:
                self._set_click_through(not self.cfg.get("click_through"))
            if ctrl and alt and s and not self._hot_s:
                self.open_settings()
            self._hot_t, self._hot_s = t, s
        except Exception:
            pass
        self.root.after(250, self._poll_keys)

    # ---------- 设置面板 ----------
    def open_settings(self) -> None:
        # 单例：连按 Ctrl+Alt+S 不应该开出一堆窗口
        if self._settings_win is not None:
            try:
                if self._settings_win.winfo_exists():
                    self._settings_win.deiconify()
                    self._settings_win.lift()
                    self._settings_win.focus_force()
                    return
            except tk.TclError:
                pass
            self._settings_win = None

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("小组件设置")
        win.configure(bg="#101319")
        win.attributes("-topmost", True)

        def on_close():
            self._settings_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        def row(text, widget_factory, r):
            tk.Label(win, text=text, bg="#101319", fg="#8b93a7", font=("Segoe UI", 9),
                     anchor="w").grid(row=r, column=0, sticky="w", padx=10, pady=5)
            w = widget_factory()
            w.grid(row=r, column=1, sticky="we", padx=10, pady=5)
            return w

        scale_kw = {"orient": "horizontal", "length": 180, "bg": "#101319", "fg": "#e8eaf0",
                    "highlightthickness": 0, "troughcolor": "#262b36",
                    "activebackground": "#3b82f6"}
        check_kw = {"bg": "#101319", "fg": "#e8eaf0", "selectcolor": "#1b2029",
                    "activebackground": "#101319", "activeforeground": "#ffffff",
                    "highlightthickness": 0, "bd": 0}

        win.columnconfigure(1, weight=1)
        s_opacity = row("透明度", lambda: tk.Scale(win, from_=0.3, to=1.0, resolution=0.01,
                                                   **scale_kw), 0)
        s_opacity.set(float(self.cfg.get("opacity", 0.94)))
        s_radius = row("圆角", lambda: tk.Scale(win, from_=0, to=28, resolution=1,
                                                **scale_kw), 1)
        s_radius.set(int(self.cfg.get("radius", 14)))
        s_font = row("字号倍率", lambda: tk.Scale(win, from_=0.8, to=1.6, resolution=0.05,
                                                  **scale_kw), 2)
        s_font.set(float(self.cfg.get("font_scale", 1.0)))

        bg_var = tk.StringVar(value=self.cfg.get("bg_image") or "")
        tk.Label(win, text="背景图片", bg="#101319", fg="#8b93a7", font=("Segoe UI", 9),
                 anchor="w").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        frm_bg = tk.Frame(win, bg="#101319")
        frm_bg.grid(row=3, column=1, sticky="we", padx=10, pady=5)
        lbl_bg = tk.Label(frm_bg, text=(Path(bg_var.get()).name if bg_var.get() else "（无）"),
                          bg="#101319", fg="#e8eaf0", font=("Segoe UI", 8), anchor="w")
        lbl_bg.pack(side="left", fill="x", expand=True)

        def choose_bg():
            p = filedialog.askopenfilename(
                title="选择背景图片（png/jpg/webp/gif）",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("全部", "*.*")])
            if p:
                bg_var.set(p)
                lbl_bg.config(text=Path(p).name)

        def clear_bg():
            bg_var.set("")
            lbl_bg.config(text="（无）")

        tk.Button(frm_bg, text="选择…", command=choose_bg, **_BTN_KW).pack(side="right")
        tk.Button(frm_bg, text="清除", command=clear_bg, **_BTN_KW).pack(side="right", padx=4)

        anim_var = tk.BooleanVar(value=bool(self.cfg.get("bg_animate", True)))
        row("动图播放", lambda: tk.Checkbutton(win, variable=anim_var, **check_kw), 4)
        fit_var = tk.StringVar(value=self.cfg.get("bg_fit", "cover"))
        row("缩放方式", lambda: tk.OptionMenu(win, fit_var, *_FIT_LABELS.keys()), 5)
        ct_var = tk.BooleanVar(value=bool(self.cfg.get("click_through", False)))
        row("鼠标穿透", lambda: tk.Checkbutton(win, variable=ct_var, **check_kw), 6)
        i_var = tk.StringVar(value=str(self.cfg.get("interval", 60)))
        e_interval = row("刷新间隔(秒)", lambda: tk.Entry(win, textvariable=i_var, width=8,
                                                         **_ENTRY_KW), 7)
        a_var = tk.StringVar(value=str(self.cfg.get("auto_scan", 15)))
        e_auto = row("自动扫描(分)", lambda: tk.Entry(win, textvariable=a_var, width=8,
                                                     **_ENTRY_KW), 8)

        tk.Label(win, text="提示：Ctrl+Alt+T 切换穿透，Ctrl+Alt+S 打开本窗口；穿透后仍可用快捷键切回。",
                 bg="#101319", fg="#5c6478", font=("Segoe UI", 8), wraplength=300,
                 justify="left").grid(row=9, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))
        err_lbl = tk.Label(win, text="", bg="#101319", fg="#f87171", font=("Segoe UI", 8),
                           wraplength=300, justify="left")
        err_lbl.grid(row=10, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))

        def mark(entry, bad: bool) -> None:
            entry.config(highlightbackground="#f87171" if bad else "#2d3442")

        def apply_and_close():
            # 数字输入必须校验：直接 int() 遇到字母会抛 ValueError，把整个回调炸掉
            interval, err1 = _parse_int(i_var.get(), 60, 5, 86400)
            auto, err2 = _parse_int(a_var.get(), 0, 0, 1440)
            mark(e_interval, bool(err1))
            mark(e_auto, bool(err2))
            errs = [f"刷新间隔：{err1}" if err1 else "", f"自动扫描：{err2}" if err2 else ""]
            msg = "；".join(e for e in errs if e)
            if msg:
                err_lbl.config(text="输入有误 —— " + msg + "（请填数字后重试）")
                return
            err_lbl.config(text="")
            self.cfg.update({
                "opacity": float(s_opacity.get()),
                "radius": int(s_radius.get()),
                "font_scale": float(s_font.get()),
                "bg_image": bg_var.get(),
                "bg_animate": bool(anim_var.get()),
                "bg_fit": fit_var.get(),
                "click_through": bool(ct_var.get()),
                "interval": interval,
                "auto_scan": auto,
            })
            self._apply_all()
            on_close()

        frm_btn = tk.Frame(win, bg="#101319")
        frm_btn.grid(row=11, column=0, columnspan=2, sticky="e", padx=10, pady=(0, 10))
        tk.Button(frm_btn, text="保存并应用", command=apply_and_close,
                  bg="#1d4ed8", fg="#ffffff", activebackground="#2563eb",
                  activeforeground="#ffffff", relief="flat", bd=0,
                  highlightthickness=0, padx=12, pady=4).pack(side="right")
        tk.Button(frm_btn, text="取消", command=on_close, **_BTN_KW).pack(side="right", padx=6)

        # 位置：优先放主窗左侧；贴屏幕左边缘时改放右侧，并保证不越界
        win.update_idletasks()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = self.root.winfo_x() - ww - 12
        if x < 0:
            x = self.root.winfo_x() + PANEL_W + 12
        if x + ww > sw:
            x = max(0, sw - ww - 8)
        y = max(0, min(self.root.winfo_y(), sh - wh - 48))
        win.geometry(f"+{int(x)}+{int(y)}")

    def _apply_all(self) -> None:
        self._apply_alpha()
        self._build_background()
        self._build_text_items()
        self._set_click_through(bool(self.cfg.get("click_through")))
        self._save_cfg()
        self.refresh()
        self._log("设置已应用: " + json.dumps({k: self.cfg[k] for k in
                                             ("opacity", "radius", "font_scale", "bg_image",
                                              "bg_animate", "bg_fit", "click_through")},
                                             ensure_ascii=False))

    # ---------- 配置持久化 ----------
    def _load_saved(self) -> dict:
        try:
            data = json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cfg(self) -> None:
        try:
            CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            CFG_PATH.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _save_pos(self) -> None:
        self._save_cfg()

    # ---------- 日志 ----------
    def _log(self, msg: str) -> None:
        if not self.log_file:
            return
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass

    # ---------- 应用配置（采集器） ----------
    def cfg_storage_db(self):
        return self._app_cfg["storage"]["db_path"]


def run_widget(cfg: dict, interval: int | None = None, topmost: bool | None = None,
               auto_scan: int | None = None, self_test: bool = False,
               log_path: str | None = None) -> int:
    log = Path(log_path) if log_path else None

    def _write(msg: str) -> None:
        if not log:
            return
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass

    def _hook(exc_type, exc, tb):
        _write("未捕获异常:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _hook
    threading.excepthook = lambda args: _write(
        "线程异常:\n" + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    )

    try:
        widget = Widget(cfg, self_test=self_test, log_path=log_path)
    except Exception:
        _write("创建窗口失败:\n" + traceback.format_exc())
        print("无法创建窗口：\n" + traceback.format_exc())
        return 2

    if interval is not None:
        widget.cfg["interval"] = int(interval)
    if topmost is not None:
        widget.cfg["topmost"] = bool(topmost)
        widget.root.attributes("-topmost", bool(topmost))
    if auto_scan is not None:
        widget.cfg["auto_scan"] = int(auto_scan)
    _write(f"小组件启动 | 窗口 {widget.root.geometry()} | interval={widget.cfg['interval']}s "
           f"auto_scan={widget.cfg['auto_scan']}min | 皮肤={widget.cfg.get('bg_image') or '无'} "
           f"| db={widget._db_path}")
    if widget.store is None:
        _write(f"数据库不可用（会先提示，等自动扫描建库后自动重连）：{widget.store_error}")
    widget.refresh()
    _write("首次刷新完成")

    if self_test:
        widget.root.after(1200, widget.root.destroy)
    else:
        threading.Thread(target=widget._scan_loop, daemon=True).start()
        widget._tick()
        widget._poll_dirty()
    widget.root.mainloop()
    widget.stop.set()
    _write("小组件退出")
    return 0
