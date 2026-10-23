# -*- mode: python ; coding: utf-8 -*-
# TokenWidget.exe —— 无控制台桌面浮窗。
#
# 与 TokenDashboard.spec 的关键差异：这里必须把 pywebview / pythonnet 的运行时
# 一起打进去，否则 exe 双击后 ImportError（`import webview` 在 run_widget 内部
# 延迟导入，PyInstaller 的静态分析看不到它）。
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [('src/token_dashboard/ui', 'token_dashboard/ui'), ('data/pricing/litellm.json', 'token_dashboard/assets')]
datas += collect_data_files('tzdata')
# pywebview 的 WebView2 互操作 DLL（webview/lib）与 pythonnet 的运行时都是数据文件，
# 不显式收会漏；hook-clr / hook-clr_loader / hook-webview / hook-pystray 负责其余的
# 二进制与动态库（contrib hooks 里已内置）。
datas += collect_data_files('webview')
datas += collect_data_files('pythonnet')

hiddenimports = (
    # webview 的各平台后端是运行时按需 import 的
    collect_submodules('webview')
    + collect_submodules('clr_loader')
    # pystray 的托盘后端同样按平台动态选择
    + collect_submodules('pystray')
    + [
        'clr',
        'pythonnet',
        'System',
        'System.Windows.Forms',
        'System.Drawing',
        # widget_web.run_widget 里的延迟 import，静态分析扫不到
        'token_dashboard.widget_web',
    ]
)

# 版本号由 packaging/bump_version.py 生成（CI 里按 tag 注入）；本地没有该文件时跳过
_version_file = Path('packaging/version_info_widget.txt')
_version = str(_version_file) if _version_file.exists() else None

# 自定义清单：声明支持到 Windows 10/11、PerMonitorV2 DPI 感知、长路径、UTF-8 代码页。
# PyInstaller 默认清单没有 dpiAware，125%/150% 缩放下整个窗口会被位图拉伸、字发虚。
_manifest = Path('packaging/app.manifest')
_manifest_arg = str(_manifest) if _manifest.exists() else None


a = Analysis(
    ['packaging/entry_widget.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TokenWidget',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir='.',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version,
    manifest=_manifest_arg,
)
