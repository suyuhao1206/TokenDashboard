# 前端冒烟脚本（可选，需 Node + Playwright）

这些脚本**不是 pytest 的一部分**（CI 不装浏览器），是给改动 UI 后本地人工核对用的。
后端逻辑的回归由 `tests/test_*.py`（49 个用例）覆盖，这里只验证「真实浏览器里点得动、
控制台无报错、关键交互符合预期」。

## 准备

```bash
# 任意目录，只需一次
npm install playwright
npx playwright install chromium
```

## 跑

先起一个**用临时配置**的服务（不要用真实 config，`ui_budget.js` 会写预算）：

```bash
uv run uvicorn --factory ...   # 或按需用 TD_CFG 起一个只读服务
```

然后：

```bash
# 面板全量交互：A1/A2/B1/B2/B4/B5/B7/B8/B9/B10/B11
node tests/ui_smoke.js

# 布局 / 响应式：所选范围卡不截断、375px 无横向滚动
node tests/ui_layout.js

# 空库引导卡（B6）：拦截 API 造空数据
node tests/ui_empty_state.js

# 未定价清单 + 预算写回（B2/B3）：需要一个可写的 config.yaml
TD_CONFIG=path/to/config.yaml node tests/ui_budget.js

# 浮窗（A5/A9/拖动）：用 Proxy 模拟 pywebview 桥
node tests/ui_widget.js
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `TD_BASE` | `http://127.0.0.1:8799` | 被测服务地址 |
| `TD_CONFIG` | `../config/config.yaml` | `ui_budget.js` 校验写回的目标文件 |
| `TD_SHOTS` | 系统临时目录 | 截图输出目录 |
