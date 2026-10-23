"""桌面浮窗（pywebview + WebView2）。

与面板共用同一套后端与设计系统：
- 同进程内嵌 uvicorn，浮窗和面板从此不分家（端口被占用时复用已有服务）
- 前端是 `/static/widget.html`，引用 `shared.css` / `shared.js`，视觉与面板同源

关于 Windows 中文路径（本项目实测踩到的坑）：
pythonnet（pywebview 的 WinForms / WebView2 后端依赖它）在 netfx 模式下用 ANSI 路径
加载程序集，路径含中文时 `Python.Runtime.dll` 与 `Microsoft.Web.WebView2.Core.dll`
都会加载失败（Failed to resolve ... / HRESULT 0x80131515）。
绕法：把这两个包复制到纯 ASCII 的临时目录，再把 `__file__` 指过去。
这是运行时的兼容层，只在检测到非 ASCII 路径时才启用。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .config import CONFIG_DIR, DATA_DIR, load_config
from .scan import run_scan

APP_NAME = "TokenDashboard"
WIDGET_DEFAULTS = {"width": 260, "height": 246, "on_top": True, "x": None, "y": None}
# 窗口最小尺寸：fit_viewport 的 clamp 与 create_window(min_size=) 必须用同一对值，
# 否则两处各写一套数字，改一处就会打架（原来 160x140 vs 180x160）。
MIN_WIDTH, MIN_HEIGHT = 180, 160

# WebView2 的用户数据目录。必须显式指定：
# pywebview 在 private_mode 且没给 storage_path 时用 tempfile.TemporaryDirectory().name，
# 而 WebView2 会一直占着该目录里的文件，临时目录删不掉——每启动一次就漏一个 ~7MB 的
# `tmpXXXX` 目录（本项目实测跑了两天攒了 15 个 / 113MB，全落在 cwd 里）。
# 给一个固定目录后：不累积、还能复用编译好的 shader 缓存，启动更快。
WEBVIEW_PROFILE = DATA_DIR / "webview2"


# ---------------------------------------------------------------------------
# 中文路径兼容层
# ---------------------------------------------------------------------------
def _is_ascii(text: str) -> bool:
    return all(ord(c) < 128 for c in text)


def _cache_root() -> Path:
    """放 ASCII 副本的根目录。优先 TEMP（几乎一定是 ASCII 且可写）。"""
    for env in ("TEMP", "TMP", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base and _is_ascii(base) and Path(base).is_dir():
            return Path(base) / APP_NAME / "clr"
    return Path.cwd() / ".clr-cache"


def _ensure_ascii_copy(src: Path, dst: Path) -> bool:
    """把包复制到 ASCII 目录（已存在且完整就跳过）。返回是否可用。"""
    marker = dst / ".staged"
    if marker.exists():
        return True
    tmp = dst.with_suffix(".tmp")
    try:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        marker.write_text("ok", encoding="utf-8")
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        tmp.rename(dst)
        return True
    except OSError:
        # 没有写权限 / 被占用：只要目标已经存在就继续用
        shutil.rmtree(tmp, ignore_errors=True)
        return dst.exists()


def prepare_ascii_runtime() -> bool:
    """让 pythonnet / pywebview 能从纯 ASCII 路径加载。返回 True 表示已就绪。"""
    cache = _cache_root()

    import pythonnet

    pkg = Path(pythonnet.__file__).resolve().parent
    if not _is_ascii(str(pkg)):
        target = cache / "pythonnet"
        if not _ensure_ascii_copy(pkg, target):
            return False
        pythonnet.__file__ = str(target / "__init__.py")

    import webview  # noqa: F401  —— 由下面按需重新加载

    wv_pkg = Path(webview.__file__).resolve().parent
    if not _is_ascii(str(wv_pkg)):
        target = cache / "webview"
        if not _ensure_ascii_copy(wv_pkg, target):
            return False
        # 让后续 import 命中 ASCII 副本（必须插在 sys.path 最前）
        if str(cache) not in sys.path:
            sys.path.insert(0, str(cache))
        for mod in [m for m in sys.modules if m == "webview" or m.startswith("webview.")]:
            del sys.modules[mod]
    return True


# ---------------------------------------------------------------------------
# 服务复用 / 内嵌
# ---------------------------------------------------------------------------
def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _api_ok(host: str, port: int) -> bool:
    """端口上跑的是不是 token-dashboard？

    只用「/api/meta 返回 200」判断太松：任何本地开发服务恰好占用 8787 都会被
    当成已有面板直接复用，浮窗于是连到别人的服务上、页面全空。
    这里改认 /api/version 的响应体（app 字段），一锤定音。
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/version", timeout=1.5) as r:
            if r.status != 200:
                return False
            data = json.loads(r.read().decode("utf-8", "replace"))
            return isinstance(data, dict) and data.get("app") == "token-dashboard"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_server(cfg: dict, log=None) -> tuple[str, int, bool]:
    """确保面板服务在跑。返回 (host, port, 是否本进程新起)。

    端口已被占用且 /api/version 认得出是本工具 → 直接复用（用户可能已经手动起了 serve）。
    """
    srv = cfg.get("server") or {}
    host = srv.get("host", "127.0.0.1")
    port = int(srv.get("port", 8787))

    if _port_open(host, port):
        if _api_ok(host, port):
            return host, port, False
        raise RuntimeError(
            f"端口 {port} 被其它程序占用，且不是 token-dashboard 服务。"
            f"请先关闭它，或在 config.yaml 里改 server.port。"
        )

    from .server import create_app

    app = create_app(cfg, log=log)
    import uvicorn

    # 关键：显式关掉 uvicorn 自带的日志配置。
    # windowed 进程（pythonw / TokenWidget.exe）里 sys.stdout / sys.stderr 是 None，
    # uvicorn 默认的 log_config 会给 DefaultFormatter 装 use_colors 探测，
    # 于是 `sys.stdout.isatty()` 直接 AttributeError，整个服务线程静默死掉
    # ——表现就是「双击 exe 没反应」，而且日志里只有一条线程异常。
    # 浮窗的日志我们自己的 say() 已经写文件了，不需要 uvicorn 再往控制台打字。
    t = threading.Thread(
        target=lambda: uvicorn.run(
            app, host=host, port=port, log_config=None, access_log=False
        ),
        daemon=True,
        name="token-server",
    )
    t.start()

    for _ in range(60):  # 最多等 6 秒
        if _api_ok(host, port):
            return host, port, True
        time.sleep(0.1)
    raise RuntimeError("内嵌服务启动超时")


