"""把 PE 头里的「子系统版本」改成目标 Windows 版本。

PyInstaller 直接沿用 bootloader 的 6.0（Vista）值，不会因为 manifest 里声明了
Win10 就自动改。虽然多数情况下 manifest 的 supportedOS 才是决定行为的那一项，
但 PE 头里的 subsystem version 仍是「这个程序要求的最低系统」的正式声明：
留 6.0 等于对外说「我支持 Vista」，系统在做兼容性判断时会参考它。

用法（构建后自动跑，也可以手动）：
    python packaging/patch_subsystem.py 10.0 dist/TokenDashboard.exe
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def patch(path: Path, major: int, minor: int) -> tuple[tuple[int, int], tuple[int, int]]:
    data = bytearray(path.read_bytes())
    if data[:2] != b"MZ":
        raise SystemExit(f"{path.name} 不是 PE 文件")
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise SystemExit(f"{path.name} PE 签名缺失")
    opt = pe_off + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise SystemExit(f"{path.name} 可选头 magic 异常：0x{magic:x}")

    off_major = opt + 48
    off_minor = opt + 50
    old = (
        struct.unpack_from("<H", data, off_major)[0],
        struct.unpack_from("<H", data, off_minor)[0],
    )
    struct.pack_into("<H", data, off_major, major)
    struct.pack_into("<H", data, off_minor, minor)
    path.write_bytes(data)
    return old, (major, minor)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("用法：python packaging/patch_subsystem.py <major.minor> <exe> [exe...]")
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ver = argv[1]
    try:
        major, minor = (int(x) for x in ver.split("."))
    except ValueError:
        print(f"版本号格式不对：{ver}")
        return 2
    for raw in argv[2:]:
        p = Path(raw)
        if not p.exists():
            print(f"[x] {p} 不存在")
            return 1
        old, new = patch(p, major, minor)
        print(f"[ok] {p.name}：子系统版本 {old[0]}.{old[1]} -> {new[0]}.{new[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
