' token-dashboard 桌面小组件启动器（无控制台）
' 优先使用打包好的 exe（dist\TokenWidget.exe），没有则回退到 venv 里的 pythonw。
' 脚本内不含中文，避免 WScript 按 ANSI 读取时乱码。
Option Explicit
Dim fso, sh, root, exePath, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
exePath = root & "\dist\TokenWidget.exe"
If fso.FileExists(exePath) Then
  cmd = """" & exePath & """"
Else
  cmd = """" & root & "\.venv\Scripts\pythonw.exe"" -m token_dashboard widget --log """ & root & "\data\widget.log"""
End If
sh.Run cmd, 0, False
