/* ============================================================================
   Token Dashboard 共享前端逻辑
   面板（index.html）与浮窗（widget.html）共用：格式化、配色、主题、toast。

   这里的函数都必须是纯的 / 无 DOM 依赖（除 applyTheme / toast），
   浮窗页面结构简单，不应该被逼着实现整套面板逻辑。
   ========================================================================== */

/* ---------- 转义：所有插进 innerHTML 的外部字符串都要过这里 ---------- */
const esc = v => String(v === null || v === undefined ? "" : v)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

/* ---------- 数字格式化 ---------- */
const isNum = n => n !== null && n !== undefined && n !== "";
const fmt = n => isNum(n) ? Number(n).toLocaleString("en-US") : "-";
const money = n => isNum(n) ? "$" + Number(n).toFixed(2) : "-";
const pad2 = n => String(n).padStart(2, "0");

/** 紧凑数字：1 位小数 + B/M/K（面板、浮窗、图表坐标轴统一口径）。 */
function compact(n) {
  if (!isNum(n)) return "-";
  n = Number(n);
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
}
/** 坐标轴用：整数刻度去掉多余的 .0（300.0M → 300M）。 */
const compactAxis = n => compact(n).replace(/\.0([BMK])$/, "$1");

/* ---------- 日期 ---------- */
/** 本地日期（YYYY-MM-DD）。不能用 toISOString：那是 UTC，UTC+8 早上会少算一天。 */
function localDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}
function todayNoon() { const d = new Date(); d.setHours(12, 0, 0, 0); return d; }
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function daysAgo(n) { return localDate(addDays(todayNoon(), -n)); }

/** 后端存的是 UTC ISO；没有时区信息时按 UTC 解释。 */
function parseTs(iso) {
  if (!iso) return null;
  const s = String(iso);
  const d = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z");
  return isNaN(d.getTime()) ? null : d;
}
function fmtTime(iso) {
  const d = parseTs(iso);
  return d ? `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
             `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}` : "-";
}
function fullTime(iso) {
  const d = parseTs(iso);
  return d ? d.toLocaleString("zh-CN", { hour12: false }) : "-";
}
function relTime(iso) {
  const d = parseTs(iso);
  if (!d) return "-";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  if (s < 86400 * 30) return Math.floor(s / 86400) + " 天前";
  return d.toLocaleDateString("zh-CN");
}
const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
function weekdayOf(dateStr) {
  const d = new Date(dateStr + "T12:00:00");
  return isNaN(d.getTime()) ? "" : WEEKDAYS[d.getDay()];
}
/** 项目路径只显示最后两段（从中间切会切断目录名）。 */
function shortPath(p) {
  if (!p) return "-";
  const parts = String(p).split(/[\\/]+/).filter(Boolean);
  return parts.length <= 2 ? parts.join("/") || "-" : "…/" + parts.slice(-2).join("/");
}

/* ---------- 稳定配色：同一个 key 在任何图/徽章里颜色一致 ---------- */
const PALETTE = ["#3b82f6", "#22c55e", "#eab308", "#f97316", "#a855f7", "#06b6d4",
                 "#ef4444", "#84cc16", "#ec4899", "#14b8a6", "#8b5cf6", "#0ea5e9"];
const colorCache = new Map();
function colorFor(key) {
  const k = String(key === null || key === undefined ? "-" : key);
  if (colorCache.has(k)) return colorCache.get(k);
  const used = new Set(colorCache.values());
  let h = 0;
  for (const ch of k) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  let idx = h % PALETTE.length;
  for (let i = 0; i < PALETTE.length && used.has(PALETTE[idx]); i++) idx = (idx + 1) % PALETTE.length;
  colorCache.set(k, PALETTE[idx]);
  return PALETTE[idx];
}

/* ---------- 主题 ---------- */
const mqTheme = window.matchMedia("(prefers-color-scheme: light)");
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
function currentTheme() {
  const saved = localStorage.getItem("td-theme");
  if (saved === "light" || saved === "dark") return saved;
  return mqTheme.matches ? "light" : "dark";
}
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const btn = document.getElementById("btnTheme");
  // 图标 + 当前状态：避免「浅色」到底是状态还是动作的歧义，也避免初始 HTML
  // 写死「◐ 主题」导致首次加载时文案跳变（按钮内容一律由 JS 填）。
  if (btn) btn.textContent = t === "light" ? "☀ 浅色" : "☾ 深色";
}

/* ---------- HTTP / 提示 ---------- */
async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}
function toast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  // 固定 1.6 秒对「扫描完成：新增 1,234 条事件 · 56 行渠道数据」这种长句太短，
  // 按时长随字数缩放（下限 2 秒），中文阅读速度约 90ms/字。
  el._t = setTimeout(() => el.classList.remove("show"), Math.max(2000, String(msg).length * 90));
}
