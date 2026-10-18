# 打包两个 exe：TokenWidget.exe（无控制台小组件）与 TokenDashboard.exe（命令行/面板）
# 用法：pwsh -File build_exe.ps1      （需要 uv，首次会下载 PyInstaller）
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

uv sync

$common = @(
  '--noconfirm', '--clean', '--onefile',
  '--runtime-tmpdir', '.',        # 解压到 exe 同目录：不依赖 %TEMP%，锁定环境的机器也能跑
  '--paths', 'src',
  '--collect-data', 'tzdata',
  '--add-data', 'src/token_dashboard/ui;token_dashboard/ui',
  '--add-data', 'data/pricing/litellm.json;token_dashboard/assets'
)

Write-Host '== 构建 TokenWidget.exe（无控制台） =='
uv run pyinstaller @common --noconsole --name TokenWidget packaging/entry_widget.py

Write-Host '== 构建 TokenDashboard.exe（控制台：scan/report/serve） =='
uv run pyinstaller @common --console --name TokenDashboard packaging/entry_cli.py

Write-Host ''
Write-Host '完成：dist\TokenWidget.exe 与 dist\TokenDashboard.exe'
Write-Host '提示：把 dist 整个目录拷到任意位置（例如 D:\Tools\token-dashboard），exe 会在同目录创建 data\ 与 config\。'
