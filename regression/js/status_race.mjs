// 面板状态轮询（refresh）的**竞态守卫**行为验证。
//
// ⚠️ 为什么需要它：`refresh()` 每 3 秒跑一次，而一次 `/status` 可能比 3 秒还慢
//    （桥接卡一下就会）。此时下一个请求已经发出，而 fetch **不保证先发先回**。
//    没有请求序号时，旧请求后到会把**新**状态覆盖回去 —— 界面上显示过期信息；
//    更糟的是旧请求若失败，会把已经渲染好的内容改成"读取失败"。
//
// 这里真跑 `web/index.html` 里的那个 refresh()，用可控的 Promise 制造
// "先发的后到"，检查它会不会被覆盖。输出 JSON 数组（与其它 .mjs 探针同格式）。
import fs from "node:fs";
import path from "node:path";

const PLUGIN = process.env.KIRA_PLUGIN_DIR || ".";
const html = fs.readFileSync(path.join(PLUGIN, "web", "app.js"), "utf-8");

// 抽出 `let _refreshSeq = 0;` 到 `refresh()` 结束（第一个行首 `}`）
const seqDef = "let _refreshSeq = 0;";
const fnIdx = html.indexOf("async function refresh()");
if (fnIdx < 0) {
  console.log(JSON.stringify([{ name: "定位 refresh()", ok: false,
                                detail: "找不到 refresh()" }]));
  process.exit(0);
}
const endIdx = html.indexOf("\n}\n", fnIdx);
if (endIdx < 0) {
  console.log(JSON.stringify([{ name: "定位 refresh()", ok: false,
                                detail: "找不到 refresh() 的结尾" }]));
  process.exit(0);
}
const seqIdx = html.indexOf(seqDef, fnIdx - 4000);
const src = html.slice(seqIdx >= 0 ? seqIdx : fnIdx, endIdx + 2);

// ⚠️ `refresh()` 现在把渲染**委托**给 `renderStatus()`（面板拆成
//    index.html + style.css + app.js 之后，函数也跟着分开了）。
//    只抽 refresh 的话它会在沙箱里 ReferenceError —— 看着像"守卫失效"，
//    其实是**探针没跟上重构** ✗ 所以这里把相关函数一起抽进来。
const extra = ["function renderStatus(", "function _renderDomains(",
               "function renderConfirmLog("].map((mk) => {
  const i = html.indexOf(mk);
  if (i < 0) return "";
  const rest = html.slice(i);
  const e = rest.indexOf("\n}\n");
  return e >= 0 ? rest.slice(0, e + 3) : "";
}).join("\n")
  // ⚠️ refresh() 的 catch 里现在会记一笔自检（`_lastErr` + `renderDiag()`）——
  //    沙箱里没有这两个名字的话，"迟到的失败响应"那条用例会直接
  //    ReferenceError 退出，看着像守卫失效，又是**探针没跟上重构** ✗
  + "\n" + (() => {
    const i = html.indexOf("let _lastErr =");
    return i >= 0 ? html.slice(i, html.indexOf(";", i) + 1) : "";
  })()
  // ⚠️ renderStatus 现在还会调 renderExtNotice（扩展版本提示）与 renderDiag ——
  //    沙箱里缺名字的话，整段 refresh 会抛错，用例会**看起来像守卫失效** ✗
  //    （又一次"探针没跟上重构"，所以这里按名字抽，抽不到就补个空壳）
  + "\n" + (() => {
    const i = html.indexOf("function renderExtNotice(");
    if (i < 0) return "const renderExtNotice = () => {};";
    const rest = html.slice(i);
    const e = rest.indexOf("\n}\n");
    return e >= 0 ? rest.slice(0, e + 2) : "const renderExtNotice = () => {};";
  })()
  + "\nconst renderDiag = () => {};\n";

const out = [];

// ── DOM 桩：任何 id 都给一个元素；createElement 给一个可挂子节点的节点 ──
function makeEl() {
  const el = {
    _text: "", _html: "",
    style: {}, className: "", children: [],
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    appendChild(c) { this.children.push(c); },
  };
  return el;
}
const els = {};
const $ = (id) => (els[id] || (els[id] = makeEl()));
const documentStub = {
  createElement: () => makeEl(),
  // 面板渲染不受控字段（域名/确认记录）走的是"DOM 节点 + textContent"，
  // 会用到 createTextNode —— 桩里缺它整段渲染就抛错 ✗
  createTextNode: (t) => { const e = makeEl(); e.textContent = String(t); return e; },
};

