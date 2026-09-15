/**
 * 协议常量 —— 与插件侧 protocol.py 保持镜像。
 * 改动任何一侧都必须同步另一侧。
 */

export const PROTOCOL_VERSION = 1;

// 消息类型
export const MSG_HELLO = "hello";
export const MSG_WELCOME = "welcome";
export const MSG_CMD = "cmd";
export const MSG_RESULT = "result";
export const MSG_EVENT = "event";
export const MSG_PING = "ping";
export const MSG_PONG = "pong";
export const MSG_ERROR = "error";

/** 分组形式，便于 `MSG.HELLO` 这样书写。与上面的常量是同一批值。 */
export const MSG = {
  CHUNK: "chunk",
  HELLO: MSG_HELLO,
  WELCOME: MSG_WELCOME,
  CMD: MSG_CMD,
  RESULT: MSG_RESULT,
  EVENT: MSG_EVENT,
  PING: MSG_PING,
  PONG: MSG_PONG,
  ERROR: MSG_ERROR,
};

// 命令名
export const MSG_EXTRA = { CHUNK: "chunk" };

export const CMD = {
  LIST_TABS: "list_tabs",
  GET_PAGE: "get_page",
  GET_SELECTION: "get_selection",
  EXTRACT: "extract",
  SCREENSHOT: "screenshot",
  WAIT_FOR: "wait_for",
  ACTIVATE_TAB: "activate_tab",
  CLOSE_TAB: "close_tab",
  NAVIGATE: "navigate",
  SCROLL: "scroll",
  CLICK: "click",
  TYPE: "type",
  EXEC_JS: "exec_js",
  UPLOAD: "upload",
  DOWNLOAD: "download",
  COOKIE_GET: "cookie_get",
  COOKIE_SET: "cookie_set",
  // 把无头后端有、扩展桥原先缺的能力补齐
  GET_INFO: "get_info",
  GO_BACK: "go_back",
  REFRESH: "refresh",
  HOVER: "hover",
  KEY_PRESS: "key_press",
  KEY_DOWN: "key_down",
  KEY_UP: "key_up",
  MOUSE_MOVE: "mouse_move",
  MOUSE_CLICK: "mouse_click",
  MOUSE_DOWN: "mouse_down",
  MOUSE_UP: "mouse_up",
  MOUSE_WHEEL: "mouse_wheel",
  MOUSE_DRAG: "mouse_drag",
  LIST_FILES: "list_files",
  DEBUG: "debug",
};

// 事件名
export const EVT = {
  PAGE_LOADED: "page_loaded",
  TAB_ACTIVATED: "tab_activated",
  TAB_CLOSED: "tab_closed",
  NAVIGATED: "navigated",
  USER_CONFIRMED: "user_confirmed",
};

// 默认服务端地址（与 KiraAI WebUI 同端口）
export const DEFAULT_HOST = "127.0.0.1";
export const DEFAULT_PORT = 5267;
// ⚠️ 这里的插件 id 必须与 manifest.json 的 plugin_id 一致。
// 默认按安装时填的令牌自动发现；如果路由变了，改这一处即可。
export const WS_PATH = "/ws/plugin/headless_browser/bridge";

