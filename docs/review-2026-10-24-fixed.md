# token-dashboard 第二轮审查：修复完成报告（2026-10-24）

> 对应文档：[review-2026-10-24.md](./review-2026-10-24.md)
> 基线：`b390e65 操作系统版本适配 v0.1.1`
> 结论：**A0-A9 / B1-B11 / C1-C3 全部完成**（A7 按文档建议只做口径说明，A8 用模拟窗口
> 覆盖四种缩放 + 实机待复核，其余均为代码修复）。
> 验证：`uv run pytest -q` → **62 passed**；`uv run ruff check src packaging tests` → 全绿；
> 面板 / 浮窗 / 空库 / 窄屏 / 浅色主题全部用真实浏览器（Playwright + Chromium）跑过，
> 控制台零 error。

---

## A. Bug 与待验证项

| # | 条目 | 状态 | 落点 |
|---|---|---|---|
| A0 | 浮窗标题栏拖动 | ✅ 已在本轮之前修复 | `widget_web.Api.move_to` + `widget.html` 拖动算法（本轮做回归验证：拖动触发 10+ 次 `move_to`、松手 `save_position`） |
| A1 | 「较前一周期」恒为「-」/ 自定义窗口错位 | ✅ 修复 | 新增 `periods.previous_period()`（纯函数）+ `server.py` 返回 `prev_range` / `prev_window`；前端只显示不再推算 |
| A2 | 模型筛选不作用于 KPI / 所选范围 / 预算 chip | ✅ 修复 | `server.summary()` 加 `model` 参数并透传给全部 `totals()`；前端 `jget("/api/summary?" + qs())` |
| A3 | 部分查询没排除 `local_date IS NULL` | ✅ 修复 | `Store._filter()` 的 `frag` 初值改为 `" AND local_date IS NOT NULL"`；`summary()` / `by_harness()` 手写 SQL 补齐 |
| A4 | CLI 表头与数据列宽不齐 | ✅ 修复 | `cli.REPORT_WIDTHS/REPORT_HEADERS` 共用常量 + `_disp_width()/_pad()` 按**终端显示宽度**（中文占 2 列）对齐 |
| A5 | widget 空柱提示与行为矛盾 | ✅ 修复（方案 a） | 空柱 `cursor: pointer`、底层改为可见的淡色块，title「当天无用量（点击查看）」，点击照常深链 |
| A6 | CSV 公式注入 + 全量导出无上限 | ✅ 修复 | `server._csv_text()` 前置 `'`（`=` `+` `-` `@` `\t` `\r`）；`db.EXPORT_LIMIT = 200_000` + `LIMIT n+1` 探测，响应头 `X-Export-Truncated` / `X-Export-Limit` |
| A7 | DST 时区活跃时段偏 1 小时 | ✅ 按建议只做说明 | `server.hourly()` 注释 + README「活跃时段口径」一节写明「固定偏移时区精确，DST 时区历史小时偏 1 小时」 |
| A8 | PerMonitorV2 后 `fit_viewport` 单位假设 | ✅ 代码修复 + 模拟验证 | 改用 `window.width/height`（逻辑像素）而非 `native.Width/Height`（物理像素）；抽出 `fit_viewport_steps()`，`tests/test_viewport.py` 覆盖 100/125/150/200% 四档收敛 + 「用物理像素必不收敛」的回归守卫 |
| A9 | widget 右键菜单勾选状态过期 | ✅ 修复 | `contextmenu` 里 `applyMenuState()` → `syncState()`（重新拉 `get_state`）；notice 只在启动时提示，不再每次右键重复弹 |

### A1 的实现细节（本轮最重要）

后端算窗口，前端只显示。理由：边界（月末/跨年/空 until）用 Python `date` 算一次比在 JS 里
和 Invalid Date 搏斗可靠，而且能写测试。

```python
# periods.py
def previous_period(since, until, today) -> tuple[str, str] | None:
    start = date.fromisoformat(since)
    end = date.fromisoformat(until) if until else today   # 空 until = 今天，不用 max_date
    span = (end - start).days + 1                          # 含首尾
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)       # 等长、不重叠
    return prev_start.isoformat(), prev_end.isoformat()
```

验证（真实库）：选 2026-10-01 ~ 2026-10-07 → `prev_window = 2026-09-24 ~ 2026-09-30`，
卡片显示 `▲ 1257.9% 较前一周期`；选「近 7 天」（until 为空）→ `prev_window = 2026-10-11 ~ 2026-10-17`，
环比不再恒为「—」。对比窗口放在卡片 tooltip 里（6 列布局下副标题放不下四段信息）。

## B. UI / UX

