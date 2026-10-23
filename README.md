# token-dashboard

> 多渠道 × 多 Harness 的本地 Token 用量聚合面板 —— 一处看清「总量 + 分渠道 + 分 harness + 分模型 + 成本」。

**状态：M1 已完成、M2 面板可用、M4 部分功能已落地（2026-10-21）** ｜ Python 3.12 + FastAPI + SQLite ｜ Claude Code 数字与 ccusage 完全一致，Codex 逐日对齐（含推理口径）

---

## 下载即用（Windows x64）

到 [Releases](https://github.com/suyuhao1206/TokenDashboard/releases) 下载最新的
`TokenDashboard-vX.Y.Z-windows-x64.zip`：

1. 解压到任意目录
2. 双击 `TokenWidget.exe` —— 右下角出现浮窗即成功
3. 浏览器打开 http://127.0.0.1:8787 看完整面板

不需要安装 Python / uv / 任何依赖。**默认已开启开机自启**（右键浮窗可一键关闭）。
未签名软件首次会被 SmartScreen 拦一次：点「更多信息」→「仍要运行」——这是未签名
PyInstaller exe 的必然提示，不是 bug。

### 系统要求

| 项 | 要求 |
|---|---|
| 操作系统 | **64 位 Windows 10 1809（build 17763）及以上**，或 Windows 11 |
| 运行时 | 浮窗需要 **Microsoft Edge WebView2**（Win11 与新版 Win10 自带；缺失时程序会弹窗给下载链接） |
| 命令行 | `scan` / `report` / `serve` / `update-pricing` **不需要** WebView2，老系统上也能跑 |

**不支持 Windows 7 / 8 / 8.1**：WebView2 运行时已停止支持这些系统，浮窗无法显示。
程序启动时会先检查系统版本与 WebView2，不满足时弹中文提示并写进 `data/widget.log`，
不会出现「双击没反应、也不知道为什么」。

两个 exe 的清单（manifest）已声明：Windows 10/11 兼容、**PerMonitorV2 高 DPI 感知**
（125%/150% 缩放下界面清晰不糊）、长路径支持、UTF-8 代码页、`asInvoker`（不请求管理员权限）。
PE 头的子系统版本也写成了 10.0，不再对外声明「支持 Vista」。

## 为什么要做

1. **调用渠道分散**：官方 API、中转站、聚合平台各看各的后台，看不到总账；同一个 harness 还会在不同渠道间来回切换（实测：Claude Code 7 月 4 日走的是 DeepSeek 模型，7 月下旬走 Anthropic 模型）。
2. **Harness 分散**：Claude Code、Codex、DSH、Antigravity、Trae…… 每家只统计自己，没有跨工具的全局视图。

目标：一个本地面板，汇总**所有渠道 × 所有 harness** 的 token 用量与成本，按天/周/月、按渠道、按 harness、按模型任意切片，支持预算提醒。

## 可行性结论：可行 ✅

本机（Windows）实测记录：

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | Claude Code 本地日志 | ✅ `~/.claude/projects/**/*.jsonl`，150 个会话文件；每条消息含 `input_tokens / output_tokens / cache_read / cache_creation` 字段 |
| 2 | Codex 本地日志 | ✅ `~/.codex/sessions/**`，112 + 2 个会话文件；`token_usage_record` 事件含单次/单回合/累计 usage（含 reasoning、cache 字段） |
| 3 | 现成工具验证 | ✅ `npx ccusage` 在本机直接跑通 Claude Code 与 Codex 的日报（含成本估算与 JSON 输出） |
| 4 | 渠道侧数据 | ✅ cc-switch 数据库：15 个渠道配置 + 4,727 条逐请求日志（含 token、成本、渠道归属）+ 按天汇总 + 177 条模型价格 |
| 5 | DSH 会话 | ✅ 会话文件（zstd 压缩）解压后确认含 `usage.{inputTokens, outputTokens, totalTokens, cacheReadTokens}` 字段 |
| 6 | 官方渠道 API | ✅ OpenRouter `GET /api/v1/activity`、Anthropic Usage & Cost API 均存在（需管理密钥，接入时核对细节） |

详细评估：`docs/feasibility.md` ｜ 数据源全表：`docs/data-sources.md`

## 快速开始（M1 已完成）

```bash
cd token-dashboard
uv sync                          # 首次：创建环境并安装依赖
uv run token-dashboard scan      # 扫描本地日志入库（幂等，可反复运行）
uv run token-dashboard report --since 2026-07-01   # CLI 日报
uv run token-dashboard serve     # 启动 Web 面板 → http://127.0.0.1:8787
```

可选：`uv run token-dashboard update-pricing` 下载 LiteLLM 价格表并重算成本。

### 桌面小组件（pywebview + WebView2）

```bash
uv run token-dashboard widget                # 无边框浮窗
uv run token-dashboard widget --debug        # 打开 DevTools 调试前端
uv run token-dashboard widget --no-tray      # 不要系统托盘
```

- **同一个进程**：内嵌 uvicorn，浮窗和面板从此不分家。端口 8787 上已有服务（比如你手动起了
  `serve`）就直接复用，不会端口冲突
- **同一套设计系统**：`widget.html` 引用面板的 `shared.css` / `shared.js`，视觉天然一致；
  浮窗**不加载 ECharts**（7 日迷你柱是 7 个 flex div），省内存
- 显示：今日 / 近 7 天 tokens + 成本、7 日迷你柱、各 harness 用量、未定价提示
- 交互：拖动标题栏 · 双击打开面板 · **点迷你柱直接打开面板并筛选到那一天** ·
  右键菜单（刷新 / 打开面板 / 置顶 / **开机自启**，两项都带 ✓ 勾选态）· `Ctrl+R` 刷新
- **`Esc` 退出是二次确认**（先提示「再按一次 Esc 退出」，2 秒内再按才退出），避免误触
- 位置与置顶状态记在 `config/widget-web.json`（`config.yaml` 的 `widget:` 段可覆盖尺寸与置顶）
- **开机自启**：首次启动自动在「启动」文件夹建 `TokenWidget.lnk`（`widget.autostart: false` 可关掉这个默认行为）；
  右键菜单可随时开关，exe 换目录后会按新路径自动重建
- 日志默认写 `data/widget.log`（`--log` 可改）：无控制台进程看不到 stdout，出问题先看这里

### 打包成 exe / 发版

```powershell
pwsh -File build_exe.ps1                # 本地构建两个 exe
pwsh -File build_exe.ps1 -Version v0.2.0  # 顺便注入版本号（与 CI 一致）
```

- `TokenWidget.exe`：无控制台，双击即出现小组件
- `TokenDashboard.exe`：命令行版（`scan` / `report` / `serve` / `update-pricing` / `widget` / `--version`）
- 把 `dist` 整个目录拷到任意位置即可（例如 `D:\Tools\token-dashboard`）：exe 会在**同目录**创建
  `data\`（数据库/价格表/日志/WebView2 profile）与 `config\`（widget-web.json）
- 构建走 `.spec` 而不是命令行参数：`pywebview` 的 WebView2 互操作 DLL 与 `pythonnet` 运行时是
  **数据文件**，命令行的 `--collect-data` 收不全，打出来的 exe 会 ImportError
- **发版**：`git tag v0.2.0 && git push origin v0.2.0`，`.github/workflows/release.yml`
  自动构建、冒烟测试、打白名单 zip 并发 GitHub Release。checklist 见 `RELEASE.md`
- ⚠️ 发布 zip 只收 `TokenWidget.exe` + `TokenDashboard.exe` + `packaging/快速上手.txt`：
  `dist/data/` 里有真实用量库与日志，**不能整包发布**
- 旧的手工自启脚本（`install-autostart.cmd` / `uninstall-autostart.cmd`）保留作备用，
  日常不需要再用——widget 自己会注册

## 当前实现状态（2026-10-21）

- **采集**：Claude Code（150 文件 → 2,497 条）、Codex（114 → 5,930）、**DSH（29 → 2,401）**、**WorkBuddy（71 → 7,255）**；合计 17,828+ 条事件 / **26.8 亿 tokens**
- **Token 口径**：`output_tokens` 一律为「含推理的总输出」，`reasoning_tokens` 只作展示、不重复计入合计与成本
  （2026-10-21 修正：此前 Codex / WorkBuddy 在推理已含于 output 的记录上又加了一次，总量与成本都偏高；修正后 2026-05-25 与 ccusage 逐项一致）
- **存储**：SQLite（`data/usage.db`）；重复扫描不重复计数（文件 mtime/size 跳过 + `dedup_key` 唯一 + 时间戳守卫）；
  去重键与文件**物理路径解耦**（Codex 归档、项目目录改名都不会重复入库，见 `docs/architecture.md` §5.1）；
  写入侧开 WAL + `busy_timeout`，面板请求线程与后台扫描线程可并发读写
- **进程模型**：一个进程 = 内嵌 uvicorn + 一个 WebView2 浮窗（约 130 MB）；面板是同一进程里的网页，
  不额外起服务。`cmd_serve` 与 `widget` 都先扫一轮再定时扫
- **面板**：`http://127.0.0.1:8787` —— 5 张 KPI 卡（今日 / 近 7 天 / 近 30 天 / 缓存命中率 / 累计成本，含环比；
  **有筛选时额外显示「所选范围」卡**）、工作回溯热力图（近 365 天，四分位分级）、每日趋势（按 harness / 模型堆叠）、
  活跃时段、模型分布、项目 Top10、最近请求分页表（页大小 10/20/50/100、**点表头排序**、**按模型筛选**、**导出 CSV**）、
  会话明细、日明细；带时间范围 / harness / 模型筛选（起止日期填反会自动互换）
- **数据自己保持新鲜**：`serve` / `widget` 启动时先扫一轮，之后每 3 分钟（`scanner.interval_minutes`）
  后台扫描一次；面板每 30 秒静默轮询（标签页切走就暂停），**轮询不碰筛选条件、不重置请求表页码**
- **渠道视图（M3 起步）**：只读接入 cc-switch，把渠道汇总写入独立表并以独立面板区块呈现（**不与 harness 总量合并**，避免重复计数）。
  支持按渠道筛选 + 渠道成本趋势图
- **成本**：`update-pricing` 后已为大部分事件算出成本；`budget.monthly_usd` 配置月度预算后，状态条会显示本月用量
  （<80% 绿 / 80-100% amber / 超支红）
- **对齐 ccusage**：Claude Code 基准日**完全一致**；Codex 逐日对齐（2026-05-25 输出 11,587 / 推理 6,818 /
  总量 393,488 / 成本 $0.909275 全部一致）

对照参考（ccusage，无需安装）：

```bash
npx ccusage@latest claude daily --since 2026-06-01 --timezone Asia/Shanghai
npx ccusage@latest codex  daily --since 2026-08-01 --timezone Asia/Shanghai
```

## 设计要点（一句话版）

**本地优先、全离线；适配器采集 → 统一口径 → SQLite → 面板 / CLI；每条流只认一个权威源以避免重复计数；复用 ccusage（长尾 harness）与 cc-switch（渠道注册表 + 历史渠道日志）。**

- 采集层：每类数据源一个适配器（Claude Code / Codex / DSH / cc-switch / 渠道 API / ccusage 桥接 / 手动导入）
- 存储层：单文件 SQLite，增量 + 幂等（重复运行不重复计数）
- 展示层：Web 面板（FastAPI + 轻量前端）+ CLI 报表
- 成本：内置价格表（LiteLLM 快照）+ 别名模型覆盖 + 渠道倍率；无法定价的模型显式标记

## 数据源一览（本机实测）

| 来源 | 类型 | 位置 / 接口 | 状态 |
|------|------|------------|------|
| Claude Code | 本地日志 | `~/.claude/projects/**/*.jsonl` | ✅ 已实测 |
| Codex | 本地日志 | `~/.codex/sessions/**`（+ `archived_sessions`） | ✅ 已实测 |
| cc-switch | 本地数据库 | `~/.cc-switch/cc-switch.db`（只读） | ✅ 已实测 |
| DSH | 本地日志（zstd） | `~/.dsh/sessions/**/session.v4.jsonl.zstd` | ✅ 字段已确认 |
| Antigravity | 本地数据 | `~/.gemini/antigravity/**` | ⚠️ 待验证（ccusage 已支持） |
| 其他 CLI（Kimi/Qwen/OpenCode…） | 本地日志 | 各工具目录 | ⚠️ 可走 ccusage 桥接 |
| OpenRouter | 渠道 API | `GET /api/v1/activity` 等 | ⚠️ 需管理密钥 |
| Anthropic 官方 | 渠道 API | Usage & Cost API（Admin） | ⚠️ 需 Admin 密钥 |
| 中转站（one-api/new-api 系） | 渠道 API | 管理日志接口 | ⚠️ 视站点而定 |
| Trae / VS Code Copilot | — | 无本地明细 | ❌ 降级为手动导入 |

## 路线图

- **M1 · 本地日志聚合**：Claude Code + Codex 解析器 → SQLite → CLI 日报/周报/月报（含成本）
- **M2 · Web 面板**：概览卡片、趋势图、模型/项目分布、会话明细、筛选
- **M3 · 渠道维度**：cc-switch 接入（渠道注册表 + 历史日志）、可选渠道 API、多源对账
- **M4 · 扩展**：DSH、Antigravity、ccusage 长尾、预算提醒、CSV/JSON 导出

详见 `docs/roadmap.md`。

## 目录结构

```
token-dashboard/
├── README.md
├── docs/
│   ├── feasibility.md      # 可行性评估（含本机实测记录）
│   ├── data-sources.md     # 数据源矩阵与字段细节
│   ├── architecture.md     # 架构设计（模块/数据模型/去重口径）
│   └── roadmap.md          # 里程碑与验收标准
└── config/
    └── config.example.yaml # 配置示例
```

## 非目标（明确不做）

- 不上传任何数据、不做云同步、不做遥测
- 不模拟登录抓取各平台网页后台（脆弱且有账号风险）
- 不读取、不展示会话内容；只提取统计字段（token 数、模型名、时间戳等）

## 下一步

1. 确认技术栈（建议：Python 3.12 + FastAPI + SQLite；本机 Python / uv / Node 均已就绪）
2. 搭建 M1 骨架：项目结构 + Claude Code / Codex 解析器 + CLI 报表 + 价格表
