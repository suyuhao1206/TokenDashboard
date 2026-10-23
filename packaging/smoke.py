"""exe 冒烟测试：进程能起、命令能跑、版本对得上。

CI 与本地 build_exe.ps1 共用。TokenWidget.exe 是 GUI，CI 无桌面会话起不来，
所以这里只测命令行版；浮窗的实机验证仍留在 RELEASE.md 的 checklist 里。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TIMEOUT = 120


def run(exe: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        timeout=TIMEOUT,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace") + (
        proc.stderr or b""
    ).decode("utf-8", "replace")
    return proc.returncode, out.strip()


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if len(argv) != 2:
        print("用法：python packaging/smoke.py <TokenDashboard.exe>")
        return 2
    exe = Path(argv[1])
    if not exe.exists():
        print(f"[x] 找不到 {exe}")
        return 1

    failed = 0

    code, out = run(exe, "--version")
    print(f"--version -> {code} | {out}")
    if code != 0 or "token-dashboard" not in out:
        failed += 1

    code, out = run(exe, "--help")
    print(f"--help    -> {code} | {out.splitlines()[0] if out else ''}")
    if code != 0:
        failed += 1

    # 空库不应该崩：CI 上就是全新目录
    code, out = run(exe, "report")
    print(f"report    -> {code} | {out.splitlines()[0] if out else ''}")
    if code != 0:
        failed += 1

    if failed:
        print(f"[x] {failed} 项冒烟测试未通过")
        return 1
    print("[ok] 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
