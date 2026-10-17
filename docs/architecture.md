# 架构设计

> 目标：本地优先、可增量、可扩展、不重复计数。

## 1. 设计原则

1. **本地优先**：默认零网络（价格表更新除外），数据不出机器
2. **单一权威源**：每条「流」（harness × 渠道）只认一个来源，避免重复计数
3. **增量幂等**：重复运行不重复计数（游标 + 幂等键）
4. **适配器隔离**：一个源失败不影响其他源（实测教训：ccusage 统一模式曾因单个 Antigravity 库打不开而整体失败）
5. **可降级**：无 API 的源允许手动导入兜底

## 2. 总览

```
┌────────────────── 采集层（适配器，各自独立容错） ──────────────────┐
│ claude_code   codex   dsh   antigravity   cc_switch   channel_api │
│  本地日志     本地日志  zstd   本地库        本地库        HTTP      │
│                      ccusage（子进程，长尾） / manual              │
└──────┬──────────┬───────┬──────┬─────────────┬────────────┬───────┘
       └──────────┴───────┴──────┴─────────────┴────────────┘
                                 ▼
                  规范化（统一口径 + 模型归一 + 成本计算）
                                 ▼
                       SQLite（usage_events + 预聚合）
                        ├──────────────┐
                        ▼              ▼
                Web 面板（FastAPI）   CLI 报表
```

## 3. 模块

| 模块 | 职责 |
|------|------|
| `collectors/` | 每源一个适配器：扫描 → 解析 → 输出标准事件 + 新游标 |
| `normalize/` | 字段归一、模型名归一（别名→规范名）、时间归一（UTC）、成本计算 |
| `store/` | SQLite 读写、幂等入库、聚合视图 / 预聚合 |
| `pricing/` | 价表加载、覆盖规则、渠道倍率 |
| `server/` | FastAPI：REST + 静态页（`/api/summary`、`/api/daily`、`/api/sessions`…） |
| `ui/` | 单页前端（ECharts），本地资源不依赖 CDN |
| `cli/` | `scan / report / serve / doctor` 等命令 |

## 4. 数据模型（草稿）

```sql
CREATE TABLE usage_events (
  id            INTEGER PRIMARY KEY,
  source        TEXT NOT NULL,          -- 适配器 id，如 'claude_code'
  harness       TEXT,                   -- claude-code / codex / dsh / ...
  channel       TEXT,                   -- 渠道 id（能确定时）
  model         TEXT,                   -- 归一后模型名
  raw_model     TEXT,                   -- 原始模型名
  ts            TEXT NOT NULL,          -- ISO8601 UTC
  input_tokens        INTEGER DEFAULT 0,   -- 非缓存输入
  cache_read_tokens   INTEGER DEFAULT 0,
  cache_write_tokens  INTEGER DEFAULT 0,
  output_tokens       INTEGER DEFAULT 0,
  reasoning_tokens    INTEGER DEFAULT 0,
  cost_usd      REAL,                   -- 估算成本（NULL = 未定价）
  session_id    TEXT,
  request_id    TEXT,
  project       TEXT,                   -- cwd / 项目
  dedup_key     TEXT UNIQUE,            -- sha1(source + session + msg_id)
  raw_ref       TEXT                    -- 原始文件 + 偏移 / API 游标
);

CREATE TABLE ingest_state (
  source      TEXT PRIMARY KEY,
  cursor      TEXT,                     -- 文件偏移 / 行号 / 时间窗 / mtime
  updated_at  TEXT
);

CREATE TABLE channels (                 -- 渠道注册表（可从 cc-switch 同步）
  id TEXT PRIMARY KEY,
  name TEXT,
  type TEXT,
  cost_multiplier REAL DEFAULT 1.0,
  daily_limit_usd REAL,
  monthly_limit_usd REAL
);
```

预聚合：`daily_rollup(date, harness, channel, model, ...)`，面板查询走聚合表。

## 5. 去重与口径（关键）

**权威源优先级**（可配置）：`local_log > proxy_log > channel_api > manual`

