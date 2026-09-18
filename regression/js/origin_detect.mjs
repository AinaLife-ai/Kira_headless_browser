/* 用最小 DOM 桩加载**真的 content.js**，看它在不同来源的页面上会做什么。
 *
 * 场景由环境变量给：
 *   OD_HOST / OD_PORT / OD_PROTO   —— 页面地址
 *   OD_PAIR_OK=1|0                 —— 同源 /api/plugin/headless_browser/pair 是否返回令牌
 *
 * 输出一行 RESULT:<json>，字段：
 *   fetches  发了几个请求（公网站点上必须是 0）
 *   sent     递给后台的消息列表
 */
const HOST = process.env.OD_HOST || "127.0.0.1";
const PORT = process.env.OD_PORT || "9977";
const PROTO = process.env.OD_PROTO || "http";
const PAIR_OK = process.env.OD_PAIR_OK === "1";

const sent = [];
let fetches = 0;
const origin = PORT ? `${PROTO}://${HOST}:${PORT}` : `${PROTO}://${HOST}`;

globalThis.window = globalThis;
globalThis.location = {
  hostname: HOST, port: PORT, protocol: PROTO + ":", origin, href: origin + "/",
};
globalThis.sessionStorage = { getItem: () => null, setItem: () => {} };
globalThis.document = {
  addEventListener() {}, removeEventListener() {},
  documentElement: { scrollHeight: 0, clientHeight: 0 },
  body: { innerText: "", scrollHeight: 0, clientHeight: 0 },
  querySelectorAll: () => [], querySelector: () => null, getElementById: () => null,
  createTreeWalker: () => ({ nextNode: () => null }),
  title: "t", readyState: "complete",
};
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.chrome = {
  runtime: {
    onMessage: { addListener() {} },
    sendMessage(msg, cb) { sent.push(msg); if (cb) cb({ ok: true }); },
    getManifest: () => ({ version: "t" }),
  },
};
globalThis.fetch = async (url) => {
  fetches++;
  const body = PAIR_OK
    ? { ok: true, token: "TOK-" + PORT, instance: "main" }
    : { ok: false };
  return { ok: PAIR_OK, json: async () => body, status: PAIR_OK ? 200 : 404 };
};

// ⚠️ 路径由环境变量给，不写死 —— 否则测的可能不是仓库里那份代码
await import(process.env.OD_PLUGIN_DIR + "/browser-bridge/content.js");
await new Promise((r) => setTimeout(r, 400));
process.stdout.write("RESULT:" + JSON.stringify({ fetches, sent }) + "\n");
process.exit(0);
