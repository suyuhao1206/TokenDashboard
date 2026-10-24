const { chromium } = require("playwright");
const BASE = process.env.TD_BASE || "http://127.0.0.1:8799";
const fails = [];

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 420, height: 420 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => fails.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") fails.push("console.error: " + m.text()); });

  // 用桥替身模拟 pywebview：初始化前注入，widget.html 只在 hasBridge() 时才调。
  // 注意 addInitScript 的函数体是在**浏览器**里执行的，不能引用 Node 的 process/BASE，
  // 需要的值要通过参数传进去。
  await page.addInitScript((baseUrl) => {
    const calls = [];
    window.__calls = calls;
    window.pywebview = {
      api: new Proxy({}, {
        get: (_t, name) => (...args) => {
          calls.push([String(name), ...args]);
          if (name === "get_state") {
            // 第一次返回 autostart=true，之后模拟用户手动删了快捷方式 → false
            window.__stateCalls = (window.__stateCalls || 0) + 1;
            return Promise.resolve({
              on_top: true,
              autostart: window.__stateCalls === 1,
              notice: null,
              base: baseUrl,
            });
          }
          if (name === "set_topmost") return Promise.resolve(args[0]);
          if (name === "set_autostart") return Promise.resolve(args[0]);
          return Promise.resolve(true);
        },
      }),
    };
  }, BASE);

  await page.goto(BASE + "/static/widget.html", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 基础渲染
  const today = await page.locator("#todayTokens").innerText();
  const bars = await page.locator("#spark .bar").count();
  console.log("widget today:", today, "bars:", bars);
  if (bars !== 7) fails.push("widget: 迷你柱不是 7 根：" + bars);

  // A5：空柱子 cursor/title 与「可点击」一致
  const voids = page.locator("#spark .bar.void");
  const voidCount = await voids.count();
  if (voidCount) {
    const info = await voids.first().evaluate((el) => ({
      cursor: getComputedStyle(el).cursor,
      title: el.title,
    }));
    console.log("A5 void bar:", JSON.stringify(info));
    if (info.cursor !== "pointer") fails.push("A5: 空柱子 cursor 不是 pointer: " + info.cursor);
    if (!info.title.includes("点击")) fails.push("A5: 空柱子 title 没有点击提示: " + info.title);
    // 点空柱子应该触发 open_panel（带日期）
    await voids.first().click();
    await page.waitForTimeout(300);
    const calls = await page.evaluate(() => window.__calls);
    const opened = calls.filter((c) => c[0] === "open_panel");
    console.log("A5 click void -> open_panel calls:", JSON.stringify(opened));
    if (!opened.length) fails.push("A5: 空柱子点击没有触发 open_panel");
  } else {
    console.log("A5 最近 7 天没有空柱子，跳过（数据全有）");
  }

  // 有数据的柱子 cursor 也是 pointer
  const solid = page.locator("#spark .bar:not(.void)").first();
  if (await solid.count()) {
    console.log("solid bar cursor:", await solid.evaluate((el) => getComputedStyle(el).cursor));
  }

  // A9：右键菜单打开时应重新拉 get_state（不是用缓存）
  const before = await page.evaluate(() => window.__stateCalls);
  await page.mouse.click(200, 60, { button: "right" });
  await page.waitForTimeout(600);
  const after = await page.evaluate(() => window.__stateCalls);
  const menuVisible = await page.locator("#menu").isVisible();
  const autoLabel = await page.locator('#menu button[data-act="autostart"]').innerText();
  console.log("A9 get_state calls:", before, "->", after, "menu:", menuVisible, "label:", autoLabel);
  if (after <= before) fails.push("A9: 右键菜单没有重新拉 get_state（仍是缓存）");
  if (autoLabel.includes("✓")) fails.push("A9: 快捷方式已删，菜单仍显示 ✓: " + autoLabel);

  // 菜单项点击（置顶）
  await page.locator('#menu button[data-act="top"]').click();
  await page.waitForTimeout(500);
  const calls2 = await page.evaluate(() => window.__calls);
  console.log("menu top call:", JSON.stringify(calls2.filter((c) => c[0] === "set_topmost")));

  // 拖动：标题栏 mousedown + mousemove 应触发 move_to
  await page.mouse.move(120, 20);
  await page.mouse.down();
  await page.mouse.move(160, 40, { steps: 6 });
  await page.mouse.move(200, 70, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const calls3 = await page.evaluate(() => window.__calls);
  const moves = calls3.filter((c) => c[0] === "move_to");
  console.log("drag move_to calls:", moves.length, JSON.stringify(moves.slice(0, 3)));
  if (!moves.length) fails.push("A0: 拖动标题栏没有触发 move_to");
  const saved = calls3.filter((c) => c[0] === "save_position");
  console.log("save_position calls:", saved.length);

  await page.screenshot({ path: (process.env.TD_SHOTS || process.env.TEMP) + "/td-widget.png" });
  await browser.close();
  console.log("\nFAILURES(" + fails.length + ")");
  fails.forEach((f) => console.log("✗ " + f));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("SCRIPT ERROR:", e); process.exit(2); });
