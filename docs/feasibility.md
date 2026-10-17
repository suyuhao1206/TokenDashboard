# 可行性评估：多渠道 × 多 Harness 的 Token 用量聚合

> 评估日期：2026-10-15 ｜ 方式：本机数据实测 + 官方文档核对 ｜ 结论：**可行**

## 1. 结论

可行，且已有现成轮子可复用（ccusage、cc-switch 数据），自建部分主要是**统一口径、去重、聚合与面板**。

- 有本地日志的 harness（Claude Code、Codex、DSH…）：几乎 100% 可统计，无需任何密钥，订阅制也能覆盖
- 有管理 API 的渠道（OpenRouter、Anthropic、OpenAI、cc-switch）：可统计，分钟级延迟
- 两者都没有的（Trae、部分闭源 IDE）：降级为手动导入，或引导流量走本地代理

真正的难点不是「能不能采到」，而是「采到之后如何不重复、口径一致」。

## 2. 需求拆解

| 需求 | 对应的数据 | 主要来源 |
|------|-----------|---------|
| 总使用量（全渠道全 harness） | 每次请求的 token 数 | 本地日志 ∪ 渠道 API |
| 分渠道看 | 请求发生在哪个渠道 | cc-switch（代理日志/汇总）、渠道 API |
| 分 harness 看 | 请求由哪个工具发出 | 本地日志（各 harness 目录） |
| 分模型看 | 模型名 | 各来源均含 |
| 成本 | 价格 × 用量 × 渠道倍率 | 本地价格表 + 覆盖规则 |
| 趋势 / 预算 | 时间序列 | 聚合层 |

## 3. 数据源分层（核心判断）

### L1 本地日志（首选：零密钥、粒度细、含订阅用量）

- **Claude Code**：`~/.claude/projects/**/*.jsonl`。每条 assistant 消息的 `message.usage` 含 input / output / cache_read / cache_creation 四个口径；条目还带 `sessionId`、`requestId`、`timestamp`、`cwd`、`gitBranch`，可做会话/项目维度。
- **Codex**：`~/.codex/sessions/**/rollout-*.jsonl`（+ `archived_sessions`）。`token_usage_record` 事件含单次（`usage`）、单回合（`turn_token_usage`）、累计（`thread_token_usage`）三档；字段含 `cached_input_tokens`、`cache_write_input_tokens`、`reasoning_output_tokens`。注意：格式随版本变化（旧版为 `token_count` 事件；部分会话无 token 事件），解析需容错。
- **DSH**：`~/.dsh/sessions/**/session.v4.jsonl.zstd`（zstd 压缩的 JSONL）。`assistant/message` 事件的 `data.usage` 含 `inputTokens / outputTokens / totalTokens / cacheReadTokens`。需 zstd 解压；旧会话可能存在字段差异。
- **Antigravity**：`~/.gemini/antigravity/**`（conversations、conversation_summaries.db）。ccusage 已支持读取，可直接复用。
- 其他 CLI（Kimi、Qwen、OpenCode、Goose…）：走 ccusage 桥接（见 §5）。

### L2 渠道 API（补充：覆盖非本机流量；需管理密钥）

- **cc-switch 本地库**：`~/.cc-switch/cc-switch.db`。含渠道注册表（名称、倍率、限额）、逐请求日志（token + 成本 + 渠道）、按天汇总、模型价格表。**本机实测存在且数据丰富**（15 个渠道、4,727 条请求日志）。注意：当前代理模式为关闭状态，汇总数据截至 2026-06-27。
- **OpenRouter**：`GET /api/v1/activity`（按天/按模型）、`POST /api/v1/analytics/query`（自定义维度）。需 Management API key。
- **Anthropic 官方**：Usage & Cost API（Admin key）。文档：`platform.claude.com/docs/en/manage-claude/usage-cost-api`。
- **OpenAI 官方**：Organization Usage / Costs API（Admin key）。
- **中转站（one-api / new-api 系）**：管理日志接口（如 `/api/log/self`），视站点版本与权限而定，逐个实测。

### L3 兜底（覆盖缺口）

- **本地代理模式**：把 harness 的 base_url 指到本工具（或 cc-switch / LiteLLM 代理），全量精确、可带渠道归属；代价是改配置。
- **手动导入**：CSV / JSON（Trae、Copilot 等无接口来源）。
- **不做**：模拟登录抓网页后台（脆弱、有风控/账号风险）。

## 4. 关键难点与对策

1. **口径不统一**（input 是否含缓存？reasoning 是否单列？）
   → 统一为五元组：`input(非缓存) / cache_read / cache_write / output / reasoning`，同时保留原始字段；来源自带语义时记录标记（参考 cc-switch 的 `input_token_semantics`）。
