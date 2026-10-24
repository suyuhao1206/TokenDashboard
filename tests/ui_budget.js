const { chromium } = require("playwright");
const BASE = process.env.TD_BASE || "http://127.0.0.1:8799";
const fails = [];

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 1100 } })).newPage();
  page.on("pageerror", (e) => fails.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") fails.push("console.error: " + m.text()); });

  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 展开未定价清单
  await page.click("#chipUnpriced");
  await page.waitForTimeout(400);
  await page.locator("#status").scrollIntoViewIfNeeded();
  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-unpriced.png", clip: undefined });
  console.log("unpriced panel rows:", await page.locator("#unpricedPanel .up-row").count());

  // 预算 chip：点开 prompt，输入 100，确认写回 + toast
  let dialogMsg = "";
  page.once("dialog", async (d) => {
    dialogMsg = d.message();
    await d.accept("100");
  });
  await page.click("#chipBudget");
  await page.waitForTimeout(1800);
  const toast = await page.locator("#toast").innerText();
  const budgetText = (await page.locator("#chipBudget").innerText()).replace(/\s+/g, " ");
  console.log("B3 dialog:", dialogMsg.replace(/\n/g, " | ").slice(0, 120));
  console.log("B3 toast:", toast);
  console.log("B3 chip after:", budgetText);
  if (!/本月/.test(budgetText)) fails.push("B3: 设置预算后 chip 没变成百分比: " + budgetText);

  const cfgText = require("fs").readFileSync(process.env.TD_CONFIG || (__dirname + "/../config/config.yaml"), "utf8");
  console.log("B3 config has budget:", /monthly_usd:\s*100/.test(cfgText));
  if (!/monthly_usd:\s*100/.test(cfgText)) fails.push("B3: 预算没有写回 config.yaml");

  // 关闭预算（恢复原状）
  page.once("dialog", async (d) => await d.accept("0"));
  await page.click("#chipBudget");
  await page.waitForTimeout(1500);
  const after = (await page.locator("#chipBudget").innerText()).replace(/\s+/g, " ");
  console.log("B3 chip after clear:", after);

  // 会话展开截图
  await page.locator("details.panel").filter({ hasText: "会话明细" }).locator("summary").click();
  await page.waitForTimeout(300);
  await page.locator("#sessions tbody tr.sess-row").first().click();
  await page.waitForTimeout(1200);
  await page.locator("#sessions").scrollIntoViewIfNeeded();
  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-session.png" });
  console.log("session detail rows:", await page.locator("#sessions tr.sess-detail").count());

  await browser.close();
  console.log("\nFAILURES(" + fails.length + ")");
  fails.forEach((f) => console.log("✗ " + f));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR:", e); process.exit(2); });
