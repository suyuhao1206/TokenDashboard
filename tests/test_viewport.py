"""A8：fit_viewport 在 100%/125%/150%/200% 缩放下的收敛性（模拟窗口，不启动 GUI）。

模拟器按 Windows 的实际行为建模：
- create_window(width=逻辑) × dpr = 物理窗口宽；
- 视口（CSS 像素）= (物理宽 - 非客户区) / dpr；
- window.width/height（pywebview 公开属性）= 物理 / dpr = 逻辑像素。

旧实现用的是物理像素的 `native.Width`，直接加 CSS 差值 → 差值被 dpr 放大，
高 DPI 下不收敛。这也是本用例要守住的回归点。
"""

from __future__ import annotations

import pytest

from token_dashboard.widget_web import (
    MIN_HEIGHT,
    MIN_WIDTH,
    VIEWPORT_INSET_X,
    VIEWPORT_INSET_Y,
    fit_viewport_steps,
)


class FakeWindow:
    """模拟 frameless pywebview 窗口在给定 DPI 下的尺寸换算。"""

    def __init__(self, dpr: float, logical_w: int, logical_h: int):
        self.dpr = dpr
        self.logical_w = logical_w
        self.logical_h = logical_h
        self.resizes: list[tuple[int, int]] = []

    # 物理窗口尺寸（WinForms 的 Width/Height）
    @property
    def physical(self) -> tuple[int, int]:
        return int(self.logical_w * self.dpr), int(self.logical_h * self.dpr)

    def viewport(self) -> tuple[int, int, float]:
        pw, ph = self.physical
        return (int((pw - VIEWPORT_INSET_X) / self.dpr),
                int((ph - VIEWPORT_INSET_Y) / self.dpr), self.dpr)

    def size(self) -> tuple[int, int]:
        pw, _ = self.physical
        return self.logical_w, self.logical_h

    def resize(self, w: int, h: int) -> None:
        self.resizes.append((w, h))
        self.logical_w, self.logical_h = int(w), int(h)


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_fit_viewport_converges_at_every_scale(dpr):
    target_w, target_h = 260, 246
    win = FakeWindow(dpr, target_w, target_h)
    logs: list[str] = []

    ok = fit_viewport_steps(
        target_w, target_h,
        read_viewport=win.viewport,
        read_size=win.size,
        resize=win.resize,
        sleep=lambda _s: None,
        say=logs.append,
        max_rounds=3,
    )

    assert ok, f"dpr={dpr} 未收敛，日志：{logs}"
    vw, vh, _ = win.viewport()
    assert abs(vw - target_w) <= 1, f"dpr={dpr} 宽 {vw} != {target_w}"
    assert abs(vh - target_h) <= 1, f"dpr={dpr} 高 {vh} != {target_h}"
    # 最多 3 轮、且实际只 resize 了很少次数（不该反复抖动）
    assert len(win.resizes) <= 2, f"dpr={dpr} resize 次数过多：{win.resizes}"


def test_fit_viewport_uses_logical_pixels_not_physical():
    """回归守卫：把 read_size 换成物理像素（旧实现）就必须不收敛/异常。"""
    target_w, target_h = 260, 246
    win = FakeWindow(2.0, target_w, target_h)
    logs: list[str] = []

    ok = fit_viewport_steps(
        target_w, target_h,
        read_viewport=win.viewport,
        read_size=lambda: win.physical,     # ← 故意用物理像素
        resize=win.resize,
        sleep=lambda _s: None,
        say=logs.append,
        max_rounds=3,
    )
    # 用物理像素时修正量被放大 2 倍，尺寸会朝错误方向跑，不会收敛
    assert not ok, "物理像素口径竟然收敛了，说明测试模型没模拟出 dpr 放大"


def test_fit_viewport_stops_when_error_stops_shrinking():
    """误差不再变小就提前退出，不要 3 轮原地打转。"""
    logs: list[str] = []
    calls = {"n": 0}

    def viewport():
        calls["n"] += 1
        return 200, 200, 1.0          # 永远差 60/46，resize 也不变

    ok = fit_viewport_steps(
        260, 246,
        read_viewport=viewport,
        read_size=lambda: (260, 246),
        resize=lambda w, h: None,
        sleep=lambda _s: None,
        say=logs.append,
        max_rounds=5,
    )
    assert not ok
    assert calls["n"] <= 3, f"不该反复读视口：{calls['n']} 次"
    assert any("停滞" in line for line in logs), logs


def test_fit_viewport_clamps_to_min_size():
    """目标比最小尺寸还小时，不会把窗口缩到 MIN 以下。"""
    win = FakeWindow(1.0, 300, 300)
    fit_viewport_steps(
        MIN_WIDTH - 40, MIN_HEIGHT - 40,
        read_viewport=win.viewport,
        read_size=win.size,
        resize=win.resize,
        sleep=lambda _s: None,
        say=lambda _m: None,
    )
    assert all(w >= MIN_WIDTH and h >= MIN_HEIGHT for w, h in win.resizes), win.resizes
