/**
 * background.js / capabilities.js / commands.js 三者共享的运行时状态与工具函数。
 *
 * 为什么需要这个文件：ES 模块之间**不共享顶层作用域**。capabilities.js 和
 * commands.js 里调用的 resolveTab / callContent / sendRaw / MSG 等，
 * 如果只在 background.js 里定义，运行时就是 ReferenceError。
 * 这些命令（执行JS/上传/下载 + 补齐的 13 个）会整个不可用。
 */

import { MSG } from "./protocol.js";

/** 共享的可变状态（socket 会被 background.js 重新赋值，所以放对象里） */
export const state = {
  socket: null,
  reconnectAttempt: 0,
  reconnectTimer: null,
  intentionalClose: false,
  userDisconnected: false,
  lastError: "",
  /** popup 做链路验证时挂的一次性回调（收到服务端 ping 时触发） */
  probe: null,
};

// ─── 发送 ──────────────────────────────────────────────────────────────

export function sendRaw(obj) {
  const s = state.socket;
  if (!s || s.readyState !== WebSocket.OPEN) return false;
  try {
    s.send(JSON.stringify(obj));
    return true;
  } catch (e) {
    console.error("[KiraBridge] 发送失败", e);
    return false;
  }
}

export function sendResult(id, ok, data, error) {
  sendRaw({ type: MSG.RESULT, id, ok, data: data ?? null, error: error ?? null });
}

export function sendEvent(name, data) {
  sendRaw({ type: MSG.EVENT, name, data: data || {} });
}

/** 下载分块回传（扩展 → 插件） */
export function sendChunk(cmdId, uint8) {
  let bin = "";
  const step = 0x8000;
  for (let i = 0; i < uint8.length; i += step) {
    bin += String.fromCharCode.apply(null, uint8.subarray(i, i + step));
  }
  sendRaw({ type: MSG.CHUNK, id: cmdId, data: btoa(bin) });
}

// ─── 标签页解析 ────────────────────────────────────────────────────────

/** 取得目标标签页：显式 tab_id > 当前窗口激活页 > 任意窗口的激活页 */
export async function resolveTab(tabId) {
  if (tabId !== undefined && tabId !== null && tabId !== "") {
    const tab = await chrome.tabs.get(Number(tabId));
    if (!tab) throw new Error(`找不到标签页 ${tabId}`);
    return tab;
  }
  // query 返回空数组时也是"真值"，所以必须显式判长度
  let tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs || !tabs.length) {
    tabs = await chrome.tabs.query({ active: true });
  }
  const tab = tabs && tabs[0];
  if (!tab) throw new Error("找不到活动标签页");
  return tab;
}

const INJECTABLE = /^https?:/i;

export function assertInjectable(tab) {
  if (!tab.url || !INJECTABLE.test(tab.url)) {
    throw new Error(`当前页面（${tab.url || "未知"}）不允许注入脚本，请先切换到普通网页`);
  }
}

/**
 * 向 content script 发一条指令。
 * content script 在页面加载时就会注入，但如果扩展刚安装、页面还没刷新，
 * 就可能没有监听者。这里用 ping 探测一次，失败则手动补注入。
 */
export async function callContent(tab, action, payload = {}, timeout = 15000) {
  assertInjectable(tab);

  const send = (a, p) => chrome.tabs.sendMessage(tab.id, { action: a, ...p });

  try {
    await send("ping", {});
  } catch (_) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"],
      });
    } catch (e) {
      throw new Error(`无法注入页面脚本：${e.message}`);
    }
  }

  const result = await Promise.race([
    send(action, payload),
    new Promise((_, rej) => setTimeout(() => rej(new Error("页面操作超时")), timeout)),
  ]);

  if (result && result.__error) throw new Error(result.__error);
  return result;
}

export function detectBrowser() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "Edge";
  if (ua.includes("Chrome/")) return "Chrome";
  if (ua.includes("Firefox/")) return "Firefox";
  return "Unknown";
}

// ─── 二次确认 ──────────────────────────────────────────────────────────

const pendingConfirms = new Map();

/**
 * 二次确认弹窗等多久算用户拒绝。
 * 插件传的是它自己愿意等多久（CONFIRM_WAIT_SECONDS），这里留 5 秒提前量 ——
 * 让扩展先判超时并回一个明确的「拒绝」，比插件单方面超时更好排查。
 */
export function confirmTimeoutMs(params) {
  const waits = Number(params && params.confirm_timeout);
  const base = waits > 0 ? waits * 1000 : 45000;
  return Math.max(5000, base - 5000);
}

