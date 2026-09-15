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

/** 拼出完整的 WebSocket 地址 */
export function buildWsUrl(host, port, token) {
  const h = (host || DEFAULT_HOST).trim();
  const p = String(port || DEFAULT_PORT).trim();
  return `ws://${h}:${p}${WS_PATH}?token=${encodeURIComponent(token)}`;
}

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
