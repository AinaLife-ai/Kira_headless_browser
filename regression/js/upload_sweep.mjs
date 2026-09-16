/**
 * 行为验证：上传会话的回收逻辑。
 *
 * 这一版用**可控的定时器**真跑回收，而不是只检查源码里"有没有 setInterval"：
 *   ① 慢速大文件：tick 前收一块、tick 后再收一块 → lastActive 让它活下来
 *   ② 被遗弃会话：tick 过了且期间无活动 → 必须被回收（再收块要失败）
 *
 * 做法：在 eval content.js **之前**把 setInterval 换成一个我们能手动推进的桩，
 * 这样就能确定性地触发清理，而不用真的等 10 分钟。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const src = readFileSync(`${PLUGIN}/browser-bridge/content.js`, "utf8");

const dom = new JSDOM(
  `<!doctype html><html><body><input id="f" type="file"></body></html>`,
  { runScripts: "outside-only", url: "https://example.com/" });
const { window } = dom;

let listener = null;
window.chrome = { runtime: { onMessage: { addListener: (fn) => { listener = fn; } } } };

// ── 可控定时器 ─────────────────────────────────────────────────────────
// content.js 用 setInterval 起清理器、用 Date.now() 判过期。
// 这里同时接管两者，让"时间"可以手动推进。
let clock = 1000000;
const timers = new Map();
let timerId = 0;
const realNow = Date.now;
window.Date = Object.create(Date);
window.Date.now = () => clock;
window.setInterval = (fn, ms) => {
  const id = ++timerId;
  timers.set(id, { fn, ms });
  return id;
};
window.clearInterval = (id) => { timers.delete(id); };
// 推进 clock 并触发所有定时器（跑若干轮，逼近 10 分钟空闲）
function advance(ms, rounds = 1) {
  for (let r = 0; r < rounds; r++) {
    clock += ms;
    for (const { fn } of Array.from(timers.values())) {
      try { fn(); } catch (_) { /* 忽略 */ }
    }
  }
}

window.eval(src);

const call = (action, payload) => new Promise((r) =>
  listener({ action, ...payload }, {}, (res) => r(res)));
const rejected = (res) => res && res.ok !== true;

const out = [];
const IDLE = 10 * 60 * 1000;   // 与 content.js 的 _UP_IDLE_MS 一致

// ── ① 慢速大文件：tick 中一直有活动 → 不能被回收 ────────────────────
{
  await call("upload_begin", { upload_id: "slow", selector: "#f", name: "s.bin", size: 1e9 });
  const r1 = await call("upload_chunk", { upload_id: "slow", index: 0, data: "AAAA" });
  // 推进 6 分钟（还没到 10 分钟空闲）→ 触发一次清理
  advance(6 * 60 * 1000);
  const r2 = await call("upload_chunk", { upload_id: "slow", index: 1, data: "AAAA" });
  // 再推进 6 分钟：上次活动只过了 6 分钟，仍不该被回收
  advance(6 * 60 * 1000);
  const r3 = await call("upload_chunk", { upload_id: "slow", index: 2, data: "AAAA" });
  out.push({
    name: "慢速传输中不会被定期清理误回收",
    ok: r1.ok === true && r2.ok === true && r3.ok === true,
    detail: `块1=${JSON.stringify(r1)} 块2(tick后)=${JSON.stringify(r2)} 块3=${JSON.stringify(r3)}`,
  });
  await call("upload_abort", { upload_id: "slow" });
}

// ── ② 被遗弃会话：tick 期间无活动 → 必须被回收 ──────────────────────
{
  await call("upload_begin", { upload_id: "stale", selector: "#f", name: "t.bin", size: 1e9 });
  const before = await call("upload_chunk", { upload_id: "stale", index: 0, data: "AAAA" });
  // 推进超过 10 分钟空闲，触发清理
  advance(11 * 60 * 1000);
  const after = await call("upload_chunk", { upload_id: "stale", index: 1, data: "AAAA" });
  out.push({
    name: "空闲超时的会话被定期清理器回收",
    ok: before.ok === true && rejected(after),
    detail: `回收前=${JSON.stringify(before)} 回收后=${JSON.stringify(after)}`,
  });
}

// ── ③ abort 立即生效 ────────────────────────────────────────────────
{
  await call("upload_begin", { upload_id: "ab", selector: "#f", name: "a.bin", size: 1e9 });
  await call("upload_abort", { upload_id: "ab" });
  const r = await call("upload_chunk", { upload_id: "ab", index: 0, data: "AAAA" });
  out.push({
    name: "abort 后会话立即失效",
    ok: rejected(r),
    detail: JSON.stringify(r),
  });
}

// ── ④ 清理器生命周期：有会话时起、空了停（用**增量**衡量）──────────
{
  // 先把上一段遗留的会话清干净，拿到一个"空"的基线
  advance(30 * 60 * 1000, 3);
  const base = timers.size;

  await call("upload_begin", { upload_id: "n1", selector: "#f", name: "n.bin", size: 1e9 });
  const grew = timers.size - base;          // 期望 +1（清理器启动）
  await call("upload_abort", { upload_id: "n1" });
  advance(11 * 60 * 1000);                  // 触发清理 → 会话空 → 应停掉
  const afterEmpty = timers.size;           // 期望回到 base
  out.push({
    name: "清理器随会话启停（不会空转）",
    ok: grew === 1 && afterEmpty === base,
    detail: `基线=${base} 建会话后 +${grew}（期望 +1）清空后=${afterEmpty}（期望 ${base}）`,
  });
}

window.Date = realNow;
console.log(JSON.stringify(out));
process.exit(0);