export async function askUser(title, message, meta = {}) {
  const id = "kira-confirm-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);
  const waitMs = Number(meta.timeout_ms) > 0 ? Number(meta.timeout_ms) : 45000;

  const decision = new Promise((resolve) => {
    const settle = (allowed, reason) => {
      if (!pendingConfirms.has(id)) return;
      pendingConfirms.delete(id);
      sendEvent("user_confirmed", {
        confirm_id: id, allowed, reason,
        command: meta.command || "",
        action: meta.action || title,
        url: meta.url || "",
      });
      resolve(allowed);
    };
    pendingConfirms.set(id, settle);
    setTimeout(() => settle(false, "timeout"), waitMs);
  });

  await chrome.notifications.create(id, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "KiraAI · " + title,
    message,
    buttons: [{ title: "允许" }, { title: "拒绝" }],
    requireInteraction: true,
    priority: 2,
  });

  return decision;
}

export function resolveConfirm(notifId, buttonIndex) {
  const settle = pendingConfirms.get(notifId);
  if (!settle) return false;
  settle(buttonIndex === 0, buttonIndex === 0 ? "approved" : "denied");
  chrome.notifications.clear(notifId);
  return true;
}

/**
 * 需要用户二次确认才能执行的命令（当 require_confirm 打开时）。
 *
 * ⚠️ 这里必须是**集中判定**。之前只在 navigate/click/type 里各写一次，
 * 结果 exec_js / upload / cookie_set 这三条高危命令完全绕过了确认 ——
 * 等于「我开了确认，但它悄悄执行了 JS、传了文件、改了 cookie」。
 */
export const PRIVILEGED_COMMANDS = new Set([
  "navigate", "click", "type", "scroll",
  "exec_js", "upload", "download", "cookie_set",
  "go_back", "refresh", "hover",
  "key_press", "key_down", "key_up",
  "mouse_click", "mouse_down", "mouse_up", "mouse_wheel", "mouse_drag",
  // ⚠️ 下面三个也是写操作，之前漏了 —— 开了「写操作需确认」时
  //    它们会被静默放行：AI 能在用户没批准的情况下**切走/关掉标签页**、
  //    或模拟鼠标移动（可能触发拖拽类交互）。
  //    这份清单必须与 Python 侧 protocol.py 的 WRITE_COMMANDS 保持同步。
  "activate_tab", "close_tab", "mouse_move",
]);

/**
 * 只读但**敏感**、同样需要用户确认的命令。
 *
 * ⚠️ 单独一个集合：cookie_get 只是读，不能塞进 PRIVILEGED_COMMANDS
 *    （那会把它误当成写操作，破坏只读模式/域名白名单的判定）；
 *    但导出的是 chrome.cookies.getAll 的**真实取值** = 登录态，
 *    用户应当看到"要导出 Cookie"并亲自批准。
 */
export const CONFIRM_ONLY_COMMANDS = new Set(["cookie_get"]);

/** 需要用户确认的全部命令（写操作 + 只读敏感） */
export const NEEDS_CONFIRM_COMMANDS = new Set([
  ...PRIVILEGED_COMMANDS, ...CONFIRM_ONLY_COMMANDS,
]);

/** 生成给用户看的确认文案 */
export function confirmPromptFor(name, params) {
  switch (name) {
    case "navigate":
      return ["跳转页面", `AI 想让浏览器打开：\n${params.url}`];
    case "cookie_get":
      return ["导出 Cookie",
              `AI 想读取并导出当前站点（${params.url || "当前页"}）的 Cookie。\n`
              + "这等于把登录态交给 AI，请确认是否允许。"];
    case "click":
      return ["点击元素",
        `AI 想点击页面上的：${params.selector || params.text || `第 ${params.index} 个元素`}`];
    case "type":
      return ["输入内容", `AI 想在 ${params.selector} 输入：\n${params.text}`];
    case "exec_js":
      return ["执行 JavaScript",
        `AI 想在当前页面执行这段代码：\n${String(params.script || "").slice(0, 300)}`];
    case "upload":
      return ["上传文件",
        `AI 想把文件「${params.name}」上传到 ${params.selector}`];
    case "download":
      return ["下载文件", `AI 想用你的浏览器会话下载：\n${params.url}`];
    case "cookie_set":
      return ["写入 Cookie",
        `AI 想向浏览器写入 ${(params.cookies || []).length} 条 cookie`];
    case "mouse_drag":
      return ["鼠标拖拽",
        `AI 想从 (${params.start_x},${params.start_y}) 拖到 (${params.end_x},${params.end_y})`];
    default:
      return [`执行 ${name}`, `AI 想执行浏览器操作：${name}`];
  }
}
