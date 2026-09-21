/**
 * 「截图时窗口最小化」的自愈行为：**借窗口一瞬，用完必须还回去**。
 *
 * 用户报的现象：
 *   `[screenshot] 用户浏览器（Edge · 扩展 v1.5.2）失败：Failed to capture tab:
 *    image readback failed`
 * 根因不是我们的 bug：窗口不可见（最小化/被完全遮挡）时 Chromium 读不到帧。
 * 但**处理**很差 —— 既不判断窗口状态、也不重试，还把浏览器原文丢出来。
 *
 * 现在的做法：只为"拿不到画面"这类错误去动窗口（恢复 → 等一帧 → 重截 → **还原**）。
 * 这个探针把三件事钉死：
 *   ① 最小化时确实会临时恢复并重截；
 *   ② **用完把窗口还原成最小化**（借东西要还，这是义务）；
 *   ③ 别的错误一律不碰用户窗口（不许为了重试把人家的窗口弹出来）。
 *
 * 做法：按名字从真 background.js 里抽函数（抽不到就自报 —— 别静默变成
 * "守卫失效"，这个坑踩过 4 次了）。输出最后一行是 JSON（供 Python 侧解析）。
 */
import fs from "node:fs";
import path from "node:path";

const PLUGIN = process.env.KIRA_PLUGIN_DIR || ".";
const SRC = fs.readFileSync(path.join(PLUGIN, "browser-bridge", "background.js"), "utf-8");

const results = [];
const push = (name, ok, detail = "") => results.push({ name, ok, detail });
const done = () => { console.log(JSON.stringify(results)); process.exit(0); };

// ── 按名字抽（抽不到就自报，别静默失败）────────────────────────────────
function pickFn(name) {
  const i = SRC.indexOf(`function ${name}(`);
  if (i < 0) return null;
  // async function / function 都从 marker 起
  const start = SRC.lastIndexOf("async ", i) === i - 6 ? i - 6 : i;
  const rest = SRC.slice(i);
  const e = rest.indexOf("\n}\n");
  return e >= 0 ? SRC.slice(start, i + e + 2) : null;
}

const pieces = {
  wait: (SRC.match(/const SHOT_RESTORE_WAIT_MS\s*=\s*\d+;/) || [])[0],
  tries: (SRC.match(/const SHOT_RESTORE_TRIES\s*=\s*\d+;/) || [])[0],
  isCap: pickFn("isCaptureUnavailable"),
  msg: pickFn("captureUnavailableMessage"),
  cap: pickFn("captureVisibleWithRestore"),
};

const missing = Object.entries(pieces).filter(([, v]) => !v).map(([k]) => k);
if (missing.length) {
  push("能按名字抽出截图自愈的实现（抽不到就自报，不静默失效）", false,
       `缺: ${missing.join(", ")}`);
  done();
}
push("能按名字抽出截图自愈的实现（抽不到就自报，不静默失效）", true,
     "SHOT_RESTORE_* / isCaptureUnavailable / captureUnavailableMessage / captureVisibleWithRestore");

// ── 沙箱：把 chrome 打桩，跑真的实现 ────────────────────────────────────
const sandboxSrc = `${pieces.wait}\n${pieces.tries}\n${pieces.isCap}\n${pieces.msg}\n${pieces.cap}`;

function makeApi({ winState = "normal", shot }) {
  const calls = { updates: [], shots: 0 };
  const chromeStub = {
    windows: {
      get: async () => ({ state: winState }),
      update: async (id, opts) => { calls.updates.push(opts); return {}; },
    },
    tabs: {
      captureVisibleTab: async () => {
        calls.shots += 1;
        if (typeof shot === "function") return shot(calls.shots);
        return "data:image/png;base64,AAA";
      },
    },
  };
  const factory = new Function("chrome", "console", `${sandboxSrc}\nreturn { captureVisibleWithRestore, isCaptureUnavailable };`);
  return { api: factory(chromeStub, { log() {}, warn() {}, error() {} }), calls };
}

const READBACK = () => {
  throw new Error("Failed to capture tab: image readback failed");
};
const IMG = () => "data:image/png;base64,OK";

// ① 最小化 + 第一次 readback 失败 → 恢复窗口 → 重截成功 → 还原最小化
{
  const { api, calls } = makeApi({
    winState: "minimized",
    shot: (n) => (n === 1 ? READBACK() : IMG()),
  });
  let img = null, err = null;
  try { img = await api.captureVisibleWithRestore(7, {}); } catch (e) { err = e; }
  const last = calls.updates[calls.updates.length - 1] || {};
  push("① 最小化时：临时恢复窗口 → 重截成功（借窗口一瞬）",
       !!img && calls.updates.some((u) => u.state === "normal"),
       `拿到图=${!!img} err=${err && err.message} 窗口操作=${JSON.stringify(calls.updates)}`);
  push("② 用完把窗口**还原成最小化**（借东西要还 —— 这条最重要）",
       last.state === "minimized",
       `最后一次窗口操作=${JSON.stringify(last)}`);
}

// ③ 恢复后仍截不到 → 报错必须能照做，且不许透传浏览器原文
{
  const { api } = makeApi({ winState: "minimized", shot: READBACK });
  let err = null;
  try { await api.captureVisibleWithRestore(7, {}); } catch (e) { err = e; }
  const m = String((err && err.message) || "");
  push("③ 仍然失败时：文案能照做（提到最小化/恢复），不透传 readback 原文",
       /最小化|不可见/.test(m) && /恢复/.test(m) && !/readback/i.test(m),
       m.slice(0, 90));
}

// ④ 别的错误（不是"拿不到画面"）→ 一次都不许碰用户窗口
{
  const { api, calls } = makeApi({
    winState: "normal",
    shot: () => { throw new Error("Cannot access a chrome:// URL"); },
  });
  let err = null;
  try { await api.captureVisibleWithRestore(7, {}); } catch (e) { err = e; }
  push("④ 非「拿不到画面」的错误：不碰用户窗口（不许为重试把窗口弹出来）",
       calls.updates.length === 0 && /chrome:\/\//.test(String(err && err.message)),
       `窗口操作=${calls.updates.length} 次；错误=${String(err && err.message).slice(0, 50)}`);
}

// ⑤ 开关关掉时（restore_window=false）→ 不碰窗口，直接给可照做的错
{
  const { api, calls } = makeApi({ winState: "minimized", shot: READBACK });
  let err = null;
  try { await api.captureVisibleWithRestore(7, { restoreWindow: false }); } catch (e) { err = e; }
  push("⑤ 用户关掉开关时不碰窗口，但错误照样可照做",
       calls.updates.length === 0 && /最小化/.test(String(err && err.message)),
       `窗口操作=${calls.updates.length} 次；错误=${String(err && err.message).slice(0, 60)}`);
}

// ⑥ 正常情况零开销：不碰窗口
{
  const { api, calls } = makeApi({ winState: "normal", shot: IMG });
  const img = await api.captureVisibleWithRestore(7, {});
  push("⑥ 窗口正常时零额外开销（不碰窗口、只截一次）",
       !!img && calls.updates.length === 0 && calls.shots === 1,
       `窗口操作=${calls.updates.length} 截图=${calls.shots} 次`);
}

done();
