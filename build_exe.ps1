# 打包两个 exe：TokenWidget.exe（无控制台小组件）与 TokenDashboard.exe（命令行/面板）
# 用法：pwsh -File build_exe.ps1              （需要 uv，首次会下载 PyInstaller）
#       pwsh -File build_exe.ps1 -Version v0.2.0   （同时注入版本号，与 CI 行为一致）
#
# 为什么走 .spec 而不是命令行参数：
#   pywebview 的 WebView2 互操作 DLL、pythonnet 的运行时 DLL 都是**数据文件**，
#   而且 `import webview` 写在 run_widget() 内部（延迟导入），PyInstaller 的静态分析
#   看不见它。用命令行参数构建时这些全都会漏掉——打出来的 exe 双击后 ImportError。
#   这些配置只能写在 spec 里，所以两个目标各自用对应的 spec 文件构建。
#
# 构建后必做两步（见「Windows 适配」注释）：
#   1. patch_subsystem.py 10.0   —— PE 头声明最低 Win10
#   2. check_exe.py              —— 校验 x64 / DPI 感知 / 版本资源
param(
    [string]$Version = ""
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

uv sync

# 不传 -Version 也要生成 version_info*.txt：它们不进 Git 仓库，而 check_exe.py
# 要求 exe 必须带版本资源。脚本会读 __init__.py 的当前版本号（与 CI 手动触发同款兜底）。
Write-Host '== 注入版本号 =='
if ($Version) {
    uv run python packaging/bump_version.py $Version
} else {
    uv run python packaging/bump_version.py
}

Write-Host '== 构建 TokenWidget.exe（无控制台） =='
uv run pyinstaller --noconfirm --clean TokenWidget.spec

Write-Host '== 构建 TokenDashboard.exe（控制台：scan/report/serve） =='
uv run pyinstaller --noconfirm --clean TokenDashboard.spec

Write-Host '== Windows 适配：PE 子系统版本 6.0 -> 10.0 =='
uv run python packaging/patch_subsystem.py 10.0 dist\TokenWidget.exe dist\TokenDashboard.exe

Write-Host '== 构建产物校验（x64 / DPI / 长路径 / 版本资源） =='
uv run python packaging/check_exe.py dist\TokenWidget.exe dist\TokenDashboard.exe
if ($LASTEXITCODE -ne 0) {
    throw '构建产物校验未通过，见上面的报错'
}

Write-Host ''
Write-Host '== 冒烟测试 =='
uv run python packaging/smoke.py dist\TokenDashboard.exe

Write-Host ''
Write-Host '完成：dist\TokenWidget.exe 与 dist\TokenDashboard.exe'
Write-Host '提示：把 dist 整个目录拷到任意位置（例如 D:\Tools\token-dashboard），exe 会在同目录创建 data\ 与 config\。'
Write-Host '      打包后请务必双击 TokenWidget.exe 验证浮窗能起（静态检查看不出的 WebView2 运行时问题只有实机能发现）。'
Write-Host '      发布 zip 只能白名单收集两个 exe + 快速上手.txt，绝不能整包 dist（含 usage.db / 日志 / WebView2 profile）。'