# ---------------------------------------------------------------------------
# 开机自启：启动文件夹快捷方式（首启自注册，右键菜单可关）
# ---------------------------------------------------------------------------
def _autostart_lnk() -> Path:
    startup = Path(os.environ.get("APPDATA") or Path.home()) / (
        r"Microsoft\Windows\Start Menu\Programs\Startup"
    )
    return startup / "TokenWidget.lnk"


def _autostart_target() -> tuple[str, str]:
    """自启要指向的目标：(exe 路径, 命令行参数)。

    打包后 sys.executable 就是 TokenWidget.exe 本身，不需要参数；
    源码/venv 里运行时 sys.executable 是 python.exe，直接指过去只会开一个 REPL，
    所以要换成同目录的 pythonw.exe 并补上 `-m token_dashboard widget`。
    """
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return str(exe), ""
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), "-m token_dashboard widget"


def autostart_enabled() -> bool:
    return _autostart_lnk().exists()


def _run_ps(script: str, env_extra: dict | None = None, timeout: int = 20) -> tuple[int, str]:
    """跑一段 PowerShell 并稳健地拿回输出。

    不能直接用 `text=True`：中文 Windows 下 subprocess 按 GBK 解码，而 PowerShell
    输出的是 UTF-8（或 OEM 码页），路径含中文时直接 UnicodeDecodeError —— 在
    `_lnk_target` 里会退化成「读不到目标」，于是每次启动都白重建一次快捷方式。
    这里统一按字节收、先 UTF-8 再 GBK 兜底。
    """
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", prefix + script],
        capture_output=True, timeout=timeout,
        env={**os.environ, **(env_extra or {})},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    raw = proc.stdout or b""
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return proc.returncode, raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return proc.returncode, raw.decode("utf-8", "replace").strip()


def _lnk_target(lnk: Path) -> str | None:
    """读回 .lnk 的 TargetPath（不引入 pywin32：走一次 PowerShell COM）。"""
    if os.name != "nt":
        return None
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "if(Test-Path $env:LNK){Write-Output $s.TargetPath}"
    )
    try:
        _, out = _run_ps(script, {"LNK": str(lnk)}, timeout=15)
        return out.splitlines()[0].strip() if out else None
    except Exception:
        return None