// ── api 桩：每次调用挂起，由测试代码决定何时以何值 resolve ──
const pending = [];
function api(pathname) {
  let resolve, reject;
  const p = new Promise((res, rej) => { resolve = res; reject = rej; });
  pending.push({ pathname, resolve, reject });
  return p;
}

// ── 装载 refresh()（把它需要的标识符都注入进去）──
let refresh;
try {
  const factory = new Function(
    "$", "api", "document", "_renderDomains", "console",
    src + "\n" + extra + "\nreturn refresh;");
  refresh = factory($, api, documentStub, () => {}, console);
} catch (e) {
  console.log(JSON.stringify([{ name: "装载 refresh()", ok: false,
                                detail: String(e).slice(0, 160) }]));
  process.exit(0);
}

const tick = () => new Promise((r) => setImmediate(r));

function status(over) {
  return Object.assign({
    bridge: { connected: true, browser: "Chrome", extension_version: "1.2.3",
              commands_sent: 7, commands_failed: 0, ws_path: "/ws/x" },
    policy: { read_only: false, allowed_domains: ["a.com"], blocked_domains: [],
              local_access: true, require_confirm: false },
    last_state: { tab_url: "https://a.com/", tab_title: "A" },
    confirm_log: [],
  }, over || {});
}

async function main() {
  // ① 迟到的**旧成功**不得覆盖新状态
  els.conn = makeEl(); els.browser = makeEl();
  pending.length = 0;
  const p1 = refresh();                       // 旧请求：慢
  await tick();
  const p2 = refresh();                       // 新请求：快
  await tick();
  pending[1].resolve(status({ bridge: { connected: true, browser: "NEW" } }));
  await p2; await tick();
  const afterNew = els.browser.textContent;
  pending[0].resolve(status({ bridge: { connected: false, browser: "STALE" } }));
  await p1; await tick();
  out.push({
    name: "迟到的旧响应不得覆盖新状态",
    ok: afterNew === "NEW" && els.browser.textContent === "NEW",
    detail: `先=${afterNew} 后被覆盖成=${els.browser.textContent}`
            + "（若为 STALE 说明序号守卫失效）",
  });

  // ② 迟到的**失败**不得把成功状态改成"读取失败"
  els.conn = makeEl(); els.browser = makeEl();
  pending.length = 0;
  const q1 = refresh();
  await tick();
  const q2 = refresh();
  await tick();
  pending[1].resolve(status({ bridge: { connected: true, browser: "OK2" } }));
  await q2; await tick();
  const connOk = els.conn.innerHTML;
  pending[0].reject(new Error("boom"));
  await q1; await tick();
  out.push({
    name: "迟到的失败响应不得覆盖成功状态",
    ok: connOk.includes("已连接") && els.conn.innerHTML.includes("已连接"),
    detail: `先=${connOk.slice(0, 24)} 后=${els.conn.innerHTML.slice(0, 24)}`,
  });

  // ③ 正常顺序下新状态生效（防"一刀切丢弃"把功能改没）
  els.conn = makeEl(); els.browser = makeEl();
  pending.length = 0;
  const r1 = refresh();
  await tick();
  pending[0].resolve(status({ bridge: { connected: true, browser: "SEQ1" } }));
  await r1; await tick();
  out.push({
    name: "正常顺序下新状态生效",
    ok: els.browser.textContent === "SEQ1",
    detail: `browser=${els.browser.textContent}`,
  });

  // ④ 单次失败仍要显示"读取失败"（守卫不能把错误吞掉）
  els.conn = makeEl();
  pending.length = 0;
  const s1 = refresh();
  await tick();
  pending[0].reject(new Error("boom"));
  await s1; await tick();
  out.push({
    name: "最新一次请求失败时仍要显示读取失败",
    ok: els.conn.innerHTML.includes("读取失败"),
    detail: els.conn.innerHTML.slice(0, 40),
  });

  console.log(JSON.stringify(out));
}

main();
