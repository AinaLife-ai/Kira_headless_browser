/**
 * 真实 DOM 验证：分块流式上传能否把文件正确塞进 input[type=file]。
 *
 * 用 jsdom 跑 content.js 的 upload_begin / upload_chunk / upload_finish，
 * 然后从 input.files[0] 把内容读回来与原文逐字节比对。
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { createHash } from "crypto";

// ⚠️ 必须能被 KIRA_PLUGIN_DIR 覆盖。
//    写死路径的话，反向验证时会**悄悄测的还是原目录的文件** ——
//    检查永远通过，等于没有检查（我第一次就这么写的，反向验证才发现）。
// ⚠️ 用 fileURLToPath 而不是 URL.pathname：后者在 Windows 上会留下
//    `/C:/...` 这种带前导斜杠的路径，在含空格/非 ASCII 的目录里也
//    不会正确解码（%20 之类）。
const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const src = readFileSync(`${PLUGIN}/browser-bridge/content.js`, "utf8");

const dom = new JSDOM(`<!doctype html><html><body>
  <input id="f" type="file">
</body></html>`, { runScripts: "outside-only", url: "https://example.com/" });

const { window } = dom;

// content.js 用到的浏览器 API 桩
let listener = null;
window.chrome = {
  runtime: { onMessage: { addListener: (fn) => { listener = fn; } } },
};
window.__kiraFlash = null;

// jsdom 没有 DataTransfer，按规范做一个最小实现。
// ⚠️ files 必须是**真正的 FileList**（有 item() 和 length，且原型正确），
//    否则给 input.files 赋值会抛
//    "The provided value is not of type 'FileList'."
//    ——这是 jsdom 的桩限制，不是产品代码的问题。
class FileListStub {
  constructor(items) { this._items = items; this.length = items.length; }
  item(i) { return this._items[i] || null; }
  [Symbol.iterator]() { return this._items[Symbol.iterator](); }
}
Object.defineProperty(FileListStub.prototype, Symbol.toStringTag, { value: "FileList" });

class DataTransferStub {
  constructor() { this._items = []; this.files = new FileListStub([]); }
  get items() {
    const self = this;
    return {
      add(file) {
        self._items.push(file);
        self.files = new FileListStub(self._items.slice());
      },
    };
  }
}
window.DataTransfer = DataTransferStub;

// 记录 File 的字节，供上面 FileReader 读取
const OrigFile = window.File;
window.File = class extends OrigFile {
  constructor(parts, name, opts) {
    super(parts, name, opts);
    try {
      const total = parts.reduce((n, p) => n + (p.size || p.byteLength || 0), 0);
      const buf = new Uint8Array(total);
      let off = 0;
      for (const p of parts) {
        // ⚠️ 跨 realm：content.js 里的 Uint8Array 不是 Node 的构造函数，
        //    用 ArrayBuffer.isView 鸭子判断（instanceof 会漏）。
        if (p && p.__bytes) { buf.set(p.__bytes, off); off += p.__bytes.length; }
        else if (ArrayBuffer.isView(p)) { buf.set(new Uint8Array(p.buffer, p.byteOffset, p.byteLength), off); off += p.byteLength; }
        else if (p && typeof p.size === "number" && p.__bytes) { buf.set(p.__bytes, off); off += p.__bytes.length; }
      }
      this.__bytes = buf;
    } catch (e) { this.__bytes = new Uint8Array(0); }
  }
};
// 同样记录 Blob 的字节（File 由 Blob 构造）
const OrigBlob = window.Blob;
window.Blob = class extends OrigBlob {
  constructor(parts, opts) {
    super(parts, opts);
    try {
      const total = parts.reduce((n, p) => n + (p.length || p.size || 0), 0);
      const buf = new Uint8Array(total);
      let off = 0;
      for (const p of parts) {
        if (ArrayBuffer.isView(p)) { buf.set(new Uint8Array(p.buffer, p.byteOffset, p.byteLength), off); off += p.byteLength; }
        else if (p && p.__bytes) { buf.set(p.__bytes, off); off += p.__bytes.length; }
      }
      this.__bytes = buf;
    } catch (e) { this.__bytes = new Uint8Array(0); }
  }
};
// 让 jsdom 的 input.files 接受我们的 FileList
const proto = window.HTMLInputElement.prototype;
const desc = Object.getOwnPropertyDescriptor(proto, "files");
Object.defineProperty(proto, "files", {
  configurable: true,
  get() { return this.__files || (desc && desc.get ? desc.get.call(this) : null); },
  set(v) { this.__files = v; },
});

// 执行 content.js（它会注册 onMessage 监听）
window.eval(src);
if (!listener) { console.log("FAIL: content.js 没有注册监听"); process.exit(1); }

function call(action, payload) {
  return new Promise((resolve) => {
    listener({ action, ...payload }, {}, (res) => resolve(res));
  });
}

// jsdom 的 File/Blob 没有 arrayBuffer()，FileReader 又要求同 realm 实例。
// 最可靠的做法：把 window 的 FileReader 换成自己实现的（用 File 内部的 _bytes）。
window.FileReader = class {
  readAsArrayBuffer(file) {
    const bytes = file.__bytes || new Uint8Array(0);
    setTimeout(() => {
      this.result = bytes.buffer.slice(0);
      if (this.onload) this.onload();
    }, 0);
  }
};

function bytesOfFile(file) {
  return new Promise((resolve) => {
    const r = new window.FileReader();
    r.onload = () => resolve(new Uint8Array(r.result));
    r.readAsArrayBuffer(file);
  });
}

async function run(sizeBytes, step) {
  const raw = Buffer.alloc(sizeBytes);
  for (let i = 0; i < raw.length; i++) raw[i] = (i * 31 + 7) & 0xff;
  const wantHash = createHash("sha256").update(raw).digest("hex");

  const id = "up_test_" + sizeBytes;
  const r0 = await call("upload_begin", {
    upload_id: id, selector: "#f", name: "t.bin",
    mime: "application/octet-stream", size: sizeBytes, limit: 0,
  });
  if (!r0.ok) { console.log(`  ${sizeBytes}B upload_begin 失败: ${r0.error}`); return false; }

  let idx = 0;
  for (let i = 0; i < raw.length; i += step) {
    const b64 = raw.subarray(i, i + step).toString("base64");
    const r = await call("upload_chunk", { upload_id: id, index: idx, data: b64 });
    if (!r.ok) { console.log(`  ${sizeBytes}B 第 ${idx} 块失败: ${r.error}`); return false; }
    idx += 1;
  }

  const rf = await call("upload_finish", { upload_id: id });
  if (!rf.ok) {
    console.log(`  ${sizeBytes}B upload_finish 失败: ${rf.error}`, JSON.stringify(rf));
    return false;
  }

  const el = window.document.querySelector("#f");
  const fl = el.files;
  if (!fl || !fl.length) { console.log(`  ${sizeBytes}B input.files 为空`); return false; }
  // 我们的 FileListStub 用 item()；jsdom 原生 FileList 也可用索引
  const file0 = (typeof fl.item === "function" && fl.item(0)) || fl[0];
  if (!file0) { console.log(`  ${sizeBytes}B files[0] 取不到`); return false; }
  const got = await bytesOfFile(file0);
  const gotHash = createHash("sha256").update(Buffer.from(got)).digest("hex");

  const ok = gotHash === wantHash && got.length === raw.length;
  console.log(`  ${String(sizeBytes).padStart(9)}B  ${String(idx).padStart(4)} 块  `
    + `file.size=${got.length}  内容 ${ok ? "✓ 逐字节一致" : "✗ 不一致"}`);
  return ok;
}

(async () => {
  const step = 255 * 1024;
  let allOk = true;
  for (const sz of [1 * 1024 * 1024, 5 * 1024 * 1024, 20 * 1024 * 1024]) {
    const ok = await run(sz, step);
    allOk = allOk && ok;
  }
  // 顺序错乱必须被拒
  const bad = await call("upload_begin", {
    upload_id: "bad1", selector: "#f", name: "b.bin", size: 100, limit: 0,
  });
  const rbad = await call("upload_chunk", { upload_id: "bad1", index: 5, data: "AAAA" });
  console.log(`  乱序分块是否被拒: ${!rbad.ok ? "✓ 已拒绝" : "✗ 竟然通过"}`);
  allOk = allOk && !rbad.ok;
  // 超过上限必须被拒
  await call("upload_begin", { upload_id: "cap1", selector: "#f", name: "c.bin", size: 999999, limit: 0 });
  await call("upload_chunk", { upload_id: "cap1", index: 0, data: "A".repeat(4 * 1024 * 1024) });
  const rcap = await call("upload_chunk", { upload_id: "cap1", index: 1, data: "A".repeat(4 * 1024 * 1024) });
  console.log(`  超限是否被拒（limit=0 时不该拒）: ${rcap.ok ? "✓ 未误拒" : "✗ 误拒了"}`);
  // ⚠️ 结论必须并入 allOk：只打印不判会导致"误拒了"仍然 exit 0，
  //    套件把它当通过（诊断信息写了但没人看）。
  allOk = allOk && rcap.ok;

  // 上限生效时必须**真的**被拒（limit 设小，第二块应被拒）
  await call("upload_begin", {
    upload_id: "cap2", selector: "#f", name: "d.bin", size: 999999, limit: 1000,
  });
  const rcap2 = await call("upload_chunk", {
    upload_id: "cap2", index: 0, data: "A".repeat(4096),
  });
  console.log(`  超过上限是否被拒: ${!rcap2.ok ? "✓ 已拒绝" : "✗ 竟然通过"}`);
  allOk = allOk && !rcap2.ok;

  console.log(allOk ? "全部通过" : "有失败项");
  process.exit(allOk ? 0 : 1);
})();