2. **重复计数**（同一请求既在本地日志、又在代理日志/渠道 API）
   → 「每条流一个权威源」策略 + 幂等键 + 多源对账报告（只提示不合并）。详见 `architecture.md` §5。
3. **渠道归因**（本地日志不含 base_url，无法直接知道走了哪个渠道）
   → 三选一或组合：① cc-switch 代理开启时逐请求归属（最准）；② cc-switch 切换时间线近似归属（零侵入）；③ 渠道 API 总量对账（不依赖代理）。
4. **格式漂移**（harness 升级改日志结构）
   → 解析器容错 + 原始行留存（raw）+ 格式探测脚本 + 解析测试。
5. **成本口径**（订阅制无法直接换算、别名模型无价）
   → 本地价表（LiteLLM 快照）+ 渠道倍率 + 别名覆盖；无法定价的显式标记 `missingPricing`（ccusage 同款做法）。
6. **隐私与安全**
   → 全本地存储；面板只绑定 `127.0.0.1`；cc-switch 只读且不读密钥字段；采集器只读文件。

## 5. 风险清单

| 风险 | 影响 | 缓解 |
|------|------|------|
| harness 升级导致解析失败 | 该源数据中断 | 容错解析 + 原始留存 + 快速修复通道；ccusage 兜底 |
| cc-switch 数据库结构变化 | 渠道数据中断 | 只依赖白名单表/列 + 版本探测 + 失败告警 |
| 中转站无 API | 该渠道无法自动统计 | 代理模式 / 手动导入 |
| 多源口径差异大 | 对账噪音 | 明确权威源优先级 + 分视图呈现 |
| 数据量大（数百万 token/天） | 查询变慢 | SQLite 索引 + 按天预聚合表 |
| 单源失败拖垮整体（实测教训：ccusage 统一模式曾因 Antigravity 库打不开而整体报错） | 报表不可用 | 每个采集器独立容错，失败仅影响该源 |

## 6. 判定

| 场景 | 可统计性 |
|------|---------|
| Claude Code / Codex / DSH / Antigravity 用量 | ✅ 高（本地日志） |
| 渠道维度（经 cc-switch 或代理的流量） | ✅ 中-高（取决于代理是否开启） |
| 官方 API 渠道总量 | ✅（管理密钥） |
| 闭源 IDE（Trae 等） | ⚠️ 仅手动导入 |

## 附录 A：本机实测记录（2026-10-15）

- **侦察**：`~/.claude/projects` 150 个 jsonl；`~/.codex/sessions` 112 个 + archived 2 个；`~/.dsh/sessions` 28 个（zstd）；cc-switch 库存在；另有 Antigravity（`~/.gemini/antigravity`）与 Trae。
- **字段核对**：
  - Claude usage keys = `input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens`（另有 server_tool_use、service_tier、cache_creation、inference_geo、iterations、speed 等新字段）
  - Codex usage keys = `input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens, total_tokens`；事件类型 `token_usage_record`
  - DSH usage keys = `inputTokens, outputTokens, totalTokens, cacheReadTokens`（camelCase，位于 `data.usage`）
- **工具验证**：`npx ccusage@latest claude daily` 与 `codex daily` 均正常出报表（含成本；未定价模型有 `missingPricing` 标记；JSON 输出结构已确认）。
- **渠道数据**：cc-switch 15 个渠道配置（claude 2 / claude-desktop 4 / codex 8 / gemini 1）、`proxy_request_logs` 4,727 条、`usage_daily_rollups` 36 天（2026-05-23 ～ 2026-06-27）、`model_pricing` 177 条。
- **API 核对**：OpenRouter Activity / Analytics API（需管理密钥）；Anthropic Usage & Cost API（需 Admin 密钥）。
- **待办验证**：Antigravity 本地库读取（曾遇打开失败，疑似占用/权限）、DSH 旧会话字段差异、中转站日志接口（逐站点）。

## 附录 B：与「直接用现成工具」的关系

- **ccusage**：已覆盖 18 种 harness 的本地日志统计（含 Claude Code、Codex、Antigravity、Kimi、Qwen 等），输出日/周/月/会话报表与 JSON。→ 本项目**复用**它作为长尾 harness 引擎，不重复造轮子；差异点在于：跨渠道归属、持久化、对账、自建面板。
- **cc-switch**：已具备渠道管理 + 代理日志 + 汇总，但只覆盖它自己管理的应用，且视图局限于「渠道」，没有跨 harness 全局视图。→ 本项目**读取**它补齐渠道维度。
- 两者组合后仍缺的：统一口径的单一数据仓库、跨源去重、统一面板、预算提醒 —— 这正是本项目要做的。
