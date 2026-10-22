"""版本号单一来源注入。

用法：python packaging/bump_version.py v0.2.0

把 tag 里的版本号同步写进两处：
1. pyproject.toml 的 `version = "..."`
2. src/token_dashboard/__init__.py 的 `__version__ = "..."`

顺带生成 packaging/version_info.txt，供 PyInstaller 写进 exe 文件属性
（TokenDashboard.spec / TokenWidget.spec 里用 version= 引入；文件不存在时
spec 会自动跳过，所以本地不跑这个脚本也能构建）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "token_dashboard" / "__init__.py"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"


def normalize(tag: str) -> str:
    """v0.2.0 → 0.2.0；也接受已经是 0.2.0 的形式。"""
    v = tag.strip()
    if v.startswith("v"):
        v = v[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-+]+)?", v):
        raise SystemExit(f"版本号格式不对：{tag!r}（期望 v1.2.3）")
    return v


def write_pyproject(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new, n = re.subn(r'(?m)^version\s*=\s*"[^"]*"', f'version = "{version}"', text, count=1)
    if n != 1:
        raise SystemExit("pyproject.toml 里找不到 version = \"...\"")
    PYPROJECT.write_text(new, encoding="utf-8")


def write_init(version: str) -> None:
    text = INIT.read_text(encoding="utf-8")
    new, n = re.subn(r'(?m)^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', text, count=1)
    if n != 1:
        raise SystemExit("__init__.py 里找不到 __version__ = \"...\"")
    INIT.write_text(new, encoding="utf-8")


def write_version_info(version: str) -> None:
    """PyInstaller 的 VSVersionInfo 文本（exe 右键属性里能看到版本）。"""
    parts = [int(p) for p in re.findall(r"\d+", version)[:3]]
    while len(parts) < 3:
        parts.append(0)
    a, b, c = parts
    VERSION_INFO.write_text(
        f"""# 由 packaging/bump_version.py 生成，请勿手改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, 0),
    prodvers=({a}, {b}, {c}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('FileDescription', 'Token Dashboard'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductName', 'Token Dashboard'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('LegalCopyright', '')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    # CI（windows-latest / GitHub Actions）的控制台是 cp1252，中文 print 会
    # UnicodeEncodeError 直接把整个发版流水线打断。所有输出统一转 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    if len(argv) != 2:
        print(__doc__)
        return 2
    version = normalize(argv[1])
    write_pyproject(version)
    write_init(version)
    write_version_info(version)
    print(f"版本号已更新为 {version}（pyproject.toml / __init__.py / version_info.txt）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
