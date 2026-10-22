# 路线图

> 每个里程碑完成即可用；M1 不依赖任何密钥。

## M0 · 评估与骨架 ✅（2026-10-15）

- [x] 本机数据源实测（Claude Code / Codex / DSH / cc-switch / API 核对）
- [x] 可行性评估与架构设计（docs/）
- [x] 项目目录与配置示例

## M1 · 本地日志聚合（Claude Code + Codex）→ CLI 报表

目标：一条命令看到两家的日/周/月/模型/项目维度用量与成本。

- [x] 项目骨架（uv + 包结构 + 配置加载）
- [x] 价格表加载（LiteLLM 快照下载 + overrides + 未定价标记）
- [x] Claude Code 解析器（`message.id + requestId` 去重、无 requestId 逐条计、cache_creation 明细）
- [x] Codex 解析器（新旧两种格式、累计增量口径、首事件用 last、单调化）
- [x] SQLite 入库（幂等：文件 mtime/size 跳过 + dedup_key 唯一 + 时间戳守卫）
- [x] CLI：`scan` / `report --json` / `update-pricing` / `serve`
- [x] 验收：与 `npx ccusage` 的 Claude Code / Codex 数字对齐（Claude 3/3 天完全一致；Codex 逐日核对仅 3 天 <0.1% 差异）
- [ ] 解析器单元测试（真实样本 + 异常样本）

## M2 · Web 面板

- [x] FastAPI 服务 + 单页前端（概览卡 + 日明细表 + 趋势/分布/热力图/请求分页表）
- [x] 概览卡（今日 / 本周 / 本月 tokens & 成本）
- [x] 趋势图（按天堆叠：harness / 模型）
- [x] 分布图（模型环图、项目排行）
- [x] 会话明细表 + 筛选（时间 / harness / 模型 / 项目）
- [x] `serve` 常驻（默认启动前自动扫描，可用 `--no-scan` 关闭；之后每 3 分钟后台定时扫描）
- [x] 面板自动更新：30 秒静默轮询 + 标签页可见性暂停（不重置筛选与页码）+「更新于」时间戳
- [x] 桌面小组件（pywebview + WebView2，同进程内嵌服务、复用面板设计系统；`start-widget.vbs` 无控制台启动）
- [x] 验收：打开 `http://127.0.0.1:8787` 即见全部数据，刷新即增量

## M3 · 渠道维度

- [x] cc-switch 只读适配器（providers 注册表 + usage_daily_rollups + proxy_request_logs 兜底）→ 独立 `channel_daily` 表 + 面板「渠道视图」
- [ ] 渠道归属：时间线近似（读 cc-switch 切换记录，把用量归到当时激活的渠道）
- [ ] 对账视图（本地日志 vs 渠道数据，差异提示）
- [ ] （可选）OpenRouter / Anthropic / OpenAI 渠道 API
- [ ] （可选）内置代理模式或引导开启 cc-switch 代理
- [ ] 验收：任一时刻能回答「这个月我在各渠道分别花了多少」
      —— 现状：cc-switch 自己的历史归因也很粗（大部分只能标为 `会话日志·codex/claude`），
      精确到渠道需要它重新开启代理（见 `docs/data-sources.md` B1）

## M4 · 扩展与体验

- [x] DSH 适配器（zstd 解压 + v4 解析：`assistant/message` 的 `usage`，模型取 `model/selection`）
- [x] WorkBuddy 适配器（`~/.workbuddy/projects/**/*.jsonl` 的 `providerData.usage/rawUsage`）
- [x] 小组件迁到 pywebview + WebView2：浮窗与面板共用 `shared.css` / `shared.js`，
      同进程内嵌服务（端口被占用时复用已有 serve），右侧深色浮窗零图表库
- [x] PyInstaller 打包 exe（`build_exe.ps1` → `TokenWidget.exe` / `TokenDashboard.exe`，走 spec 收全 pywebview 运行时）
- [ ] 旧 tkinter 浮窗的皮肤 / 动图 / 鼠标穿透（随 tkinter 实现一起下线，需要时在新浮窗上重做）
- [ ] 全局热键显隐浮窗
- [ ] Antigravity（优先走 ccusage 桥接）
- [ ] ccusage 桥接适配器（长尾 harness，消费 `--json`）
- [x] 预算与提醒（`budget.monthly_usd` → 状态条本月用量 chip，80% / 100% 分级告警）
- [x] 导出（请求表「导出 CSV」，UTF-8 BOM，Excel 直接打开）
- [ ] 导出 JSON / 分享图
- [ ] 手动导入（Trae / Copilot 等）
- [x] 发版链路：GitHub Actions 打 tag 自动构建 + 冒烟测试 + 白名单 zip 发 Release；
      默认开机自启（首启自注册，菜单可关）；版本号单一来源注入
- [x] 体验修复（2026-10-21 审查）：KPI 跟随筛选、图表骨架屏、表头排序、模型筛选、
      加载失败重试、渠道筛选与趋势图、widget 菜单状态 / 迷你柱深链 / Esc 二次确认
- [ ] 验收：覆盖本机全部活跃 harness 与渠道

## 开放问题（待确认）

1. 技术栈：Python（推荐）还是 TypeScript？
2. 渠道归因：是否接受开启 cc-switch 代理以获得逐请求归属？（最准，但需保持开启）
3. DSH 旧会话字段差异的兼容范围
4. 成本口径：订阅制是否按价表估算并标注？（默认：是）
