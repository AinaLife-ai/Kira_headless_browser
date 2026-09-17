/**
 * 行为验证：上传过程中页面变化时，收尾不能"假报成功"。
 *
 * ⚠️ 背景：元素是在 uploadBegin 时解析的，而大文件要传十几秒（256MB ≈ 13s）。
 *    这段时间里 SPA 完全可能重新渲染 —— 旧的 input 已经从文档里摘掉。
 *    往脱离文档的元素上写 el.files **不会有任何效果**（表单里不会出现这个
 *    文件，用户点提交什么都没传），而过去这里照样 return ok:true。
 *
 * 三种情况都要对：
 *   C 正常                → 成功，且文件挂在当前元素上
 *   A 重渲染但元素仍匹配  → 成功，且挂在**新**元素上（不是旧的脱离元素）
 *   B 元素被彻底删除      → 必须失败，绝不能假报成功
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const src = readFileSync(`${PLUGIN}/browser-bridge/content.js`, "utf8");

const dom = new JSDOM(
  `<!doctype html><html><body><div id="w"><input id="f" type="file"></div></body></html>`,
  { runScripts: "outside-only", url: "https://example.com/" });
const { window } = dom;

let listener = null;
window.chrome = { runtime: { onMessage: { addListener: (fn) => { listener = fn; } } } };
window.DataTransfer = class {
  constructor() { this._i = []; this.files = []; }
  get items() {
    const s = this;
    return { add(f) { s._i.push(f); s.files = s._i.slice(); } };
  }
};
Object.defineProperty(window.HTMLInputElement.prototype, "files", {
  configurable: true,
  get() { return this.__f; },
  set(v) { this.__f = v; },
});

window.eval(src);
const call = (a, p) => new Promise((r) => listener({ action: a, ...p }, {}, r));
const nfiles = (sel) => {
  const el = window.document.querySelector(sel);
  return el && el.files ? el.files.length : 0;
};

const out = [];

// ⚠️ 下面每处 `size` 都写 **3**：`"AAAA"` 这段 base64 解码后正好是 3 字节。
//    声明值必须与实际内容一致 —— 虽然当前 size 只用于"上限预检"
//    （不参与完成判断），但声明 100 却只传 3 字节会让测试数据本身误导人，
//    也容易掩盖"size 预检坏了"这类问题。

// ── C 正常 ────────────────────────────────────────────────────────────
await call("upload_begin", { upload_id: "c", selector: "#f", name: "c.bin", size: 3 });
await call("upload_chunk", { upload_id: "c", index: 0, data: "AAAA" });
const rc = await call("upload_finish", { upload_id: "c" });
out.push({
  name: "正常上传：成功且文件挂在当前元素上",
  ok: rc.ok === true && nfiles("#f") === 1,
  detail: `${JSON.stringify(rc)} files=${nfiles("#f")}`,
});

// ── A 重渲染，元素仍匹配同一选择器 ───────────────────────────────────
await call("upload_begin", { upload_id: "a", selector: "#f", name: "a.bin", size: 3 });
window.document.querySelector("#w").innerHTML = '<input id="f" type="file">';
await call("upload_chunk", { upload_id: "a", index: 0, data: "AAAA" });
const ra = await call("upload_finish", { upload_id: "a" });
out.push({
  name: "重渲染后仍能完成，且挂到**新**元素上",
  ok: ra.ok === true && nfiles("#f") === 1,
  detail: `${JSON.stringify(ra)} files=${nfiles("#f")}`,
});

// ── B 元素被彻底删除 ─────────────────────────────────────────────────
await call("upload_begin", { upload_id: "b", selector: "#f", name: "b.bin", size: 3 });
window.document.querySelector("#w").innerHTML = "<p>没有输入框了</p>";
await call("upload_chunk", { upload_id: "b", index: 0, data: "AAAA" });
const rb = await call("upload_finish", { upload_id: "b" });
out.push({
  name: "目标被移除时必须失败（不得假报成功）",
  ok: rb.ok !== true,
  detail: JSON.stringify(rb),
});

console.log(JSON.stringify(out));
process.exit(0);
