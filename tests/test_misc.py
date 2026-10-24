"""C1 补充：CLI 报表列宽 / 价格配置写回 / 前端静态资源完整性。"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from token_dashboard.cli import REPORT_HEADERS, REPORT_WIDTHS, _disp_width, _pad, _report_header
from token_dashboard.config import load_config, save_budget


def _split_by_display_width(text: str, widths) -> list[str]:
    """按「显示宽度」切分字符串（中文占 2 列，不能按字符下标切）。"""
    out, cur, acc = [], "", 0
    widths = list(widths)
    idx = 0
    for ch in text:
        w = _disp_width(ch)
        if idx < len(widths) and acc + w > widths[idx]:
            out.append(cur)
            cur, acc = "", 0
            idx += 1
        cur += ch
        acc += w
    out.append(cur)
    return out


def test_report_header_width_matches_data_widths():
    """A4：表头每个单元格的显示宽度必须等于对应数据列的宽度。"""
    header = _report_header()
    cells = _split_by_display_width(header, REPORT_WIDTHS)
    assert len(cells) == len(REPORT_HEADERS), f"切出 {len(cells)} 列：{cells}"
    for cell, name, width in zip(cells, REPORT_HEADERS, REPORT_WIDTHS, strict=True):
        # 超过列宽的窄表头会被撑开，这里只要求「列宽 >= 表头宽度」
        assert _disp_width(cell) >= _disp_width(name)
        assert cell.strip() == name, f"第 {name} 列内容错位：{cell!r}"
    assert _disp_width(header) == sum(REPORT_WIDTHS)


def test_data_row_width_matches_header():
    """数据行与表头逐列对齐：同一列的数字右边界与表头右边界一致。"""
    from token_dashboard.cli import _pad

    row = _pad("2026-10-01", REPORT_WIDTHS[0], "<")
    for value, width in zip((1234, 5678, 90, 1234567, 40, 1234999), REPORT_WIDTHS[1:7],
                            strict=True):
        row += _pad(f"{value:,}", width)
    row += _pad("0.1234", REPORT_WIDTHS[7]) + _pad("0", REPORT_WIDTHS[8])
    assert _disp_width(row) == sum(REPORT_WIDTHS)


def test_report_widths_expand_for_oversized_values():
    """超过 10 位的 tokens 不能串列：列宽要自适应到内容宽度。"""
    from token_dashboard.cli import _column_widths, _pad, _report_cells, _report_header

    row = {
        "date": "2026-12-31", "input_tokens": 999_999_999_999, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "output_tokens": 999_999_999_999, "reasoning_tokens": 0,
        "cost_usd": 1234.5678, "unpriced": 99999,
    }
    widths = _column_widths([row])
    cells = _report_cells(row)
    for i, (cell, width) in enumerate(zip(cells, widths, strict=True)):
        assert _disp_width(cell) <= width, f"第 {i} 列放不下 {cell!r}"
    # 每列都严格对齐：所有单元格补齐后显示宽度一致
    line = _pad(cells[0], widths[0], "<") + "".join(
        _pad(c, w) for c, w in zip(cells[1:], widths[1:], strict=True))
    assert _disp_width(line) == sum(widths)
    # 表头用同一组宽度 → 也严丝合缝
    assert _disp_width(_report_header(widths)) == sum(widths)


def test_pad_accounts_for_cjk_double_width():
    assert _disp_width("输出含推理") == 10
    assert _disp_width("abc") == 3
    assert _disp_width(_pad("输出含推理", 14)) == 14
    assert _disp_width(_pad("abc", 10)) == 10


def test_save_budget_appends_section_when_missing(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("server:\n  port: 8787\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    save_budget(cfg, 42.5)
    text = cfg_file.read_text(encoding="utf-8")
    assert "budget:" in text
    assert "monthly_usd: 42.5" in text
    # 新追加的段落必须保留中文注释（不能被写成 ???）
    assert "预算提醒" in text, text
    # 写回后重新加载必须解析成同一个值
    assert load_config(str(cfg_file))["budget"]["monthly_usd"] == 42.5


@pytest.mark.parametrize(("value", "expected"), [
    (100, "100"),
    (1000.0, "1000"),      # 不能退化成 "1"（str().rstrip("0") 的老坑）
    (100.50, "100.5"),
    (99.99, "99.99"),
    (12.0, "12"),
])
def test_save_budget_number_format(tmp_path, value, expected):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("budget:\n  monthly_usd: null\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    save_budget(cfg, value)
    text = cfg_file.read_text(encoding="utf-8")
    assert f"monthly_usd: {expected}" in text, text
    assert load_config(str(cfg_file))["budget"]["monthly_usd"] == value


def test_save_budget_keeps_comments(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "budget:\n  # 月度预算（USD）\n  monthly_usd: 100   # 旧值\n\nscanner:\n  interval_minutes: 3\n",
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_file))
    save_budget(cfg, 200)
    text = cfg_file.read_text(encoding="utf-8")
    assert "# 月度预算（USD）" in text
    assert "monthly_usd: 200" in text
    assert "scanner:" in text and "interval_minutes: 3" in text
    assert load_config(str(cfg_file))["budget"]["monthly_usd"] == 200


def test_load_config_expands_pricing_overrides_path(tmp_path):
    """B2：pricing.overrides_file 是相对路径时要相对 config/ 目录展开。

    config.example.yaml 写的是 `overrides_file: pricing.overrides.yaml`：
    不展开的话 Pricing 会去进程 cwd 找文件，而面板引导用户改的是
    config/pricing.overrides.yaml，两边对不上，自定义定价静默失效。
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("pricing:\n  overrides_file: pricing.overrides.yaml\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert Path(cfg["pricing"]["overrides_file"]) == tmp_path / "pricing.overrides.yaml"
    # 绝对路径原样保留
    abs_file = tmp_path / "custom-overrides.yaml"
    cfg_file.write_text(f"pricing:\n  overrides_file: {abs_file}\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert Path(cfg["pricing"]["overrides_file"]) == abs_file


def test_dsh_decode_project_fallback_splits_on_hyphen(tmp_path, monkeypatch):
    """B6 回退行为：磁盘上匹配不到任何目录时按单段切分（与旧行为一致）。"""
    from token_dashboard.collectors.dsh import decode_project

    monkeypatch.chdir(tmp_path)   # cwd 是空目录，exists() 全部落空
    assert decode_project("--code-token-dashboard--") == str(
        Path("code") / "token" / "dashboard")


def test_dsh_decode_project_merges_existing_hyphen_dirs(tmp_path, monkeypatch):
    """B6：带连字符的目录名在本机真实存在时优先合并还原，不再拆成两级。"""
    from token_dashboard.collectors.dsh import decode_project

    (tmp_path / "code" / "token-dashboard").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert Path(decode_project("--code-token-dashboard--")) == Path("code") / "token-dashboard"


@pytest.mark.skipif(os.name != "nt", reason="盘符还原只在 Windows 上有意义")
def test_dsh_decode_project_restores_drive_colon():
    """B6：盘符段补回冒号（旧实现把 D: 编码成 D，还原后变成无冒号的 D\\...）。"""
    from token_dashboard.collectors.dsh import decode_project

    assert decode_project("--C-Users--").lower() == "c:\\users"


def test_ui_assets_have_no_unresolved_references():
    """前端静态文件的基本完整性：引用的 id 都存在、没有遗留 TODO 占位。"""
    from token_dashboard.config import resource_dir

    ui = resource_dir() / "ui"
    index = (ui / "index.html").read_text(encoding="utf-8")
    widget = (ui / "widget.html").read_text(encoding="utf-8")

    # 页面里用到的每个 getElementById 都必须能在 HTML 里找到对应 id
    for name, html in (("index", index), ("widget", widget)):
        ids = set(re.findall(r'id="([^"]+)"', html))
        used = set(re.findall(r'getElementById\("([^"]+)"\)', html))
        missing = used - ids
        assert not missing, f"{name}.html 缺少 id：{sorted(missing)}"

    # 面板与浮窗共享的设计系统必须被两个页面都引用
    for html in (index, widget):
        assert "/static/shared.css" in html
        assert "/static/shared.js" in html


def test_index_has_required_ui_hooks():
    """B 系列 UI 任务的落点检查（防止后续重构把它们又删掉）。"""
    from token_dashboard.config import resource_dir

    index = (resource_dir() / "ui" / "index.html").read_text(encoding="utf-8")
    for needle in [
        'id="onboard"',              # B6 引导卡
        'id="chipUnpriced"',         # B2 未定价 chip
        'function priceTemplate',    # B2 价格模板
        'id="chipBudget"',           # B3 预算入口
        'data-copy-price',           # B2 复制按钮
        'id="sessionFilter"',        # B5 会话筛选
        'function toggleSessionRow', # B5 会话展开
        'id="lastUpd"',              # B10 可点击刷新
        'keydown',                   # B8 快捷键
        'id="fMetric"',              # U1 图表指标切换
        'id="btnResetFilter"',       # U3 重置筛选按钮
        'id="btnUpdatePricing"',     # U5 一键更新价格表
        'function syncHashFromState',  # B5 深链双向同步
        'function clearAllFilters',    # U3 清筛选共用逻辑
        'unpricedOpen',              # B4 展开状态保持
        'openSessions',              # B4 会话展开恢复
        'metricHelpers',             # U1 指标感知渲染
    ]:
        assert needle in index, f"index.html 缺少 {needle}"


@pytest.mark.parametrize("path", ["src/token_dashboard/ui/index.html",
                                  "src/token_dashboard/ui/widget.html"])
def test_html_is_utf8(path):
    root = Path(__file__).resolve().parents[1]
    raw = (root / path).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "HTML 不应带 BOM（浏览器能处理但没必要）"
    raw.decode("utf-8")
