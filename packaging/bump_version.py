"""版本号单一来源注入。

用法：python packaging/bump_version.py v0.2.0   # 显式指定版本号
      python packaging/bump_version.py          # 用 __init__.py 里的当前版本号

把版本号同步写进两处：
1. pyproject.toml 的 `version = "..."`
2. src/token_dashboard/__init__.py 的 `__version__ = "..."`

顺带生成 packaging/version_info.txt，供 PyInstaller 写进 exe 文件属性
（TokenDashboard.spec / TokenWidget.spec 里用 version= 引入）。
不带参数运行也很重要：check_exe.py 要求 exe 里必须有版本资源，而
version_info*.txt 在 .gitignore 里不进仓库——CI 手动触发（workflow_dispatch）
或全新 clone 后直接跑 build_exe.ps1 时都没有 tag 可传，这时只有靠
「读 __init__.py 当前版本号」来生成版本资源文件，否则 PyInstaller 构建必败。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "token_dashboard" / "__init__.py"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"
VERSION_INFO_WIDGET = ROOT / "packaging" / "version_info_widget.txt"


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


def write_version_info(version: str, exe_name: str = 'TokenDashboard',
                       desc: str = 'Token Dashboard - 本地 Token 用量面板',
                       path: Path | None = None) -> None:
    """PyInstaller 的 VSVersionInfo 文本（exe 右键属性里能看到版本与系统要求）。

    两个 exe 各写一份：两个 spec 各自引用自己的文件，这样「属性 → 详细信息」
    里的 OriginalFilename 不会都写成 TokenDashboard.exe。
    """
    out = path or VERSION_INFO
    parts = [int(p) for p in re.findall(r"\d+", version)[:3]]
    while len(parts) < 3:
        parts.append(0)
    a, b, c = parts
    out.write_text(
        f"""# 由 packaging/bump_version.py 生成，请勿手改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, 0),
    prodvers=({a}, {b}, {c}, 0),
    # OS=0x40004 (VOS__WINDOWS32) + fileType=0x1 (VFT_APP)：
    # 「属性 → 详细信息」里显示为 Windows 应用，而不是「MS-DOS 程序」。
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('FileDescription', '{desc}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductName', 'Token Dashboard'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('InternalName', '{exe_name}'),
        StringStruct('OriginalFilename', '{exe_name}.exe'),
        # 系统要求写在 exe 属性里，用户拿到 zip 不用翻 README 就能看到
        StringStruct('Comments', '需要 Windows 10 1809 (build 17763) 或更高版本，64 位；桌面浮窗需要 WebView2 运行时'),
        StringStruct('LegalCopyright', '')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def current_version() -> str:
    """不传参数时的兜底：读 __init__.py 里的当前版本号。"""
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("__init__.py 里找不到 __version__ = \"...\"，无法确定当前版本号")
    return m.group(1)


def main(argv: list[str]) -> int:
    # CI（windows-latest / GitHub Actions）的控制台是 cp1252，中文 print 会
    # UnicodeEncodeError 直接把整个发版流水线打断。所有输出统一转 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    if len(argv) > 2:
        print(__doc__)
        return 2
    version = normalize(argv[1]) if len(argv) == 2 else current_version()
    write_pyproject(version)
    write_init(version)
    write_version_info(version, "TokenDashboard", "Token Dashboard - 本地 Token 用量面板")
    write_version_info(
        version, "TokenWidget", "Token Dashboard - 桌面浮窗", VERSION_INFO_WIDGET
    )
    print(f"版本号已同步为 {version}（pyproject.toml / __init__.py / version_info*.txt）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
