# 发布 checklist（每次发版照抄）

- [ ] 本地 `pwsh -File build_exe.ps1` 构建通过
- [ ] 发版前 `uv run pytest -q` 全绿（口径 / 排序 / 去重 / 环比窗口 / CSV 全覆盖）
- [ ] `uv run ruff check src packaging tests` 无告警
- [ ] 实机双击 `TokenWidget.exe`：浮窗起、面板开、自启注册、菜单可关自启
- [ ] **100% / 125% / 150% / 200% 缩放下各启动一次 widget，确认视口不跑偏**
  （看 `data/widget.log` 里「视口 #i」是否 3 轮内收敛到目标 260×246，±1 以内；无反复 resize）
- [ ] `TokenDashboard.exe scan / report / serve / update-pricing / --version` 全绿
- [ ] `TokenDashboard.exe report` 表头与数据列右对齐（列宽共用常量）
- [ ] 确认 `stage/` 里只有 3 个文件（无 data/、无 log）——D0 红线
- [ ] `git tag vX.Y.Z && git push --tags`，CI 绿
- [ ] 从 Release 下载 zip 到干净环境（虚拟机/另一台机）验证完整首启流程
- [ ] Release notes 写清：新功能 / 修复 / SmartScreen 说明链接

## 发版命令

```powershell
pwsh -File build_exe.ps1                 # 本地先确认能构建
git tag v0.2.0
git push origin v0.2.0                   # tag 推送后 .github/workflows/release.yml 自动构建发版
```

## 为什么不能直接压缩 dist/

`dist/data/` 里有真实 `usage.db`（全部用量）、`widget.log` 与 WebView2 profile。
`.gitignore` 只挡住它们进 git，**挡不住 zip**。发布物一律白名单：
`TokenWidget.exe` + `TokenDashboard.exe` + `packaging/快速上手.txt`。
