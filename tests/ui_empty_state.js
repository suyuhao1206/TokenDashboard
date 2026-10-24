const { chromium } = require("playwright");
const BASE = process.env.TD_BASE || "http://127.0.0.1:8799";
const fails = [];

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
  page.on("pageerror", (e) => fails.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") fails.push("console.error: " + m.text()); });

  // 空库：拦截 API 造一个「没有任何数据」的响应，验证引导卡出现
  await page.route("**/api/meta", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ harnesses: [], models: [], channels: [], min_date: null,
                           max_date: null, channel_rows: 0, channel_cost: null, version: "0.1.1" }),
  }));
  await page.route("**/api/daily*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/api/breakdown*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/api/sessions*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/api/summary*", (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      all: { events: 0, tokens: 0, cost: 0, unpriced: 0, input: 0, cache_read: 0 },
      range: {}, prev_range: null, prev_window: null,
      today: {}, last7d: {}, last30d: {}, month: {},
      by_harness: [], timezone: "Asia/Shanghai",
      budget: { monthly_usd: null }, unpriced_models: [],
    }),
  }));
  await page.route("**/api/requests*", (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ total: 0, page: 1, size: 20, rows: [], sort: "ts", order: "desc" }),
  }));
  await page.route("**/api/hourly*", (r) => r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify(Array.from({ length: 24 }, (_, h) => ({ hour: h, events: 0, tokens: 0 }))),
  }));

  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);

  const onboardVisible = await page.locator("#onboard").isVisible();
  const onboardText = (await page.locator("#onboard").innerText()).replace(/\s+/g, " ");
  console.log("B6 onboard visible:", onboardVisible);
  console.log("B6 text:", onboardText);
  if (!onboardVisible) fails.push("B6: 空库时引导卡没有出现");
  for (const kw of ["scan", "扫描", "预算"]) {
    if (!onboardText.toLowerCase().includes(kw.toLowerCase())) fails.push("B6: 引导卡缺步骤 " + kw);
  }
  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-onboard.png" });
  console.log("B6 screenshot saved");

  await browser.close();
  console.log("\nFAILURES(" + fails.length + ")");
  fails.forEach((f) => console.log("✗ " + f));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR:", e); process.exit(2); });