| # | 条目 | 状态 | 实现 |
|---|---|---|---|
| B1 | 模型分布图/表在模型筛选下退化 | ✅ | 分布请求改为 `qs({dim:"model", limit:10, model:""})`——忽略自身维度筛选，保留日期 + harness |
| B2 | 未定价引导：一键复制价格模板 ⭐ | ✅ | 后端 `/api/summary` 新增 `unpriced_models`；前端 chip 可展开（多列网格 + 内部滚动，上限 320px），每行「复制价格模板」输出 YAML 并 toast 指引 |
| B3 | 预算未配置时给可操作入口 | ✅ 进阶版 | 未设预算 chip 可点 → `prompt` 输入 → `POST /api/config/budget` **行级编辑**写回 `config.yaml`（保留注释/顺序，`newline="\n"`）并更新内存，立即生效 |
| B4 | 热力图日期格点击 = 筛选到当天 | ✅ | `.hm-cell[data-d]` 加 `cursor:pointer` + click 委托，设 `since=until=当天`、`syncPresets()`、toast |
| B5 | 会话明细可展开（按天构成） | ✅ | `_filter()` 加 `session_id`，`/api/requests` 与 `/api/daily` 透传；会话行点击展开按天小表 + 合计 + 「查看全部请求 →」；请求表标题旁出现会话筛选 tag，可 ✕ 清除 |
| B6 | 首次使用引导从 toast 升级为引导卡 | ✅ | `meta.min_date == null` 时 hero 区显示三步引导卡 + 「立即扫描」（复用 `refresh("scan")`），有数据自动隐藏 |
| B7 | 请求表「合计」hover 显示五项构成 | ✅ | `title="输入 X · 缓存读 X · 缓存写 X · 输出(含推理) X · 其中推理 X"`，与输出列风格一致 |
| B8 | 键盘快捷键 | ✅ | `R` 刷新、`1/2/3/4` 切 今天/近7天/近30天/全部、`Esc` 清筛选；`input/select/textarea/contenteditable` 内直接返回 |
| B9 | 窄屏下渠道趋势图需提示 | ✅ | `blank(chart, boxId, text)` 支持自定义占位文案；≤680px 显示「宽屏查看渠道趋势」并把 `#chTrendNote` 置为「窄屏已隐藏」；跨断点 resize 自动重画 |
| B10 | 状态条「更新于 …」可点击刷新 | ✅ | `#lastUpd` 加 `cursor:pointer` + `refresh("manual")`（不触发 `/api/scan`） |
| B11 | CSV 加「本地时间」列 | ✅ | 导出多写一列「本地时间」（`parse_iso(...).astimezone(tz)`），保留 UTC 列；文件名带筛选范围（`...-20261001-20261007-...csv`，单边为 `-from...` / `-to...`） |

**顺带修的体验问题**（评审未列，实测发现）：

- `copyText()` 在非安全上下文/无剪贴板权限时直接报「复制失败」——现在回退到 `execCommand`，
  并在长文本（YAML 模板）场景用自定义成功文案，不再把整段模板塞进 toast。
- 空区间（`since/until` 无数据）的 `totals()` 原先返回一堆 `null`，现在统一归零，
  前端不再出现「精确 null」。
- 「所选范围」卡在 6 列布局下变窄，副标题改成「条数 · 成本」，精确 tokens / 成本环比 /
  对比窗口移入 tooltip。

## C. 工程与质量

| # | 条目 | 状态 | 实现 |
|---|---|---|---|
| C1 | 补自动化测试 | ✅ | 新增 `tests/`：`test_normalize.py`（7）`test_db.py`（4）`test_requests.py`（10）`test_periods.py`（9）`test_server.py`（10）`test_viewport.py`（7）`test_misc.py`（15）= **62 passed** |
| C2 | CI 加静态检查 | ✅ | `uv add --dev ruff pytest httpx`；`release.yml` 在构建 exe 前加 `ruff check` + `pytest -q`；`pyproject.toml` 配 ruff + pytest |
| C3 | 发布 checklist 补两条 | ✅ | `RELEASE.md`：四档缩放启动 widget 验证视口、发版前 pytest 全绿（另加 ruff 一条） |

### 测试覆盖（对应评审 C1 的六条建议）

1. **`normalize_output()`**：OpenAI 含推理 / gemini 另加 / total 缺失走启发式 / reasoning=0，
   外加 total 差 1 的容错；
2. **`Store._order_by()`**：白名单外回退 ts、数值列 NULL 恒最后、注入串不生效
   （`test_sort_param_is_not_injectable`）；
3. **`upsert_events()` 幂等 + `ensure_dedup_version()` 清空重采**；
4. **请求表排序/筛选/分页 + CSV 合计不含推理双算**（output=100 / reasoning=40 → 合计 1100 不是 1140）；
5. **A1 环比窗口**：预设 until 为空、自定义跨月、跨年、闰年 2 月、单日、起止反转；
6. **`_csv_text()` 公式注入防护** + 整条 CSV 导出链路（列数、本地时间、文件名）。

另加：`A3` 的 `local_date IS NULL` 全链路排除、`A8` 四档 DPI 收敛、预算写回（含 `1000.0` 不能变 `"1"`
的格式化坑）、前端静态资源 `getElementById` 引用完整性。

## 前端冒烟脚本（可选，不在 CI）

`tests/ui_*.js` + `tests/UI-SMOKE.md`：面板全量交互、布局/响应式、空库引导、预算写回、
浮窗（用 Proxy 模拟 pywebview 桥）。需要 Node + Playwright，本地人工核对用。

## 刻意没做的

- **A7 逐条按事件换算 DST**：文档已判定「工作量与收益不匹配」，只做口径说明。
- **B5 会话筛选做全局筛选**：评审原文是「`/api/requests` 与 `/api/daily` 透传 `session_id`」，
  所以会话下钻只作用于请求表 + 展开明细，不进 KPI/图表（否则同屏数字会自相矛盾）。
- **A8 实机四档缩放**：自动化只能验证算法收敛；真实 WebView2 的非客户区偏移仍需按
  `RELEASE.md` 的 checklist 在 100/125/150/200% 下各启动一次确认（`data/widget.log` 里看「视口 #i」）。