def set_autostart(on: bool, log=None) -> bool:
    """创建 / 删除「启动」文件夹里的 TokenWidget.lnk。返回最终是否处于开启状态。

    不打算引入 pywin32（打包体积 + hiddenimports 都要跟着变），
    直接让 PowerShell 走 WScript.Shell COM——和 install-autostart.cmd 同一套思路，
    但由 Python 调起、不弹 cmd 窗口。
    """
    lnk = _autostart_lnk()
    if os.name != "nt":
        return False
    try:
        if on:
            target, args = _autostart_target()
            # 已经指向当前 exe 就不重写；用户移动了目录则重建（见 D3 第 6 条）
            if lnk.exists() and _lnk_target(lnk) == target:
                return True
            lnk.parent.mkdir(parents=True, exist_ok=True)
            script = (
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
                "$s.TargetPath=$env:TARGET;"
                "$s.Arguments=$env:ARGS;"
                "$s.WorkingDirectory=$env:WORKDIR;"
                "$s.Description='Token Dashboard desktop widget';"
                "$s.Save()"
            )
            code, out = _run_ps(
                script,
                {"LNK": str(lnk), "TARGET": target, "ARGS": args,
                 "WORKDIR": str(Path(target).parent)},
            )
            if code != 0:
                if log:
                    log(f"写自启快捷方式失败：{out[:400]}")
                return False
            return lnk.exists()
        lnk.unlink(missing_ok=True)
        return not lnk.exists()
    except Exception as exc:
        if log:
            log(f"设置开机自启失败：{exc!r}")
        return False


# ---------------------------------------------------------------------------
# 窗口位置持久化
# ---------------------------------------------------------------------------
def _widget_state_path() -> Path:
    # 独立文件名（旧 tkinter 浮窗的 config/widget.json 口径不同，已随代码一起删除）
    return CONFIG_DIR / "widget-web.json"


def default_log_path() -> Path:
    """默认日志：无控制台启动时（exe / pythonw）出问题唯一能看到的东西。"""
    return DATA_DIR / "widget.log"


def load_widget_state() -> dict:
    path = _widget_state_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {**WIDGET_DEFAULTS, **data}
        except (OSError, json.JSONDecodeError):
            pass
    return dict(WIDGET_DEFAULTS)


