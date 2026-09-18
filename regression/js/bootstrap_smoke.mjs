/**
 * Service Worker **启动路径**冒烟 —— 真跑一遍 bootstrap。
 *
 * 为什么要它：这次线上事故就是"静态检查全绿、一启动就炸"——
 * 重构时 `getConfig()` 忘了改成列表版，返回的对象里**没有 `instances`**，
 * 而调用方直接读 `cfg.instances.length` → `undefined.length` →
 * bootstrap 抛异常、扩展整个起不来。
 *
 * 静态检查（正则扫源码）看不出这种"函数之间的形状不匹配"——
 * 只有**真的把它跑起来**才会现形。所以这里 stub 掉 chrome API，
 * 真 import 一遍 background.js，并断言启动过程中没有报错。
 */
const store = {};
const errors = [];

// 拦截 console.error —— background.js 的 safeRun 失败时会往这里写
const realErr = console.error;
console.error = (...a) => { errors.push(a.map(String).join(" ")); };

function listener() { return { addListener() {}, removeListener() {} }; }

globalThis.chrome = {
  storage: {
    local: {
      async get(keys) {
        const arr = Array.isArray(keys) ? keys : Object.keys(keys || {});
        const out = {};
        for (const k of arr) out[k] = store[k];
        return out;
      },
      async set(obj) { Object.assign(store, obj); },
    },
  },
  action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
  alarms: { get: async () => null, create() {}, onAlarm: listener() },
  runtime: {
    getManifest: () => ({ version: "test" }),
    onInstalled: listener(), onStartup: listener(), onMessage: listener(),
    sendMessage: async () => ({}),
  },
  tabs: {
    onActivated: listener(), onRemoved: listener(), onUpdated: listener(),
    query: async () => [], get: async () => null, create: async () => ({}),
    remove: async () => {}, update: async () => ({}),
    captureVisibleTab: async () => "",
  },
  notifications: { onClicked: listener(), onButtonClicked: listener() },
  scripting: { executeScript: async () => [] },
  webNavigation: { onCompleted: listener() },
  windows: { update: async () => {} },
};

// WebSocket：这里不连，只要不炸
globalThis.WebSocket = class { constructor() { this.readyState = 3; } send() {} close() {} };
globalThis.WebSocket.OPEN = 1;
// fetch：探测端口会用到 —— 直接拒，模拟"本机没有 KiraAI"
globalThis.fetch = async () => { throw new Error("no network in test"); };

const P = process.env.KIRA_PLUGIN_DIR + "/browser-bridge/";
const mod = await import(P + "background.js");
// 等 bootstrap 里那几个 await 跑完
await new Promise((r) => setTimeout(r, 400));

const results = [];
const push = (name, ok, detail) => results.push({ name, ok, detail });

const boot = errors.filter((e) => e.includes("bootstrap 失败"));
push("bootstrap 不抛异常（扩展起得来）", boot.length === 0,
     boot.join(" | ").slice(0, 160));

// 配置形状：调用方直接读 .length，必须是数组
const cfg = await mod.getConfig();
// ⚠️ 用可选链读：形状不对时要**干净地报失败**，而不是让测试自己崩掉
push("getConfig() 返回 instances 数组",
     Array.isArray(cfg.instances), `instances=${JSON.stringify(cfg.instances)}`);
push("没配对时 instances 为空数组（不是 undefined）",
     cfg.instances && cfg.instances.length === 0, `len=${cfg.instances?.length}`);

// 老版本的单份配置要能迁移过来
store.kb_token = "T1"; store.kb_host = "127.0.0.1"; store.kb_port = 6000;
const cfg2 = await mod.getConfig();
push("老配置能迁移成列表",
     !!cfg2.instances && cfg2.instances.length === 1
     && Number(cfg2.instances[0].port) === 6000
     && cfg2.instances[0].token === "T1",
     JSON.stringify(cfg2.instances));

// 空配置下 connect 也不能炸（会走自动发现）
let connectErr = "";
try { await mod.connect({ manual: true }); } catch (e) { connectErr = String(e); }
push("空配置下 connect() 不抛异常", connectErr === "", connectErr.slice(0, 140));

console.error = realErr;
console.log("RESULT:" + JSON.stringify(results));
// ⚠️ 必须显式退出：bootstrap 会挂重连定时器，node 不会自己结束
process.exit(0);
