# 数据源矩阵与字段细节

> 基于本机实测（2026-10-15）与官方文档核对。字段名均为实测/文档确认；标 ⚠️ 的需在实现时再验证。

## A. Harness 本地数据源

### A1. Claude Code（✅ 已实测）

- 路径：`~/.claude/projects/<编码路径>/<session-uuid>.jsonl`
- 每行一条事件；assistant 消息条目关键字段：
  - 顶层：`type, timestamp, sessionId, requestId, uuid, parentUuid, isSidechain, cwd, version, gitBranch, entrypoint`
  - `message.model`：模型名
  - `message.usage`：`input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens`
    （新版本扩展字段：`server_tool_use, service_tier, cache_creation, inference_geo, iterations, speed`）
- **去重规则（已实现并与 ccusage 逐字段对齐）**：
  - 有 `requestId`：按 `(message.id, requestId)` 去重，保留 `timestamp` 最新的一条；
  - 无 `requestId`：逐条计数（按 `uuid` 保证唯一）；
  - subagent（`isSidechain`）也是真实调用，计入。
- **cache 写入口径**：优先用 `usage.cache_creation` 明细（`ephemeral_5m_input_tokens + ephemeral_1h_input_tokens`），
  缺失时回退到标量 `cache_creation_input_tokens`（此点直接影响与 ccusage 的对齐，实测 2026-07-26 差 9,341 tokens）。
- 验证：2026-07-04 / 07-26 / 07-27 三天与 ccusage 日报**逐字段完全一致**。
- 可选实时源：OpenTelemetry（`CLAUDE_CODE_ENABLE_TELEMETRY=1`；`claude_code.api_request` 事件含 token、cost_usd、request_id、model 等）。文档：`code.claude.com/docs/en/monitoring-usage`

### A2. Codex（✅ 已实测）

