const { chromium } = require("playwright");

const BASE = process.env.TD_BASE || "http://127.0.0.1:8799";
const fails = [];
const warnings = [];

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();

  page.on("console", (m) => {
    if (m.type() === "error") fails.push("console.error: " + m.text());
    if (m.type() === "warning") warnings.push("console.warn: " + m.text());
  });
  page.on("pageerror", (e) => fails.push("pageerror: " + e.message));

  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  const log = (...a) => console.log(...a);

  // ---- 基础渲染 ----
  const cards = await page.locator("#hero .card").count();
  log("hero cards:", cards);
  const hmCells = await page.locator(".hm-cell[data-d]").count();
  log("heatmap cells with data:", hmCells);
  const rowCount = await page.locator("#reqTable tbody tr").count();
  log("request rows:", rowCount);
  const sessRows = await page.locator("#sessions tbody tr.sess-row").count();
  log("session rows:", sessRows);

  // ---- A1: 预设范围应有环比数值，不是「-」 ----
  await page.click('#presets button[data-preset="30"]');
  await page.waitForTimeout(1500);
  const rangeCard = page.locator("#hero .card.range");
  log("range card count:", await rangeCard.count());
  const deltaText = (await rangeCard.locator(".d").innerText()).replace(/\s+/g, " ").trim();
  log("A1 delta text:", deltaText);
  if (deltaText.startsWith("—")) fails.push("A1: 预设范围环比仍为「—」: " + deltaText);
  const heroText = await page.locator("#hero").innerText();
  log("hero snapshot:", heroText.replace(/\s+/g, " ").slice(0, 260));

  // ---- A2: 模型筛选作用到 KPI ----
  const modelOpts = await page.locator("#fModel option").allInnerTexts();
  log("model options:", modelOpts.length, modelOpts.slice(0, 4));
  const beforeAll = (await page.locator("#hero .card").nth(4).innerText()).replace(/\s+/g, " ");
  await page.selectOption("#fModel", { index: 1 });
  await page.waitForTimeout(1500);
  const afterAll = (await page.locator("#hero .card").nth(4).innerText()).replace(/\s+/g, " ");
  log("A2 累计成本 before:", beforeAll, "| after:", afterAll);
  if (beforeAll === afterAll) fails.push("A2: 模型筛选后累计成本卡没变");

  // ---- B1: 模型分布图仍显示多个模型 ----
  const pieSeries = await page.evaluate(() => {
    const inst = echarts.getInstanceByDom(document.getElementById("chartModels"));
    if (!inst) return null;
    const opt = inst.getOption();
    return opt.series && opt.series[0] && opt.series[0].data ? opt.series[0].data.length : 0;
  });
  log("B1 model pie slices:", pieSeries);
  if (!pieSeries || pieSeries < 2) fails.push("B1: 模型筛选下分布图退化到 " + pieSeries + " 个扇区");

  // ---- B2: 未定价面板 ----
  const unpricedChip = await page.locator("#chipUnpriced").count();
  log("B2 unpriced chip:", unpricedChip);
  if (unpricedChip) {
    await page.click("#chipUnpriced");
    await page.waitForTimeout(300);
    const visible = await page.locator("#unpricedPanel").isVisible();
    const rows = await page.locator("#unpricedPanel .up-row").count();
    log("B2 panel visible:", visible, "rows:", rows);
    if (!visible || rows === 0) fails.push("B2: 未定价面板没展开或没有行");
    const btn = page.locator("[data-copy-price]").first();
    const model = await btn.getAttribute("data-copy-price");
    await btn.click();
    await page.waitForTimeout(400);
    const toast = await page.locator("#toast").innerText();
    log("B2 toast:", toast.slice(0, 80));
    log("B2 model:", model);
  }

  // ---- B3: 预算 chip ----
  const budgetChip = await page.locator("#chipBudget").count();
  log("B3 budget chip:", budgetChip);
  if (!budgetChip) fails.push("B3: 未配置预算时没有可点击入口");

  // ---- B4: 热力图点击筛选 ----
  await page.locator('#presets button[data-preset="all"]').click();
  await page.waitForTimeout(1200);
  const targetCell = page.locator(".hm-cell[data-d]").nth(30);
  const targetDate = await targetCell.getAttribute("data-d");
  await targetCell.click();
  await page.waitForTimeout(1500);
  const sinceVal = await page.locator("#fSince").inputValue();
  const untilVal = await page.locator("#fUntil").inputValue();
  log("B4 clicked", targetDate, "-> since/until:", sinceVal, untilVal);
  if (sinceVal !== targetDate || untilVal !== targetDate) fails.push("B4: 点击热力图没筛到当天");

  // ---- B5: 会话展开 ----
  await page.locator('#presets button[data-preset="all"]').click();
  await page.waitForTimeout(1200);
  await page.locator("details.panel").filter({ hasText: "会话明细" }).locator("summary").click();
  await page.waitForTimeout(300);
  const firstSess = page.locator("#sessions tbody tr.sess-row").first();
  const sessId = await firstSess.getAttribute("data-session");
  const sessTokens = (await firstSess.locator("td").nth(6).innerText()).replace(/,/g, "");
  await firstSess.click();
  await page.waitForTimeout(1200);
  const detailVisible = await page.locator("#sessions tr.sess-detail").count();
  const detailText = detailVisible ? (await page.locator("#sessions tr.sess-detail").first().innerText()).replace(/\s+/g, " ") : "";
  log("B5 session", sessId && sessId.slice(0, 8), "row tokens", sessTokens,
      "detail rows:", detailVisible, detailText.slice(0, 140));
  if (!detailVisible) fails.push("B5: 会话行点击没有展开明细");

  // 展开行的「查看全部请求」应把请求表筛到该会话
  const link = page.locator("#sessions tr.sess-detail a[data-req-for]").first();
  await link.click();
  await page.waitForTimeout(1600);
  const sessTagVisible = await page.locator("#sessionFilter").isVisible();
  const reqTotalText = await page.locator("#reqPager .pg-info").innerText();
  log("B5 请求表会话筛选 tag:", sessTagVisible, "pager:", reqTotalText.replace(/\s+/g, " "));
  if (!sessTagVisible) fails.push("B5: 会话筛选 tag 没出现");
  // 清除会话筛选
  await page.click("#sessionFilterClear");
  await page.waitForTimeout(1400);
  const sessTagGone = await page.locator("#sessionFilter").isVisible();
  log("B5 清除后 tag 可见:", sessTagGone);
  if (sessTagGone) fails.push("B5: 会话筛选没被清除");

  // ---- B7: 合计 tooltip ----
  const totalCell = page.locator("#reqTable tbody tr td[title]").filter({ hasText: /^[\d,]+$/ }).first();
  const tip = await page.locator("#reqTable tbody tr").first().locator("td").nth(9).getAttribute("title");
  log("B7 total tooltip:", (tip || "").slice(0, 100));
  if (!tip || !tip.includes("其中推理")) fails.push("B7: 合计列缺五项构成 tooltip");

  // ---- B8: 快捷键 ----
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("2");
  await page.waitForTimeout(1300);
  const preset7on = await page.locator('#presets button[data-preset="7"]').evaluate((el) => el.classList.contains("on"));
  log("B8 key 2 -> 近7天 active:", preset7on);
  if (!preset7on) fails.push("B8: 快捷键 2 没切到近 7 天");
  await page.keyboard.press("4");
  await page.waitForTimeout(1000);
  const allOn = await page.locator('#presets button[data-preset="all"]').evaluate((el) => el.classList.contains("on"));
  log("B8 key 4 -> 全部 active:", allOn);
  if (!allOn) fails.push("B8: 快捷键 4 没切到全部");
  // 日期框内按 1 不应切换范围
  await page.click("#fSince");
  await page.keyboard.press("1");
  await page.waitForTimeout(300);
  const stillAll = await page.locator('#presets button[data-preset="all"]').evaluate((el) => el.classList.contains("on"));
  log("B8 日期框内按 1 后仍为全部:", stillAll);
  if (!stillAll) fails.push("B8: 日期输入框内按 1 被误当成快捷键");

  // ---- B9: 窄屏提示 ----
  await page.setViewportSize({ width: 640, height: 900 });
  await page.waitForTimeout(1200);
  const chVisible = await page.locator("#channelPanel").isVisible();
  const chText = chVisible ? (await page.locator("#boxChannelTrend").innerText()).replace(/\s+/g, " ") : "(渠道面板隐藏)";
  log("B9 narrow channel box:", chText.slice(0, 120));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(800);

  // ---- B10: 点「更新于」刷新 ----
  const updBefore = await page.locator("#lastUpd").innerText();
  await page.locator("#lastUpd").click();
  await page.waitForTimeout(1500);
  const updAfter = await page.locator("#lastUpd").innerText();
  log("B10 更新于:", updBefore, "->", updAfter);

  // ---- B11 / A6: 导出 ----
  await page.locator('#presets button[data-preset="7"]').click();
  await page.waitForTimeout(1200);
  const dl = page.waitForEvent("download", { timeout: 30000 });
  await page.click("#btnExport");
  const download = await dl;
  const fname = download.suggestedFilename();
  log("B11 export filename:", fname);
  const path = await download.path();
  const fs = require("fs");
  const head = fs.readFileSync(path, "utf8").split(/\r?\n/).slice(0, 2).join("\n");
  log("B11 export head:", head.slice(0, 160));
  if (!fname.includes("2026")) fails.push("B11: 导出文件名没有带筛选范围: " + fname);

  // ---- B6: 引导卡在空库时出现（用 DOM 直接验证 renderSub 逻辑） ----
  const onboardHidden = await page.locator("#onboard").evaluate((el) => el.hidden);
  log("B6 onboard hidden with data:", onboardHidden);

  // ---- 主题切换 ----
  await page.click("#btnTheme");
  await page.waitForTimeout(600);
  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  log("theme after toggle:", theme);
  await page.click("#btnTheme");
  await page.waitForTimeout(400);

  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-panel.png", fullPage: false });
  await browser.close();

  console.log("\n=== WARNINGS ===");
  warnings.slice(0, 10).forEach((w) => console.log(w));
  console.log("\n=== FAILURES (" + fails.length + ") ===");
  fails.forEach((f) => console.log("✗ " + f));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error("SCRIPT ERROR:", e);
  process.exit(2);
});
