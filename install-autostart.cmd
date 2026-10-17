@echo off
rem 把 TokenWidget.exe 的快捷方式放进「启动」文件夹（开机自启）
setlocal
set "HERE=%~dp0"
set "TARGET=%HERE%dist\TokenWidget.exe"
if not exist "%TARGET%" (
  echo [x] 找不到 %TARGET%
  echo     请先运行 build_exe.ps1 生成 exe。
  pause
  exit /b 1
)
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(([Environment]::GetFolderPath('Startup')) + '\TokenWidget.lnk');" ^
  "$s.TargetPath = '%TARGET%';" ^
  "$s.WorkingDirectory = '%HERE%dist';" ^
  "$s.Description = 'Token Dashboard 桌面小组件';" ^
  "$s.Save(); Write-Host '[ok] 已加入开机自启：' ([Environment]::GetFolderPath('Startup')) '\TokenWidget.lnk'"
endlocal
pause