- 路径：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`；归档在 `~/.codex/archived_sessions/`（**ccusage 也读归档目录，本项目同样纳入**）
- **两种事件格式**（同一文件优先新格式，避免双计）：
  1. 旧格式 `type=event_msg` / `payload.type=token_count`：`payload.info.total_token_usage`（会话累计）+ `payload.info.last_token_usage`（本次增量）
  2. 新格式 `type=token_usage_record`：`payload.thread_token_usage`（线程累计）+ `payload.usage`（本次增量）；关联键 `thread_id / turn_id / session_id / response_id`
  字段统一为：`input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens`
- **计数口径（已实现并与 ccusage 全量逐日核对）**：
  - 文件内推进"累计基线"，增量 = 本次累计 − 上次累计（累计回退时按单调化处理）；
  - 文件首条事件无前序基线，用其 `last_token_usage / usage` —— 续写文件（同会话分叉成新文件）的首条累计包含上一段用量，直接用累计会重复计入；
  - 拆分：`非缓存输入 = max(0, Δinput − Δcached)`，`缓存读取 = min(Δinput, Δcached)`；
  - 模型/项目：`turn_context.payload.model / payload.cwd`、`session_meta` 顺序关联。
- 验证：2026-05-01 ~ 2026-09-30 逐日与 ccusage 对齐，**仅 3 天存在差异**（06-04、06-07、07-30，各为单会话内数百~数十万 tokens 的细微差异，占总量的 <0.1%，原因待查）；2026-08-01~08-10、05 月、09 月完全一致。
- 其他：`~/.codex/session_index.jsonl`（`id / thread_name / updated_at`）；`state_5.sqlite` 等内部库不作为统计源。

### A3. DSH / DeepSeek Harness（✅ 已实测）

- 路径：`~/.dsh/sessions/<编码项目>/<session-uuid>/session.v4.jsonl.zstd`（zstd 压缩 JSONL，依赖 `zstandard`）
- 用量事件：`type=assistant/message` → `data.usage = {inputTokens, outputTokens, totalTokens, cacheReadTokens?}`
  - **三个字段互不重叠**（实测 `totalTokens = input + output + cacheRead`），直接映射即可
- 模型/渠道：`type=model/selection` → `data = {provider, model, reasoningEffort}`（本机样例：`provider=my-workbuddy`）
- 时间：顶层 `time` 为**毫秒时间戳**；`seq` 为事件序号
- 去重键：`file + seq`；项目名由目录编码还原（`~XXXX` 为 UTF-16 码元，`-` 表示路径分隔符）
- 实测：29 个会话文件 → 2,401 条事件 / 5.53 亿 tokens（2026-10-03 ~ 10-15）

### A4. WorkBuddy（✅ 已实测）

- 路径：`~/.workbuddy/projects/<编码项目>/<session-uuid>.jsonl`（UTF-8 JSONL，另有 `.file-rollback.ndjson` 忽略）
- 用量字段：`providerData.usage`（应用归一化形式，首选）
  `{requests, inputTokens, outputTokens, totalTokens, inputTokensDetails:[{cached_tokens}], outputTokensDetails:[{reasoning_tokens}]}`
  回退 `providerData.rawUsage`（OpenAI 风格 `prompt_tokens` / `completion_tokens` / `*_details`）
- 口径：`非缓存输入 = inputTokens − cached_tokens`、`缓存读取 = cached_tokens`（OpenAI 语义里 inputTokens 已含缓存，与 Codex 同款处理）
- 模型：`providerData.requestModelName`（回退 `model`）；时间：顶层 `timestamp`（毫秒）；会话：`sessionId`；项目：`cwd`
- 去重键：`file + messageId`（实测带 `rawUsage` 的行 messageId 唯一，不会重复计数）
- 实测：71 个会话文件 → 7,255 条事件 / **13.3 亿 tokens**（2026-07-27 ~ 10-15，是本机当前用量最大的 harness）

### A5. Antigravity（⚠️ 待验证）

- 路径：`~/.gemini/antigravity/`（`conversations/*.db`、`conversation_summaries.db` 等）
- ccusage 已支持 Antigravity 读取（`ccusage antigravity daily`）；建议直接复用
- 实测问题：ccusage 统一模式在本机读取其会话库时报 `unable to open database file`（疑似被占用或权限）；注意错误隔离

### A6. 其他 harness（长尾）

- 可走 ccusage 桥接（`--json` 消费）：OpenCode、Amp、Droid、Codebuff、Hermes Agent、pi-agent、Goose、OpenClaw、Kilo、Kimi、Qwen、GitHub Copilot CLI、Gemini CLI、Grok Build CLI、ZCode
- Trae：无本地明细（仅扩展/工作区数据）→ 手动导入
- VS Code Copilot Chat：本地无用量明细（账单在 GitHub 侧）→ 手动导入

## B. 渠道数据源

### B1. cc-switch 本地库（✅ 已实测）

- 路径：`~/.cc-switch/cc-switch.db`（SQLite，只读访问）
- 关键表：

| 表 | 内容 | 用途 |
|----|------|------|
| `providers` | 渠道配置：`name, app_type, cost_multiplier, limit_daily_usd, limit_monthly_usd, provider_type` | 渠道注册表（**不读 `settings_config` 密钥字段**） |
| `proxy_request_logs` | 逐请求：tokens、各分项成本、model、provider_id、session_id、status_code | 渠道 × 请求级明细 |
| `usage_daily_rollups` | 按天 × 渠道 × 模型汇总 | 渠道日汇总 |
| `model_pricing` | 177 条模型价格 | 价格参考 |
| `session_log_sync` | 已同步的会话文件清单 | 增量线索 |

- 实测状态：15 个渠道配置；4,727 条请求日志；汇总 36 天（截至 2026-06-27）；代理当前关闭
- **归因现状（重要）**：`usage_daily_rollups` / `proxy_request_logs` 里的 `provider_id` 以 `_` 开头
  （`_session`、`_codex_session`）表示"由会话日志推算、无法归属到具体渠道"的用量。
  实测该用户历史数据中约 99% 的用量只能标为 `会话日志·codex/claude`，仅少数能归到
  `公益codex`、`StepFun`、`Agnes` 等具体渠道 → 想要"精确到渠道"，需要重新开启 cc-switch 代理
  （此后每条请求都会带 provider 归属）。
- 注意：其 `input_token_semantics` 列表明已处理口径问题；读取时忽略含密钥的列

### B2. OpenRouter（⚠️ 接入时核对）

- `GET /api/v1/activity`：按天 × 模型端点的用量与花费
- `POST /api/v1/analytics/query` + `GET /api/v1/analytics/meta`：自定义指标 / 维度 / 粒度
- 鉴权：Management API key（实测无鉴权时返回 401）

### B3. Anthropic 官方（⚠️ 接入时核对）

- Usage & Cost API（Admin API key）
- 文档：`platform.claude.com/docs/en/manage-claude/usage-cost-api`
- 另有：Claude Code Analytics API（团队 / 组织视角）

### B4. OpenAI 官方（⚠️ 接入时核对）

- Organization Usage / Costs API（Admin key）

### B5. 中转站（one-api / new-api 系）（⚠️ 逐站点实测）

- 常见：`/api/log/self`（用户令牌）或管理端日志接口；返回 prompt / completion tokens、quota、模型、时间
- 不同站点版本与权限差异大；无接口时走代理或手动导入

## C. 可复用工具

| 工具 | 能力 | 本项目如何用 |
|------|------|-------------|
| ccusage（v20，18 源） | 本地日志 → 日/周/月/会话报表、JSON、成本、时区、离线模式 | 长尾 harness 引擎 + 交叉验证 |
| cc-switch | 渠道管理 + 代理日志 + 汇总 | 渠道注册表 + 渠道历史数据 |
| LiteLLM price map | 模型价格（含 cache 读写） | 内置价格表快照 |
| models.dev | 模型价格 / 规格 | 价格交叉校验 |

## D. 定价与成本

- 主价表：LiteLLM `model_prices_and_context_window.json` 快照（ccusage 同款）
- 覆盖：`pricing.overrides.yaml`（别名模型，如 `gemini-pro-agent`、`fable-5` 等渠道别名）
- 渠道倍率：按渠道乘 `cost_multiplier`（cc-switch 已有此概念）
- 无法定价：标记 `missingPricing`，面板单独提示（不显示为 $0 假象）
- 订阅制 harness：成本为「按价表估算」，面板需标注口径

## E. 覆盖缺口

| 缺口 | 兜底 |
|------|------|
| Trae、Copilot 等无本地明细 | 手动导入（CSV） |
| 未走代理、未开 API 的渠道 | 引导开启 cc-switch 代理或渠道 API |
| 移动端 / 网页端用量 | 渠道 API（若有） |
