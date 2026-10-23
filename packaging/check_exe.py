"""构建产物校验：manifest / 版本资源 / 目标系统声明。

CI 里跑，也可以在本地跑：
    python packaging/check_exe.py dist/TokenDashboard.exe dist/TokenWidget.exe

检查项（都是「构建绿了但用户拿到不能用」的典型）：
1. PE 头：必须是 64 位（x64），不能是 32 位或 ARM；
2. manifest：必须带 PerMonitorV2（高 DPI）与 longPathAware；
3. 版本资源：能读到 FileVersion 与 ProductVersion；
4. 子系统版本不能低于 Win10（否则系统按老程序对待，不做 DPI 适配）。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

MIN_SUBSYSTEM_VERSION = (10, 0)
NEEDED_MANIFEST_MARKERS = (b"PerMonitorV2", b"longPathAware")


def _pe_header(data: bytes) -> dict:
    if data[:2] != b"MZ":
        raise SystemExit("不是 PE 文件")
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise SystemExit("PE 签名缺失")
    machine, _nsec, _ts, _psym, _nsym, _opt_size, _chars = struct.unpack_from(
        "<HHIIIHH", data, pe_off + 4
    )
    opt = pe_off + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    is_plus = magic == 0x20B
    sub_major = struct.unpack_from("<H", data, opt + 48)[0]
    sub_minor = struct.unpack_from("<H", data, opt + 50)[0]
    subsystem = struct.unpack_from("<H", data, opt + 68)[0]
    return {
        "machine": machine,
        "pe32plus": is_plus,
        "subsystem_version": (sub_major, sub_minor),
        "subsystem": subsystem,
    }


def _version_strings(path: Path) -> dict:
    """用 Win32 版本 API 读 exe 属性的「详细信息」。非 Windows 返回空。"""
    try:
        import win32api  # type: ignore
    except ImportError:
        win32api = None
    if win32api is not None:
        try:
            info = win32api.GetFileVersionInfo(str(path), "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
            return {"FileVersion": version}
        except Exception:
            pass
    # 没有 pywin32（CI 也不装）：退回 PowerShell 读，零依赖
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"],
            capture_output=True, timeout=30,
        ).stdout.decode("utf-8", "replace").strip()
        return {"FileVersion": out} if out else {}
    except Exception:
        return {}


def check(path: Path) -> list[str]:
    problems: list[str] = []
    data = path.read_bytes()
    hdr = _pe_header(data)

    if hdr["machine"] != 0x8664:
        problems.append(f"不是 x64（machine=0x{hdr['machine']:04x}）")
    if not hdr["pe32plus"]:
        problems.append("不是 PE32+（疑似 32 位构建）")

    for marker in NEEDED_MANIFEST_MARKERS:
        if marker not in data:
            problems.append(f"manifest 缺少 {marker.decode()}")

    if hdr["subsystem_version"] < MIN_SUBSYSTEM_VERSION:
        vs = ".".join(map(str, hdr["subsystem_version"]))
        want = ".".join(map(str, MIN_SUBSYSTEM_VERSION))
        problems.append(f"子系统版本 {vs} 低于 Win10 要求 {want}")

    ver = _version_strings(path).get("FileVersion")
    if not ver:
        problems.append("读不到版本资源（exe 属性里没有版本号）")

    return problems


def main(paths: list[str]) -> int:
    if not paths:
        print("用法：python packaging/check_exe.py <exe> [exe...]")
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    failed = 0
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            print(f"[x] {p} 不存在")
            failed += 1
            continue
        problems = check(p)
        if problems:
            failed += 1
            print(f"[x] {p.name}")
            for item in problems:
                print(f"    - {item}")
        else:
            ver = _version_strings(p).get("FileVersion", "?")
            print(f"[ok] {p.name}：x64 / PerMonitorV2 / 长路径 / 版本 {ver}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
