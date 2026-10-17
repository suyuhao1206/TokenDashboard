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

    st = data.setdefault("storage", {})
    st["db_path"] = _expand_path(st.get("db_path") or (DATA_DIR / "usage.db"), base)

    for c in (data.get("collectors") or {}).values():
        if not isinstance(c, dict):
            continue
        if "paths" in c:
            c["paths"] = [_expand_path(x, base) for x in (c["paths"] or [])]
        if "db_path" in c:
            c["db_path"] = _expand_path(c["db_path"], base)

    return data


def ui_timezone(cfg: dict):
    from zoneinfo import ZoneInfo

    name = ((cfg.get("ui") or {}).get("timezone")) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        from datetime import timezone

        return timezone.utc
