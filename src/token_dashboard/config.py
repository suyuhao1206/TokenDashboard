"""配置加载与路径解析。

兼容两种运行方式：
- 源码运行（`uv run token-dashboard ...`）：程序根目录 = 项目根
- PyInstaller 打包后的 exe：程序根目录 = **exe 所在目录**（data/、config/ 都在它旁边），
  只读资源（UI 静态文件、内置价格表）放在 sys._MEIPASS 里。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "token_dashboard"
    return Path(__file__).resolve().parent


ROOT = app_root()
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG = CONFIG_DIR / "config.yaml"
EXAMPLE_CONFIG = CONFIG_DIR / "config.example.yaml"


def _builtin_defaults() -> dict:
    """没有 config.yaml 时使用（打包后 exe 也能开箱即用）。"""
    home = Path.home()
    return {
        "server": {"host": "127.0.0.1", "port": 8787},
        "storage": {"db_path": str(DATA_DIR / "usage.db")},
        "collectors": {
            "claude_code": {"enabled": True, "paths": [str(home / ".claude" / "projects")]},
            "codex": {
                "enabled": True,
                "paths": [str(home / ".codex" / "sessions"),
                          str(home / ".codex" / "archived_sessions")],
            },
            "dsh": {"enabled": True, "paths": [str(home / ".dsh" / "sessions")]},
            "workbuddy": {"enabled": True, "paths": [str(home / ".workbuddy" / "projects")]},
            "cc_switch": {
                "enabled": True,
                "db_path": str(home / ".cc-switch" / "cc-switch.db"),
            },
        },
        "pricing": {"overrides_file": str(CONFIG_DIR / "pricing.overrides.yaml")},
        "ui": {"timezone": "Asia/Shanghai"},
        # 预算提醒（M4）：本月 1 号至今的成本超过 80% / 100% 时面板状态条告警
        "budget": {"monthly_usd": None},
        # 后台定时扫描间隔（分钟）。0 = 关闭，面板只能手动触发扫描。
        "scanner": {"interval_minutes": 3},
        # 桌面浮窗（pywebview + WebView2）。尺寸单位是 CSS 像素；
        # x / y 为 None 时交给系统居中，拖动后写回 config/widget-web.json。
        "widget": {
            "width": 260, "height": 246, "on_top": True, "x": None, "y": None,
            # 首启自注册开机自启（启动文件夹快捷方式）；右键菜单可关闭
            "autostart": True,
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 里的字典逐键覆盖，其余类型（含列表）整体替换。"""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _expand_path(value: str, base: Path) -> Path:
    s = os.path.expandvars(os.path.expanduser(str(value)))
    p = Path(s)
    if not p.is_absolute():
        p = base / p
    return p


def load_config(path: str | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    # 内置默认永远作为底：用户只写 `server:` 时，collectors 段仍然完整，
    # 不会出现「所有采集器 paths 为空 → 静默扫不到任何数据」。
    data = _builtin_defaults()
    if p.exists():
        user = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
        if not isinstance(user, dict):
            raise ValueError(f"配置文件格式不正确（应为映射）：{p}")
        data = _deep_merge(data, user)
        base = p.parent
    else:
        base = CONFIG_DIR
    # 记住配置文件位置：面板的「设置预算」要写回同一个文件
    data["_config_path"] = str(p)

    st = data.setdefault("storage", {})
    st["db_path"] = _expand_path(st.get("db_path") or (DATA_DIR / "usage.db"), base)

    for c in (data.get("collectors") or {}).values():
        if not isinstance(c, dict):
            continue
        if "paths" in c:
            c["paths"] = [_expand_path(x, base) for x in (c["paths"] or [])]
        if "db_path" in c:
            c["db_path"] = _expand_path(c["db_path"], base)

    # pricing.overrides_file 同样相对 config/ 目录展开（config.example.yaml 里写的是
    # 相对路径 pricing.overrides.yaml）：不展开的话 Pricing 会去进程 cwd 找文件，
    # 而面板引导用户改的是 config/pricing.overrides.yaml，两边对不上，自定义定价失效。
    pr = data.setdefault("pricing", {})
    if pr.get("overrides_file"):
        pr["overrides_file"] = str(_expand_path(pr["overrides_file"], base))

    return data


def save_budget(cfg: dict, monthly_usd: float | None) -> Path:
    """把 budget.monthly_usd 写回配置文件，返回写入路径。

    面板的「设置预算」用它实现「改完立即生效、不用重启」。这里做**行级编辑**
    而不是 yaml.safe_dump：用户配置文件里的注释和顺序比机器可读性重要得多。
    找不到 budget 段就自动追加一段。
    """
    path = Path(cfg.get("_config_path") or DEFAULT_CONFIG)
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = text.splitlines()
    if monthly_usd is None:
        value = "null"
    else:
        # 不能用 str(x).rstrip("0")：1000.0 会变成 "1"。按整数/小数分别格式化。
        num = float(monthly_usd)
        value = str(int(num)) if num.is_integer() else f"{num:.2f}".rstrip("0").rstrip(".")

    budget_at = next((i for i, line in enumerate(lines) if line.strip() == "budget:"), None)
    if budget_at is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [
            "# ---------- 预算提醒 ----------",
            "budget:",
            f"  monthly_usd: {value}",
        ]
    else:
        # budget 段的范围：到下一个顶层键（非空、非注释、不缩进）为止
        end = len(lines)
        for i in range(budget_at + 1, len(lines)):
            s = lines[i]
            if s.strip() and not s.startswith((" ", "\t", "#")):
                end = i
                break
        key_at = next(
            (i for i in range(budget_at + 1, end) if lines[i].strip().startswith("monthly_usd:")),
            None,
        )
        if key_at is None:
            lines.insert(budget_at + 1, f"  monthly_usd: {value}")
        else:
            indent = lines[key_at][: len(lines[key_at]) - len(lines[key_at].lstrip())]
            comment = ""
            if "#" in lines[key_at]:
                comment = "  " + lines[key_at][lines[key_at].index("#"):]
            lines[key_at] = f"{indent}monthly_usd: {value}{comment}"

    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n"：不让 Windows 把每一行都改写成 CRLF，否则一次改预算
    # 会在 git diff 里显示「整个文件都变了」。
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    cfg.setdefault("budget", {})["monthly_usd"] = monthly_usd
    return path


def ui_timezone(cfg: dict):
    from zoneinfo import ZoneInfo

    name = ((cfg.get("ui") or {}).get("timezone")) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        from datetime import timezone

        return timezone.utc