- 同一「流」（同一 harness 的同一批请求）出现在多源时，**只取优先级最高的源计入总量**；其他源仅用于对账
- 幂等键：本地日志用 `source + session_id + message_id/response_id`；API 用 `source + 日期 + 维度键`
- **对账视图**：当多源同时覆盖同一时间段时，输出差异报告（如「7 月 Claude Code 本地日志 $X vs cc-switch 汇总 $Y，偏差 Z%」），**不自动合并**
- 口径统一为五元组 `input / cache_read / cache_write / output / reasoning`；来源无法区分时记录其语义标记

### 5.1 实际实现的去重键（必须与文件物理位置解耦）

幂等键一旦掺进**绝对路径**，日志被搬移（Codex 的 `sessions/` → `archived_sessions/`、
项目目录改名）就会让同一份用量按新路径再入一次，**数据翻倍**。因此实现里一律用
「文件内稳定标识」而不是路径：

| 采集器 | 去重键 | 说明 |
|--------|--------|------|
| `claude_code` | `message.id + requestId`（回退 `uuid`，再回退 `文件名 + 行号`） | 键本来就与目录无关 |
| `codex` | `文件名(不含目录) + 行号` | rollout 文件名自带时间戳 + uuid，全局唯一 |
| `dsh` | `会话 id（= 会话目录名）+ seq`（缺 seq 用 `L行号`） | 与项目目录名无关 |
| `workbuddy` | `会话 id + messageId`（缺 messageId 用 `L行号`） | 与项目目录名无关 |

**方案升级的迁移**：`collector_meta(source, dedup_version, updated_at)` 记录每个来源当前
使用的去重键版本；采集器启动时调用 `Store.ensure_dedup_version()`，版本不一致就删除该来源
的旧行 + 清空 `scanned_files` 强制重扫（数据都能从本地日志重建）。版本一致时只是一次
SELECT。改动去重键**必须**同时递增该采集器的 `DEDUP_VERSION`，否则新旧键无法匹配，
会重复入库。

## 6. 渠道归因（难点）

本地日志不含 base_url，渠道归属靠三条路径（按优先级）：

1. **代理模式**（最准）：流量经 cc-switch 代理（或本工具内置代理），逐请求带渠道
2. **时间线近似**（零侵入）：读 cc-switch 切换记录，把「某时间段内的用量」归属到当时激活的渠道
3. **渠道 API 对账**（不依赖代理）：渠道侧总量 vs 本地总量，做差集提示

MVP 先做 2 + 3，代理模式作为可选增强。

## 7. 调度与运行

- 单进程：`token-dashboard serve`（内置调度器，默认 10 分钟扫一次）
- 也可仅用 CLI：`token-dashboard scan` + `token-dashboard report daily`
- Windows：`start.ps1` / 计划任务；后续可 Docker
- 环境：Python 3.12 + uv（本机已就绪）；Node 仅在调用 ccusage 时需要

## 8. 技术栈建议

| 层 | 选择 | 理由 |
|----|------|------|
| 语言 | Python 3.12 | 数据处理/解析生态好；本机已装 uv |
| Web | FastAPI + Uvicorn | 轻量、单进程、本地面板足够 |
| DB | SQLite（stdlib sqlite3） | 单文件、零运维、够用 |
| 调度 | APScheduler（或简单循环） | 内置定时扫描 |
| HTTP | httpx | 渠道 API |
| CLI | Typer | 报告命令 |
| 前端 | 原生 HTML + ECharts（本地打包） | 免构建、离线可用 |
| 可选 | `zstandard`（DSH）、`watchdog`（文件监听） | 按需 |

备选：TypeScript 全家桶（ccusage 是 TS，生态可借鉴）；如更顺手可切换，接口设计不变。

## 9. 安全与隐私

- 面板仅绑定 `127.0.0.1`；无遥测、无云同步
- 密钥只存 `.env`（gitignore）；cc-switch 只读白名单列，**绝不读 `settings_config`**
- 采集器只读；不解析、不存储会话正文（仅统计字段）
- 数据库文件本地加密可后续增强（SQLCipher）—— 非 MVP

## 10. 新增一个适配器（步骤）

1. 在 `collectors/` 新建模块，实现 `discover() -> sources` 与 `pull(source, cursor) -> (events, cursor)`
2. 用真实样本写解析测试（含异常 / 旧格式样本）
3. 定义幂等键与游标策略
4. 在 `config.yaml` 注册并设默认禁用
5. 跑 `token-dashboard doctor` 验证该源
