"""运行环境自检：操作系统版本 + WebView2 运行时。

为什么要单独做：这两个是「换一台机器就报错、但报错完全看不懂」的典型。
- Windows 版本太低（Win7/8/8.1 或过老的 Win10）：WebView2 早已停止支持，
  pywebview 会在导入 pythonnet 时抛一堆 HRESULT，用户完全不知道是系统太老。
- 没装 WebView2 运行时：`webview.start()` 内部才炸，窗口一闪而过（exe 无控制台，
  连报错都看不到）。

所以这里在启动早期就做检查，返回人话级别的提示。

最低版本取 Windows 10 1809（build 17763）：
- WebView2 的 Evergreen 运行时从 2023 年起不再支持 Win7/8/8.1；
- 1809 是仍有支持的 Win10 分支里最老的一档。
"""
from __future__ import annotations

import os
import sys

MIN_BUILD = 17763  # Windows 10 1809
MIN_NAME = "Windows 10 1809（build 17763）"

# WebView2 Evergreen 运行时的注册表位置（32/64 位都要看：装的是同一个包）
_WEBVIEW2_KEYS = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
)


def os_version() -> tuple[int, int, int] | None:
    """(major, minor, build)；非 Windows 返回 None。"""
    if os.name != "nt":
        return None
    return tuple(sys.getwindowsversion()[:3])  # type: ignore[return-value]


def os_label() -> str:
    v = os_version()
    if not v:
        return f"{os.sys.platform}（非 Windows）"
    major, minor, build = v
    # 微软从 Win11 起仍报 10.0，用 build 号区分；
    # 6.x 才是真正的老系统（Vista=6.0 / 7=6.1 / 8=6.2 / 8.1=6.3）
    if major < 10:
        name = {6: {0: "Windows Vista", 1: "Windows 7", 2: "Windows 8", 3: "Windows 8.1"}}.get(
            major, {}
        ).get(minor, f"Windows {major}.{minor}")
    elif build >= 22000:
        name = "Windows 11"
    else:
        name = "Windows 10"
    return f"{name}（{major}.{minor}.{build}）"


def check_windows() -> str | None:
    """系统版本不满足时返回人话提示，满足则返回 None。"""
    if os.name != "nt":
        return (
            f"当前系统是 {os_label()}，本工具目前只支持 64 位 Windows。\n"
            f"（面板服务本身跨平台，但桌面浮窗依赖 Windows 的 WebView2。）"
        )
    v = os_version()
    assert v is not None
    if v[2] < MIN_BUILD:
        return (
            f"系统版本过低：检测到 {os_label()}，最低需要 {MIN_NAME}。\n"
            "原因：桌面浮窗依赖 Microsoft Edge WebView2 运行时，"
            "它在 Windows 7 / 8 / 8.1 以及 1809 之前的 Win10 上已停止支持。\n"
            "处理：升级 Windows 后重试；只想用命令行的话可以跑 "
            "`TokenDashboard.exe scan / report`（这两个命令不依赖 WebView2）。"
        )
    return None


def webview2_installed() -> bool:
    """检查 WebView2 Evergreen 运行时是否已安装。非 Windows 直接 False。"""
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    for key in _WEBVIEW2_KEYS:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, key) as k:
                    pv = winreg.QueryValueEx(k, "pv")[0]
                if pv and pv != "0.0.0.0":
                    return True
            except OSError:
                continue
    return False


def check_webview2() -> str | None:
    """缺 WebView2 时返回人话提示（带下载地址），否则 None。"""
    if webview2_installed():
        return None
    return (
        "缺少 Microsoft Edge WebView2 运行时，桌面浮窗无法显示。\n"
        "处理：装一次即可（约 2 MB，微软官方，Win11 一般已自带）：\n"
        "  https://go.microsoft.com/fwlink/p/?LinkId=2124703\n"
        "装完重新双击 TokenWidget.exe。命令行 scan / report 不受影响。"
    )


def check_gui_ready() -> str | None:
    """浮窗启动前的完整检查：系统版本 + WebView2。"""
    return check_windows() or check_webview2()
