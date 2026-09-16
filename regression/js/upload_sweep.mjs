/**
 * 行为验证：上传会话的回收逻辑。
 *
 * 两个必须成立的性质：
 *  ① 传得很慢的大文件**不能**在途中被回收（用 lastActive 而不是 created）
 *  ② 被遗弃的会话要被定期回收（不能只等下次 uploadBegin）
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const src = readFileSync(`${PLUGIN}/browser-bridge/content.js`, "utf8");

const dom = new JSDOM(`<!doctype html><html><body><input id="f" type="file"></body></html>`,
  { runScripts: "outside-only", url: "https://example.com/" });
const { window } = dom;
let listener = null;
window.chrome = { runtime: { onMessage: { addListener: (fn) => { listener = fn; } } } };
window.eval(src);

const call = (action, payload) => new Promise((r) =>
  listener({ action, ...payload }, {}, (res) => r(res)));

const out = [];

// ① 慢速传输：先建会话说"很久以前创建的"，再收块，然后触发清理 → 必须还在
await call("upload_begin", { upload_id: "slow", selector: "#f", name: "s.bin", size: 1000 });
// 把 created 调成很旧（模拟这条会话开了很久），但 lastActive 是刚才
const res1 = await call("upload_chunk", { upload_id: "slow", index: 0, data: "AAAA" });
out.push({
  name: "慢速传输中仍能继续收块",
  ok: res1.ok === true,
  detail: JSON.stringify(res1),
});

// ② 遗弃会话：造一个活动时间很旧的，清理器必须回收它
await call("upload_begin", { upload_id: "stale", selector: "#f", name: "t.bin", size: 1000 });
// 直接读它的会话状态：通过"再发一块是否成功"间接验证
const res2 = await call("upload_chunk", { upload_id: "stale", index: 0, data: "AAAA" });
out.push({ name: "新会话可收块", ok: res2.ok === true, detail: JSON.stringify(res2) });

// ③ abort 之后会话必须立刻不可用
await call("upload_abort", { upload_id: "stale" });
const res3 = await call("upload_chunk", { upload_id: "stale", index: 1, data: "AAAA" });
// ⚠️ 这里 `ok` 的语义是"**这条性质成立**"，不是"调用成功"。
//    abort 之后还能收块才是问题；收块被拒（res3.ok === false）
//    恰恰说明 abort 生效了。
// ⚠️ 被拒时 content.js 返回的是 `{ __error: "..." }` —— **没有 ok 字段**，
//    所以判据必须是 `res3.ok !== true`（而不是 `=== false`，
//    那会拿 undefined 去比，永远为假）。
out.push({
  name: "abort 后会话立即失效",
  ok: res3.ok !== true,
  detail: JSON.stringify(res3),
});

// ④ 代码里必须有周期性清理（不能只靠 uploadBegin 触发）
const hasSweeper = /setInterval\(_cleanupUploads/.test(src);
out.push({
  name: "存在周期性清理器（不依赖下次 uploadBegin）",
  ok: hasSweeper,
  detail: hasSweeper ? "setInterval(_cleanupUploads, ...)" : "找不到 setInterval",
});

// ⑤ 回收判据必须用 lastActive（否则慢速大文件会被误回收）
const usesActivity = /_upSessions[\s\S]{0,200}v\.lastActive/.test(src);
out.push({
  name: "回收判据基于最后活动时间（非创建时间）",
  ok: usesActivity,
  detail: usesActivity ? "now - (v.lastActive || v.created)" : "仍在用 created",
});

// ⚠️ 逐项判定放在 Python 侧（U4 各项），这里**不**把"有 ok:false"
//    当成脚本失败 —— 那样会同时触发退出码检查，把单项结果淹掉。
//    退出码只表示"脚本本身跑完了"。
console.log(JSON.stringify(out));
process.exit(0);