/** 本机地址判定（ws:// 只允许用在回环） */
function isLoopbackHost(h) {
  const x = String(h || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (x === "localhost" || x === "127.0.0.1" || x === "::1"
      || x === "0.0.0.0" || x.endsWith(".localhost") || x === "127.0.0.1.") {
    return true;
  }
  // ⚠️ 与 security.py 的 is_local_host 对齐：IPv6 的各种等值写法
  //    也算回环 —— 否则 `::ffff:127.0.0.1` / `0:0:0:0:0:0:0:1`
  //    会被判成"远程主机"而强制要求 wss，连本机反而连不上。
  const bare = x.replace(/\.$/, "");
  if (bare.includes(":")) {
    // 全部展开成 8 组再比较，避免 `0:0:0:0:0:0:0:1` 这类写法漏判
    const parts = _expandV6(bare);
    if (parts && parts.slice(0, 7).every((p) => p === "0")
        && (parts[7] === "1" || parts[7] === "0")) {
      return true;
    }
  }
  // v4-mapped：::ffff:a.b.c.d
  const vm = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(bare);
  if (vm) return vm[1] === "127.0.0.1";
  return false;
}

/** 把 IPv6 展开成 8 个十六进制组（失败返回 null）。处理 `::` 缩写。 */
function _expandV6(h) {
  if ((h.match(/::/g) || []).length > 1) return null;
  let head = [], tail = [];
  if (h.includes("::")) {
    const [a, b] = h.split("::");
    head = a ? a.split(":") : [];
    tail = b ? b.split(":") : [];
  } else {
    head = h.split(":");
  }
  const need = 8 - head.length - tail.length;
  if (need < 0) return null;
  const all = [...head, ...Array(need).fill("0"), ...tail];
  if (all.length !== 8) return null;
  const norm = all.map((g) => (g === "" ? "0" : g.replace(/^0+(?=.)/, "").toLowerCase()));
  return norm.every((g) => /^[0-9a-f]{1,4}$/.test(g)) ? norm : null;
}

/**
 * 拼出完整的 WebSocket 地址。
 *
 * ⚠️ 令牌是放在 **query string** 里的。`ws://` 是明文传输 ——
 *    只要 host 不是本机，网络上的任何人都能抓到这枚令牌，
 *    然后拿到整个浏览器桥的权限。所以：
 *      * 回环地址 → `ws://`（本机，不出网卡，安全）
 *      * 其它地址 → **必须 `wss://`**（证书校验由浏览器完成）
 *    如果用户填的是远程主机又想用 ws://，这里直接抛错，
 *    而不是悄悄把令牌明文发出去。
 */
export function buildWsUrl(host, port, token) {
  const raw = (host || DEFAULT_HOST).trim();
  const p = String(port || DEFAULT_PORT).trim();

  // 解析出 scheme（用户可能填 `ws://h` / `wss://h` / 裸主机名）
  let scheme = "";
  let h = raw;
  const m = /^(wss?):\/\//i.exec(raw);
  if (m) {
    scheme = m[1].toLowerCase();
    h = raw.slice(m[0].length);
  }
  h = h.replace(/\/.*$/, "");                        // 去掉可能的路径
  // ⚠️ 端口只能从**带方括号的 IPv6** 或**单冒号的 host:port** 里剥。
  //    裸 IPv6 里到处都是冒号 —— `::1` 被 `/:\d+$/` 剥掉尾巴就成了 `::`，
  //    于是 isLoopbackHost 判不出回环、拼出的 URL 也是非法的
  //    （`wss://::5267/...`）。
  if (/^\[.*\]:\d+$/.test(h) || (!h.startsWith("[") && h.split(":").length === 2)) {
    h = h.replace(/:\d+$/, "");
  }

  const loopback = isLoopbackHost(h);
  // 默认：本机用 ws（不出网卡），其它主机用 wss（令牌在 query 里，必须加密）
  if (!scheme) scheme = loopback ? "ws" : "wss";
  // 唯一会拒绝的情况：**显式**要求用明文连非本机 —— 那才是真危险
  if (scheme === "ws" && !loopback) {
    throw new Error(
      `拒绝以明文 ws:// 连接非本机地址 ${h} —— 接入令牌会暴露在网络上。`
      + `请去掉地址里的 ws://（默认会用 wss://）。`
    );
  }
  // 裸 IPv6 在 URL 里必须加方括号，否则 `::1:5267` 无法解析。
  const wireHost = (h.includes(":") && !h.startsWith("[")) ? `[${h}]` : h;
  return `${scheme}://${wireHost}:${p}${WS_PATH}?token=${encodeURIComponent(token)}`;
}

export { isLoopbackHost };

// 保活：MV3 的 Service Worker 会被回收，用 alarms 定期唤醒
export const KEEPALIVE_ALARM = "kira-bridge-keepalive";
export const KEEPALIVE_PERIOD_MINUTES = 0.5; // 30 秒，Chrome 允许的最小值

// 重连退避（毫秒）
export const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000, 30000];

/**
 * 二次确认弹窗的兜底等待时间（毫秒）。
 * 正常情况下插件会在命令参数里带 `confirm_timeout`（秒），这里只是它没带时的
 * 兜底值。与插件侧 main.py 的 CONFIRM_WAIT_SECONDS 对应 —— 那边是 45 秒。
 */
export const DEFAULT_CONFIRM_TIMEOUT_MS = 45000;

// 存储键
export const STORE = {
  TOKEN: "kb_token",
  HOST: "kb_host",
  PORT: "kb_port",
  AUTO_CONNECT: "kb_auto_connect",
  LAST_STATUS: "kb_last_status",
  /** 用户是否手动点过「断开」—— 必须持久化：
   *  MV3 的 Service Worker 会被回收，内存标记撑不过一次回收。 */
  USER_DISCONNECTED: "kb_user_disconnected",
};