def save_widget_state(state: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _widget_state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # 写不了就下次用默认位置，不该因此崩掉


# ---------------------------------------------------------------------------
# 前端桥
# ---------------------------------------------------------------------------
class Api:
    """暴露给 widget.html 的 `pywebview.api.*`。"""

    def __init__(self, cfg: dict, base_url: str, state: dict):
        self._cfg = cfg
        self._base = base_url
        self._state = state
        self._window = None
        self._closing = False
        # 首启自动注册开机自启时置为提示文案，前端启动后 toast 一次（D3 的「可见地告知」）
        self.autostart_notice: str | None = None

    # --- 窗口操作 ---
    def begin_drag(self) -> bool:
        """无边框窗口拖动：把这次点击伪装成"点在标题栏"上，交给系统接管。"""
        try:
            hwnd = int(self._window.native.Handle.ToInt64())
            user32 = ctypes.windll.user32
            user32.ReleaseCapture()
            # WM_NCLBUTTONDOWN(0xA1) + HTCAPTION(2)
            user32.SendMessageW(hwnd, 0xA1, 2, 0)
            return True
        except Exception:
            return False

    def set_topmost(self, on: bool | None = None) -> bool:
        try:
            w = self._window
            current = bool(getattr(w, "on_top", True))
            w.on_top = (not current) if on is None else bool(on)
            self._state["on_top"] = bool(w.on_top)
            save_widget_state(self._state)
            return bool(w.on_top)
        except Exception:
            return False

    def get_state(self) -> dict:
        return {
            "on_top": bool(self._state.get("on_top", True)),
            "autostart": autostart_enabled(),
            "notice": self.autostart_notice,
            "base": self._base,
        }

    def set_autostart(self, on: bool | None = None) -> bool:
        """切换开机自启（右键菜单用）。返回切换后的真实状态。"""
        want = (not autostart_enabled()) if on is None else bool(on)
        ok = set_autostart(want)
        self._state["autostart"] = ok
        save_widget_state(self._state)
        return ok

    def open_panel(self, since: str | None = None, until: str | None = None) -> bool:
        """用系统默认浏览器打开面板。

        since / until：浮窗点迷你柱时传入，面板通过 hash 初始化筛选（C8）。

        注意：不要用 webbrowser.open——旧版在打包后引发过 GIL 崩溃。
        """
        url = self._base
        params = []
        if since:
            params.append("since=" + str(since))
            params.append("until=" + str(until or since))
        if params:
            url += "/#" + "&".join(params)
        try:
            os.startfile(url)
            return True
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
                return True
            except Exception:
                return False

    def quit(self) -> bool:
        self._closing = True
        try:
            self.save_position()
        except Exception:
            pass
        try:
            self._window.destroy()
        except Exception:
            os._exit(0)
        return True

    # --- 位置 ---
    def save_position(self) -> bool:
        """记下窗口位置（逻辑像素）。

        必须用 pywebview 的 window.x / window.y，不能直接读 `native.Left/Top`：
        native 是 WinForms 控件的**物理像素**，而 create_window(x=, y=) 收的是
        **逻辑像素**。DPR=1.5 时把物理值原样存下来再回放，窗口会跑到 1.5 倍远处
        （实测：移到 (600,450) 存成 600,450，下次按逻辑值 600 打开 → 屏幕外）。
        pywebview 的这两个属性内部按窗口 DPI 换算，存读口径才一致。
        """
        try:
            self._state["x"] = int(self._window.x)
            self._state["y"] = int(self._window.y)
            save_widget_state(self._state)
            return True
        except Exception:
            return False


def _dpi_scale() -> float:
    """系统缩放（150% → 1.5）。只用于日志诊断，不参与尺寸计算。"""
    try:
        user32 = ctypes.windll.user32
        dc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        user32.ReleaseDC(0, dc)
        return max(1.0, min(3.0, dpi / 96.0))
    except Exception:
        return 1.0


# pywebview 的 width/height 与 WebView2 实际视口之间有一个固定偏移：
# 实测（DPR=1.5，frameless）请求 260x246 只得到 246x209 的视口，宽少 14、高少 37。
# 这个偏移来自 WinForms 窗口的非客户区，与 DPI 无关，所以直接加上即可。
# 注意它只是"起点"：真实值由 fit_viewport() 读回视口后迭代校正，不依赖这个常数。
VIEWPORT_INSET_X = 14
VIEWPORT_INSET_Y = 37


def _make_tray_icon(api: "Api", window) -> object | None:
    """系统托盘：浮窗被关闭后还能从托盘叫回来。

    pystray 是可选的；装不上就静默跳过（浮窗本身照常工作）。
    """
    try:
        from PIL import Image, ImageDraw
        import pystray
    except Exception:
        return None

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=(20, 24, 36, 255))
    d.polygon([(34, 10), (18, 36), (30, 36), (26, 54), (46, 26), (33, 26)],
              fill=(59, 130, 246, 255))

    def show(_icon=None, _item=None):
        try:
            window.show()
            window.restore()
        except Exception:
            pass

    def toggle_top(_icon=None, _item=None):
        api.set_topmost()

    def open_panel(_icon=None, _item=None):
        api.open_panel()

    def toggle_autostart(_icon=None, _item=None):
        api.set_autostart()

    def quit_app(_icon=None, _item=None):
        try:
            icon.stop()
        except Exception:
            pass
        api.quit()

    menu = pystray.Menu(
        pystray.MenuItem("显示浮窗", show, default=True),
        pystray.MenuItem("打开面板", open_panel),
        pystray.MenuItem("切换置顶", toggle_top),
        pystray.MenuItem(
            "开机自启",
            toggle_autostart,
            checked=lambda _item: autostart_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )
    icon = pystray.Icon("TokenDashboard", img, "Token Dashboard", menu)
    # 托盘是可选功能，但它的线程崩了不应该只在日志里留一条 traceback 就完事：
    # 某些环境（受限会话 / 无任务栏的沙箱）Shell_NotifyIcon 会被拒绝（WinError 5），
    # 这里捕获后返回 None，浮窗照常工作，调用方也能据此记一条「托盘不可用」。
    state = {"failed": None}

    def run() -> None:
        try:
            icon.run()
        except Exception as exc:  # 包括 _win32._create_window 抛的 PermissionError
            state["failed"] = exc

    threading.Thread(target=run, daemon=True, name="widget-tray").start()
    return icon


# ---------------------------------------------------------------------------
# 日志 / 异常钩子
# ---------------------------------------------------------------------------
def _make_logger(log_path: str | None):
    """无控制台启动（pythonw / TokenWidget.exe）时 stdout 是 None，
    print 会「成功」但什么都看不到，异常也无处可去。所有诊断信息都写到文件里。
    """
    if not log_path:
        return lambda msg: None
    path = Path(log_path)

    def write(msg: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass

    return write


def _install_excepthooks(log) -> None:
    def hook(exc_type, exc, tb):
        log("未捕获异常:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook
    threading.excepthook = lambda args: log(
        "线程异常:\n"
        + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    )


def _show_fatal(msg: str) -> None:
    """无控制台进程里唯一能让人看见错误的办法：弹个系统消息框。

    exe 是 console=False，双击后如果直接 return，用户只会看到「什么都没发生」。
    这里用 user32.MessageBoxW（不拉 tkinter，省一次启动开销）。
    """
    try:
        ctypes.windll.user32.MessageBoxW(
            None, msg, "Token Dashboard", 0x10  # MB_ICONERROR
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def run_widget(cfg: dict | None = None, **kwargs) -> int:
    cfg = cfg or load_config()
    # 不显式给 --log 时也写默认日志：exe 是 console=False，崩溃时没有控制台可看，
    # 没日志就只能对着「双击没反应」干瞪眼。
    log = _make_logger(kwargs.get("log_path") or str(default_log_path()))
    _install_excepthooks(log)

    def say(msg: str) -> None:
        log(msg)
        try:
            print(f"[widget] {msg}", flush=True)
        except Exception:
            pass  # console=False 时 stdout 为 None

    # 系统适配：先查操作系统版本与 WebView2 运行时。
    # 这两项不满足时 pywebview 的报错是 HRESULT / ImportError，用户根本读不懂；
    # 提前拦下来给中文指引，并写进 widget.log。
    from .syscheck import check_gui_ready, os_label

    say(f"运行环境：{os_label()}")
    problem = check_gui_ready()
    if problem:
        say("环境检查未通过：\n" + problem)
        _show_fatal(problem)
        return 3

    state = load_widget_state()
    wcfg = {**WIDGET_DEFAULTS, **(cfg.get("widget") or {})}
    # config 里显式给的值优先于上次拖动的记录；尺寸只认 config（存档里的 DPI 口径不可靠）
    for k in ("width", "height", "on_top"):
        if k in wcfg and wcfg[k] is not None:
            state[k] = wcfg[k]

    # 默认开机自启：首启自注册。这是侵入性操作，必须「可见地」告知——
    # 浮窗脚注会显示状态、日志留痕，右键菜单一键可关（见 D3 的分寸要求）。
    autostart_wanted = bool(wcfg.get("autostart", True))
    if os.name == "nt":
        if autostart_wanted and not autostart_enabled():
            if set_autostart(True, log=log):
                say("已默认开启开机自启（右键菜单可关闭）")
            else:
                say("默认开机自启注册失败（不影响浮窗，可从右键菜单重试）")
        elif autostart_wanted:
            # 用户移动过 exe 目录：快捷方式还指着旧路径，这里顺手重建
            set_autostart(True, log=log)
            say("开机自启已开启")

    host, port, spawned = ensure_server(cfg, log=log)
    base = f"http://{host}:{port}"
    say(f"服务：{base}（{'本进程内嵌' if spawned else '复用已有进程'}）")

    # 先扫一轮再开窗。
    # 不能指望定时扫描来「第一次」填数据：start_scanner 是先睡后扫，
    # 浮窗刚起来时今日数字会是 0，要等 interval 分钟才变正常——看起来就像坏了。
    # cmd_serve 也是启动先扫一轮，之后才交给定时线程。
    try:
        result = run_scan(cfg)
        events = sum(s.get("events", 0) for s in result.values())
        say(f"启动扫描完成：新增 {events} 条事件")
    except Exception as exc:
        say(f"启动扫描失败（不影响浮窗显示）：{exc!r}")

    # 中文路径下 pythonnet 会加载失败；先把加载器指到 ASCII 副本
    if not prepare_ascii_runtime():
        say(
            "无法为 pythonnet 准备 ASCII 运行目录。"
            "如果项目路径含中文，请把项目移到纯英文目录后再试。"
        )
        return 2

    import webview

    api = Api(cfg, base, state)
    if autostart_wanted and os.name == "nt":
        api.autostart_notice = "已默认开启开机自启（右键菜单可关闭）"
    w = int(state.get("width") or WIDGET_DEFAULTS["width"])
    h = int(state.get("height") or WIDGET_DEFAULTS["height"])
    x, y = state.get("x"), state.get("y")

    window = webview.create_window(
        "Token Widget",
        f"{base}/static/widget.html",
        width=w + VIEWPORT_INSET_X,
        height=h + VIEWPORT_INSET_Y,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        x=x,
        y=y,
        frameless=True,
        easy_drag=False,        # 拖动由我们自己的标题栏 + api.begin_drag 处理
        on_top=bool(state.get("on_top", True)),
        background_color="#0d1017",
        text_select=False,
        js_api=api,
    )
    api._window = window

    def fit_viewport(target_w: int, target_h: int) -> None:
        """把 WebView2 视口校正到目标 CSS 尺寸。

        pywebview 的 width/height 与 WebView2 的 CSS 视口之间既有 DPI 缩放
        又有非客户区偏移，比例随系统缩放变化，硬编码不可靠。
        这里读一次实际视口、按差额调整窗口尺寸，最多迭代两轮。
        """
        for i in range(3):
            try:
                raw = window.evaluate_js(
                    "JSON.stringify([innerWidth, innerHeight, devicePixelRatio])"
                )
                if not raw:
                    say("fit_viewport: 读不到视口")
                    return
                vw, vh, dpr = json.loads(raw)
                say(f"视口 #{i}: {vw}x{vh} dpr={dpr} 目标 {target_w}x{target_h}")
                dw, dh = target_w - int(vw), target_h - int(vh)
                if abs(dw) <= 1 and abs(dh) <= 1:
                    say("视口已匹配")
                    return
                cur = window.native
                new_w = max(MIN_WIDTH, int(cur.Width) + dw)
                new_h = max(MIN_HEIGHT, int(cur.Height) + dh)
                say(f"resize {cur.Width}x{cur.Height} -> {new_w}x{new_h}")
                window.resize(new_w, new_h)
                time.sleep(0.4)
            except Exception as exc:
                say(f"fit_viewport 失败: {exc!r}")
                return

    def watch_position() -> None:
        """定期记下窗口位置：窗口被系统关闭时也能保存（不依赖 closing 事件）。

        pywebview 6.2.1 上 `window.events.closing += fn` 会让 `webview.start()`
        立刻返回（窗口一闪而过），所以改用"轮询位置 + start() 返回后落盘"。
        """
        last = None
        time.sleep(2.5)  # 等窗口真正显示 + 视口校正完成
        while not api._closing:
            try:
                n = window.native
                if not n.Visible:
                    time.sleep(1.0)
                    continue
                # 用 window.x/y（逻辑像素），与 create_window(x=, y=) 同口径
                cur = (int(window.x), int(window.y))
                if cur != last:
                    last = cur
                    api._state.update(x=cur[0], y=cur[1])
                    save_widget_state(api._state)
            except Exception:
                pass
            time.sleep(1.5)

    threading.Thread(target=watch_position, daemon=True, name="widget-pos").start()

    def after_start() -> None:
        time.sleep(1.2)  # 等 WebView2 完成首次导航
        fit_viewport(w, h)
        if kwargs.get("tray", True):
            _make_tray_icon(api, window)
        say("窗口就绪")

    say(f"启动：{w}x{h} @ ({x},{y}) on_top={state.get('on_top')}")
    try:
        webview.start(
            func=after_start,
            debug=bool(kwargs.get("debug")),
            # 固定用户数据目录 + 非隐私模式：见 WEBVIEW_PROFILE 的注释。
            # private_mode=False 才允许复用缓存；隐私模式每次都是全新 profile。
            private_mode=False,
            storage_path=str(WEBVIEW_PROFILE),
        )
    finally:
        # start() 返回意味着窗口已关闭
        api.save_position()
        save_widget_state(api._state)
        say("退出")
    return 0
