const { chromium } = require("playwright");
const BASE = process.env.TD_BASE || "http://127.0.0.1:8799";
const fails = [];

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => fails.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") fails.push("console.error: " + m.text()); });

  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 1) 未筛选时：5 张卡、无 range 卡
  console.log("unfiltered cards:", await page.locator("#hero .card").count());
  console.log("range card:", await page.locator("#hero .card.range").count());

  // 2) 今天预设：一轮筛选后 range 卡出现且环比有数字
  await page.click('#presets button[data-preset="today"]');
  await page.waitForTimeout(1600);
  const rangeCard = page.locator("#hero .card.range");
  if (!(await rangeCard.count())) fails.push("今天预设：所选范围卡没出现");
  const txt = (await rangeCard.innerText()).replace(/\s+/g, " ");
  console.log("today range card:", txt);
  console.log("preset today active:", await page.locator('#presets button[data-preset="today"]').evaluate(e => e.classList.contains("on")));

  // 3) 自定义范围：环比窗口与手算一致（10-01~10-07 → 09-24~09-30，放在卡片 tooltip 里）
  await page.fill("#fSince", "2026-10-01");
  await page.dispatchEvent("#fSince", "change");
  await page.waitForTimeout(400);
  await page.fill("#fUntil", "2026-10-07");
  await page.dispatchEvent("#fUntil", "change");
  await page.waitForTimeout(1600);
  const rangeCardSel = page.locator("#hero .card.range");
  const custom = (await rangeCardSel.innerText()).replace(/\s+/g, " ");
  const rangeTitle = await rangeCardSel.getAttribute("title");
  console.log("custom range card:", custom);
  console.log("custom range tooltip:", rangeTitle);
  if (!(rangeTitle || "").includes("2026-09-24 ~ 2026-09-30")) {
    fails.push("A1: 自定义 10-01~10-07 的对比窗口不是 09-24~09-30: " + rangeTitle);
  }
  // 副标题不能被截断（单行内放得下「条数 · 成本」）
  const noteFits = await rangeCardSel.locator(".n").evaluate(
    (n) => n.scrollHeight <= n.clientHeight + 1);
  console.log("range note fits:", noteFits, "| text:", await rangeCardSel.locator(".n").innerText());
  if (!noteFits) fails.push("所选范围卡副标题被截断");
  // 4) 窄屏 375px 检查无横向滚动
  await page.setViewportSize({ width: 375, height: 800 });
  await page.waitForTimeout(1200);
  const overflow = await page.evaluate(() => ({
    doc: document.documentElement.scrollWidth,
    win: window.innerWidth,
  }));
  console.log("375px scrollWidth/innerWidth:", JSON.stringify(overflow));
  if (overflow.doc > overflow.win + 2) fails.push(`窄屏 375px 出现横向滚动：${overflow.doc} > ${overflow.win}`);
  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-narrow.png", fullPage: false });

  // 5) 浅色主题下的截图
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.waitForTimeout(800);
  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  if (theme !== "light") await page.click("#btnTheme");
  await page.waitForTimeout(800);
  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-light.png", fullPage: false });

  await browser.close();
  console.log("\nFAILURES(" + fails.length + ")");
  fails.forEach((f) => console.log("✗ " + f));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR:", e); process.exit(2); });
