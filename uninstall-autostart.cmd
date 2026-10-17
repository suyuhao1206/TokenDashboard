@echo off
rem 取消开机自启（删除「启动」文件夹里的快捷方式）
powershell -NoProfile -Command ^
  "$p = ([Environment]::GetFolderPath('Startup')) + '\TokenWidget.lnk';" ^
  "if (Test-Path $p) { Remove-Item $p -Force; Write-Host '[ok] 已移除' $p } else { Write-Host '[i] 未找到' $p }"
pause
