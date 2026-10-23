# -*- mode: python ; coding: utf-8 -*-
# TokenDashboard.exe —— 命令行（scan/report/serve/update-pricing/widget）。
#
# `widget` 子命令同样会用到 pywebview，所以这里也把对应的数据文件与隐藏导入收进来。
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [('src/token_dashboard/ui', 'token_dashboard/ui'), ('data/pricing/litellm.json', 'token_dashboard/assets')]
datas += collect_data_files('tzdata')
datas += collect_data_files('webview')
datas += collect_data_files('pythonnet')

hiddenimports = (
    collect_submodules('webview')
    + collect_submodules('clr_loader')
    + collect_submodules('pystray')
    + [
        'clr',
        'pythonnet',
        'System',
        'System.Windows.Forms',
        'System.Drawing',
        # 延迟导入，静态分析扫不到
        'token_dashboard.widget_web',
        'token_dashboard.server',
    ]
)

# 版本号由 packaging/bump_version.py 生成（CI 里按 tag 注入）；本地没有该文件时跳过
_version_file = Path('packaging/version_info.txt')
_version = str(_version_file) if _version_file.exists() else None

# 自定义清单：声明支持到 Windows 10/11、PerMonitorV2 DPI 感知、长路径、UTF-8 代码页。
# PyInstaller 默认清单没有 dpiAware，125%/150% 缩放下整个窗口会被位图拉伸、字发虚。
_manifest = Path('packaging/app.manifest')
_manifest_arg = str(_manifest) if _manifest.exists() else None


a = Analysis(
    ['packaging/entry_cli.py'],
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
    name='TokenDashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir='.',
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version,
    manifest=_manifest_arg,
)
