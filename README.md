# token-dashboard

> 多渠道 × 多 Harness 的本地 Token 用量聚合面板 —— 一处看清「总量 + 分渠道 + 分 harness + 分模型 + 成本」。

**状态：M1 已完成、M2 面板可用（2026-10-15）** ｜ Python 3.12 + FastAPI + SQLite ｜ Claude Code 数字与 ccusage 完全一致，Codex 仅 3 天 <0.1% 差异

---

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

### 桌面小组件（可选，直读本地库、不需要开面板）

```bash
uv run token-dashboard widget                    # 无边框浮窗
uv run token-dashboard widget --interval 30 --auto-scan 5
uv run token-dashboard widget --self-test        # 自检：取一次数后退出
```

- 显示：今日 / 近 7 天 / 近 30 天 / 全部 的 tokens + 成本，底部 7 日迷你柱状图 + 各 harness 用量
- 交互：拖动移动 · 双击切换置顶 · 右键菜单（刷新 / 立即扫描 / 打开面板 / 置顶 / 退出）· Esc 退出
- 位置与置顶状态记在 `config/widget.json`；日志可写 `data/widget.log`（`--log`）
- 双击 `start-widget.vbs` 可无控制台启动；把它的快捷方式放进 `shell:startup` 即开机自启
- 零新增依赖（tkinter 随 Python 提供），常驻内存约 26 MB
- **皮肤**：右键 → 设置…（或 `Ctrl+Alt+S`）可选背景图片（png/jpg/webp/**gif 动图**，可循环播放）、
  缩放方式（裁剪填满/完整显示/拉伸）、**透明度**（0.3–1.0）、圆角、字号倍率、文字/强调色
- **鼠标穿透**（纯装饰模式）：`Ctrl+Alt+T` 随时切换，穿透后仍可用该快捷键切回

### 打包成 exe（长期使用推荐）

```powershell
powershell -File build_exe.ps1   # Windows 自带 PowerShell 即可；装了 PowerShell 7 也可以 pwsh -File
```

- `TokenWidget.exe`：无控制台，双击即出现小组件（支持传参，如 `TokenWidget.exe --interval 30`）
- `TokenDashboard.exe`：命令行版（`scan` / `report` / `serve` / `update-pricing` / `widget`）
- 把 `dist` 整个目录拷到任意位置即可（例如 `D:\Tools\token-dashboard`）：exe 会在**同目录**创建
  `data\`（数据库/价格表/日志）与 `config\`（widget.json）
- 构建带 `--runtime-tmpdir .`：onefile 解压目录放在 exe 同目录，不依赖 `%TEMP%`（受限机器也能跑）
- **实测**：两个 exe 各约 **28.6 MB**；`TokenDashboard.exe scan/report` 正常；`TokenWidget.exe` 双击即启动、
  启动时自动扫描并显示（日志 `dist\data\widget.log`）
- **开机自启**：双击 `install-autostart.cmd`（把快捷方式写进「启动」文件夹）；取消用 `uninstall-autostart.cmd`
- 也可以手动：`Win+R` → `shell:startup` → 把 `TokenWidget.exe` 的快捷方式丢进去

## 当前实现状态（2026-10-15）

- **采集**：Claude Code（150 文件 → 2,497 条）、Codex（114 → 5,930）、**DSH（29 → 2,401）**、**WorkBuddy（71 → 7,255）**；合计 17,828+ 条事件 / **26.8 亿 tokens**
- **存储**：SQLite（`data/usage.db`）；重复扫描不重复计数（文件 mtime/size 跳过 + `dedup_key` 唯一 + 时间戳守卫）；
  去重键与文件**物理路径解耦**（Codex 归档、项目目录改名都不会重复入库，见 `docs/architecture.md` §5.1）；
  写入侧开 WAL + `busy_timeout`，面板与小组件可并发读写
- **面板**：`http://127.0.0.1:8787` —— 5 张 KPI 卡（今日 / 近 7 天 / 近 30 天 / 缓存命中率 / 累计成本，含环比）、
  工作回溯热力图（近 365 天，四分位分级）、每日趋势（按 harness / 模型堆叠）、活跃时段、模型分布、项目 Top10、
  最近请求分页表（页大小 10/20/50/100）、会话明细、日明细；带时间范围与 harness 筛选，60 秒自动刷新
- **渠道视图（M3 起步）**：只读接入 cc-switch，把渠道汇总写入独立表并以独立面板区块呈现（**不与 harness 总量合并**，避免重复计数）。注意 cc-switch 自身的历史归因较粗，大部分只能标为「会话日志·codex/claude」
- **成本**：`update-pricing` 后已为 6,997 / 8,427 条事件算出成本（合计约 **$924**，其中 Codex $599 / Claude Code $325）
- **对齐 ccusage**：Claude Code 3/3 基准日**完全一致**；Codex 57 天中 54 天完全一致
- **已知差异**：Codex 的 2026-06-04 / 06-07 / 07-30 三天（单会话内细节口径，差异 <0.1%，原因待查，见 `docs/data-sources.md`）

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
