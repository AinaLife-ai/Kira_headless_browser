/**
 * Kira Browser Bridge —— 后台 Service Worker
 *
 * 职责：
 *   1. 维持与 KiraAI 插件的 WebSocket 长连接（含保活与自动重连）
 *   2. 接收插件下发的命令，在浏览器里执行并回传结果
 *   3. 主动上报浏览事件（页面加载、标签切换）供 AI「可感知」
 *
 * MV3 注意事项：
 *   - Service Worker 会被浏览器回收，WebSocket 会随之断开。这里用
 *     chrome.alarms 定期唤醒，唤醒后检测连接并按需重连。
 *   - chrome.scripting.executeScript 只能传函数，不能传字符串脚本。
 *     所有页面内操作都放在 content.js 里，通过 sendMessage 调用。
 */

import {
  PROTOCOL_VERSION, MSG, CMD, EVT,
  buildWsUrl, KEEPALIVE_ALARM, KEEPALIVE_PERIOD_MINUTES, RECONNECT_DELAYS, STORE,
  DEFAULT_CONFIRM_TIMEOUT_MS,
} from "./protocol.js";
import { execJs, upload, downloadViaSession, cookieGet, cookieSet } from "./capabilities.js";
import {
  getInfo, goBack, refresh, hover, keyPress, keyDownUp,
  mouseMove, mouseClick, mouseDownUp, mouseWheel, mouseDrag,
  listFiles, debugInfo,
} from "./commands.js";

// ─── 连接状态 ────────────────────────────────────────────────────────────────

let socket = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let intentionalClose = false;
let lastError = "";

/**
 * 用户是否手动点过「断开」。
 *
 * 与 intentionalClose 的区别：intentionalClose 只在本次 close 事件里有效，
 * 而 alarms 保活每 30 秒就会调用一次 ensureAlive() —— 如果它不看这个标记，
 * 用户点完「断开」，半分钟后连接又自己回来了，弹窗里那句"自动重连已暂停"
 * 就成了假话。只有手动点「连接」才会清掉它。
 */
let userDisconnected = false;

// ─── 状态持久化（popup 需要读） ──────────────────────────────────────────────

async function setStatus(patch) {
  const cur = (await chrome.storage.local.get(STORE.LAST_STATUS))[STORE.LAST_STATUS] || {};
  const next = Object.assign({}, cur, patch, { updatedAt: Date.now() });
  await chrome.storage.local.set({ [STORE.LAST_STATUS]: next });
}

async function getConfig() {
  const s = await chrome.storage.local.get([
    STORE.TOKEN, STORE.HOST, STORE.PORT, STORE.AUTO_CONNECT,
  ]);
  return {
    token: s[STORE.TOKEN] || "",
    host: s[STORE.HOST] || "127.0.0.1",
    port: s[STORE.PORT] || 5267,
    autoConnect: s[STORE.AUTO_CONNECT] !== false,
  };
}

// ─── 连接管理 ────────────────────────────────────────────────────────────────

export async function connect({ manual = false } = {}) {
  const cfg = await getConfig();

  if (!cfg.token) {
    lastError = "尚未配置令牌";
    await setStatus({ connected: false, error: lastError });
    return { ok: false, error: lastError };
  }

  // 已经是打开状态就不重复连
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return { ok: true, already: true };
  }

  // 手动连接是用户明确表达「我要连」，允许覆盖上一次的手动断开；
  // 自动重连/保活则必须尊重它，否则「断开」形同虚设。
  if (manual) {
    userDisconnected = false;
  } else if (userDisconnected) {
    return { ok: false, error: "用户已手动断开" };
  }

  intentionalClose = false;
  clearTimeout(reconnectTimer);

  const url = buildWsUrl(cfg.host, cfg.port, cfg.token);
  console.log("[KiraBridge] 正在连接", url.replace(/token=.*/, "token=***"));

  try {
    socket = new WebSocket(url);
  } catch (e) {
    lastError = "创建连接失败：" + e.message;
    await setStatus({ connected: false, error: lastError });
    scheduleReconnect();
    return { ok: false, error: lastError };
  }

  return new Promise((resolve) => {
    let settled = false;
    const settle = (v) => { if (!settled) { settled = true; resolve(v); } };

    const openTimeout = setTimeout(() => {
      lastError = "连接超时：确认 KiraAI 正在运行，且插件已启用";
      settle({ ok: false, error: lastError });
    }, 8000);

    socket.onopen = () => {
      clearTimeout(openTimeout);
      reconnectAttempt = 0;
      lastError = "";
      console.log("[KiraBridge] 已连接");

      chrome.action.setBadgeText({ text: "ON" });
      chrome.action.setBadgeBackgroundColor({ color: "#16a34a" });

      sendRaw({
        type: MSG.HELLO,
        protocol: PROTOCOL_VERSION,
        extension_version: chrome.runtime.getManifest().version,
        browser: detectBrowser(),
      });

      setStatus({ connected: true, error: "" });
      settle({ ok: true });
    };

    socket.onmessage = (ev) => {
      handleMessage(ev.data).catch((e) => console.error("[KiraBridge] 消息处理异常", e));
    };

    socket.onerror = () => {
      // onerror 后必然跟 onclose，这里不做重连，避免双触发
      lastError = "连接出错，请确认 KiraAI 正在运行";
    };

    socket.onclose = (ev) => {
      clearTimeout(openTimeout);
      const wasOpen = ev.wasClean;
      console.log("[KiraBridge] 连接关闭", ev.code, ev.reason);

      chrome.action.setBadgeText({ text: "" });
      setStatus({ connected: false, error: lastError || `连接已断开 (${ev.code})` });

      socket = null;
      settle({ ok: false, error: lastError || `连接已断开 (${ev.code})` });

      if (!intentionalClose) scheduleReconnect();
    };
  });
}

export async function disconnect() {
  intentionalClose = true;
  userDisconnected = true;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;

  if (socket) {
    try { socket.close(1000, "user disconnected"); } catch (_) {}
    socket = null;
  }

  chrome.action.setBadgeText({ text: "" });
  await setStatus({ connected: false, error: "" });
  return { ok: true };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  if (userDisconnected) return;

  const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)];
  reconnectAttempt += 1;

  console.log(`[KiraBridge] ${delay}ms 后重连（第 ${reconnectAttempt} 次）`);
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    const cfg = await getConfig();
    if (!cfg.autoConnect) return;
    if (!cfg.token) return;
    await connect();
  }, delay);
}

/** 保活与自愈：alarms 唤醒后调用 */
async function ensureAlive() {
  const cfg = await getConfig();
  if (!cfg.autoConnect || !cfg.token) return;
  if (userDisconnected) return;

  if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
    console.log("[KiraBridge] 保活检测：连接已断，尝试重连");
    reconnectAttempt = 0;
    await connect();
  }
}

function sendRaw(obj) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  try {
    socket.send(JSON.stringify(obj));
    return true;
  } catch (e) {
    console.error("[KiraBridge] 发送失败", e);
    return false;
  }
}

function sendResult(id, ok, data, error) {
  sendRaw({ type: MSG.RESULT, id, ok, data: data ?? null, error: error ?? null });
}

function sendEvent(name, data) {
  sendRaw({ type: MSG.EVENT, name, data: data || {} });
}

function detectBrowser() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "Edge";
  if (ua.includes("Chrome/")) return "Chrome";
  if (ua.includes("Firefox/")) return "Firefox";
  return "Unknown";
}

// ─── 消息分发 ────────────────────────────────────────────────────────────────

async function handleMessage(raw) {
  let msg;
  try { msg = JSON.parse(raw); } catch { return; }

  switch (msg.type) {
    case MSG.WELCOME:
      console.log("[KiraBridge] 服务端协议版本", msg.protocol);
      break;

    case MSG.PING:
      sendRaw({ type: MSG.PONG, ts: Date.now() });
      break;

    case MSG.CMD:
      await runCommand(msg.id, msg.name, msg.params || {});
      break;

    default:
      break;
  }
}

async function runCommand(id, name, params) {
  try {
    // 下载需要边收边回传分块，得知道自己的 cmdId
    const data = name === CMD.DOWNLOAD
      ? await downloadViaSession(params, id)
      : await execute(name, params);
    if (data && data.__declined) {
      sendResult(id, true, { declined: true });
    } else {
      sendResult(id, true, data);
    }
  } catch (e) {
    console.error(`[KiraBridge] 命令 ${name} 失败`, e);
    sendResult(id, false, null, e.message || String(e));
  }
}

async function execute(name, params) {
  switch (name) {
    case CMD.LIST_TABS:    return await listTabs();
    case CMD.GET_PAGE:     return await getPage(params);
    case CMD.GET_SELECTION:return await getSelection(params);
    case CMD.EXTRACT:      return await extract(params);
    case CMD.WAIT_FOR:     return await waitFor(params);
    case CMD.SCREENSHOT:   return await screenshot(params);
    case CMD.ACTIVATE_TAB: return await activateTab(params);
    case CMD.CLOSE_TAB:    return await closeTab(params);
    case CMD.NAVIGATE:     return await navigate(params);
    case CMD.SCROLL:       return await scroll(params);
    case CMD.CLICK:        return await click(params);
    case CMD.TYPE:         return await typeText(params);
    case CMD.EXEC_JS:      return await execJs(params);
    case CMD.UPLOAD:       return await upload(params);
    case CMD.COOKIE_GET:   return await cookieGet(params);
    case CMD.COOKIE_SET:   return await cookieSet(params);
    case CMD.GET_INFO:     return await getInfo(params);
    case CMD.GO_BACK:      return await goBack(params);
    case CMD.REFRESH:      return await refresh(params);
    case CMD.HOVER:        return await hover(params);
    case CMD.KEY_PRESS:    return await keyPress(params);
    case CMD.KEY_DOWN:     return await keyDownUp(params, true);
    case CMD.KEY_UP:       return await keyDownUp(params, false);
    case CMD.MOUSE_MOVE:   return await mouseMove(params);
    case CMD.MOUSE_CLICK:  return await mouseClick(params);
    case CMD.MOUSE_DOWN:   return await mouseDownUp(params, true);
    case CMD.MOUSE_UP:     return await mouseDownUp(params, false);
    case CMD.MOUSE_WHEEL:  return await mouseWheel(params);
    case CMD.MOUSE_DRAG:   return await mouseDrag(params);
    case CMD.LIST_FILES:   return await listFiles(params);
    case CMD.DEBUG:        return await debugInfo(params);
    default:
      throw new Error(`未知命令：${name}`);
  }
}

// ─── 工具函数 ────────────────────────────────────────────────────────────────

/** 取得目标标签页：显式 tab_id > 当前窗口激活页 > 任意窗口的激活页 */
async function resolveTab(tabId) {
  if (tabId !== undefined && tabId !== null && tabId !== "") {
    const tab = await chrome.tabs.get(Number(tabId));
    if (!tab) throw new Error(`找不到标签页 ${tabId}`);
    return tab;
  }

  // 注意：query 返回空数组时也是"真值"，所以不能写成 `a || b`，
  // 必须显式判长度，否则第一个查询没结果时不会走兜底。
  let tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs || !tabs.length) {
    tabs = await chrome.tabs.query({ active: true });
  }

  const tab = tabs && tabs[0];
  if (!tab) throw new Error("找不到活动标签页");
  return tab;
}

const INJECTABLE = /^https?:/i;

function assertInjectable(tab) {
  if (!tab.url || !INJECTABLE.test(tab.url)) {
    throw new Error(`当前页面（${tab.url || "未知"}）不允许注入脚本，请先切换到普通网页`);
  }
}

/**
 * 向 content script 发一条指令。
 * content script 在页面加载时就会注入，但如果扩展刚安装、页面还没刷新，
 * 就可能没有监听者。这里用 ping 探测一次，失败则手动补注入。
 */
async function callContent(tab, action, payload = {}, timeout = 15000) {
  assertInjectable(tab);

  const send = (a, p) => chrome.tabs.sendMessage(tab.id, { action: a, ...p });

  try {
    await send("ping", {});
  } catch (_) {
    // content script 不在，手动注入一次（幂等）
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

// ─── 只读命令实现 ────────────────────────────────────────────────────────────

async function listTabs() {
  const tabs = await chrome.tabs.query({});
  const out = tabs
    .filter((t) => t.url && !t.url.startsWith("chrome-extension://"))
    .map((t) => ({
      id: t.id,
      title: t.title || "",
      url: t.url || "",
      active: !!t.active,
      pinned: !!t.pinned,
      windowId: t.windowId,
    }));
  // 激活的排前面
  out.sort((a, b) => Number(b.active) - Number(a.active));
  return { tabs: out, tab_count: out.length };
}

async function getPage(params) {
  const tab = await resolveTab(params.tab_id);
  assertInjectable(tab);

  const detail = params.detail || "text";
  const res = await callContent(tab, "get_page", { detail }, 20000);

  return {
    url: tab.url || res.url || "",
    title: tab.title || res.title || "",
    content: res.content || "",
    detail,
  };
}

async function getSelection(params) {
  const tab = await resolveTab(params.tab_id);
  const res = await callContent(tab, "get_selection", {});
  return { url: tab.url, title: tab.title, content: res.content || "" };
}

async function extract(params) {
  const tab = await resolveTab(params.tab_id);
  const res = await callContent(tab, "extract", {
    selector: params.selector,
    attr: params.attr || null,
    limit: params.limit || 50,
  });
  // tab_url 交给插件侧做域名校验 —— 提取和读正文一样属于读操作
  return { url: tab.url, tab_url: tab.url, title: tab.title, items: res.items || [] };
}

async function waitFor(params) {
  const tab = await resolveTab(params.tab_id);
  const started = Date.now();
  const res = await callContent(tab, "wait_for", {
    selector: params.selector || null,
    text: params.text || null,
    timeout: params.timeout || 10,
  }, ((params.timeout || 10) + 5) * 1000);

  return {
    found: !!res.found,
    elapsed: ((Date.now() - started) / 1000).toFixed(1),
    url: tab.url,
    tab_url: tab.url,
  };
}

async function screenshot(params) {
  const tab = await resolveTab(params.tab_id);
  // captureVisibleTab 需要 activeTab 权限，且只能截当前活动标签
  const url = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  return { url: tab.url, title: tab.title, image: url };
}

// ─── 写命令实现 ──────────────────────────────────────────────────────────────

async function activateTab(params) {
  const tab = await resolveTab(params.tab_id);
  await chrome.tabs.update(tab.id, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
  return { ok: true, tab_id: tab.id, url: tab.url };
}

async function closeTab(params) {
  const tab = await resolveTab(params.tab_id);
  await chrome.tabs.remove(tab.id);
  return { ok: true, closed: tab.id };
}

async function navigate(params) {
  if (params.require_confirm) {
    const ok = await askUser("跳转页面", `AI 想让浏览器打开：\n${params.url}`,
      { command: CMD.NAVIGATE, action: "navigate", url: params.url,
        timeout_ms: confirmTimeoutMs(params) });
    if (!ok) return { __declined: true };
  }

  if (params.new_tab) {
    const tab = await chrome.tabs.create({ url: params.url, active: true });
    return { ok: true, tab_id: tab.id, url: params.url, navigated: true };
  }

  const tab = await resolveTab(params.tab_id);
  await chrome.tabs.update(tab.id, { url: params.url, active: true });
  return { ok: true, tab_id: tab.id, url: params.url, navigated: true };
}

async function scroll(params) {
  const tab = await resolveTab(params.tab_id);
  const res = await callContent(tab, "scroll", {
    direction: params.direction,
    amount: params.amount || null,
  });
  return { ok: true, changed: !!res.changed };
}

async function click(params) {
  if (params.require_confirm) {
    const target = params.selector || params.text || `第 ${params.index} 个元素`;
    const ok = await askUser("点击元素", `AI 想点击页面上的：${target}`,
      { command: CMD.CLICK, action: "click:" + target,
        timeout_ms: confirmTimeoutMs(params) });
    if (!ok) return { __declined: true };
  }

  const tab = await resolveTab(params.tab_id);
  assertInjectable(tab);

  const beforeUrl = tab.url;

  const res = await callContent(tab, "click", {
    selector: params.selector || null,
    text: params.text || null,
    index: params.index ?? null,
  }, 20000);

  // 点击可能触发跳转，稍等一下再读一次状态
  await new Promise((r) => setTimeout(r, 700));
  let afterUrl = beforeUrl;
  let navigated = false;
  try {
    const fresh = await chrome.tabs.get(tab.id);
    afterUrl = fresh.url;
    navigated = afterUrl !== beforeUrl;
  } catch (_) { /* 标签可能被关掉了 */ }

  return {
    ok: true,
    match: res.match || null,
    changed: !!res.changed,
    navigated,
    url: afterUrl,
  };
}

async function typeText(params) {
  if (params.require_confirm) {
    const ok = await askUser("输入内容", `AI 想在 ${params.selector} 输入：\n${params.text}`,
      { command: CMD.TYPE, action: "type:" + params.selector,
        timeout_ms: confirmTimeoutMs(params) });
    if (!ok) return { __declined: true };
  }

  const tab = await resolveTab(params.tab_id);
  assertInjectable(tab);

  const beforeUrl = tab.url;
  const res = await callContent(tab, "type", {
    selector: params.selector,
    text: params.text,
    submit: !!params.submit,
    clear_first: params.clear_first !== false,
  }, 20000);

  await new Promise((r) => setTimeout(r, 700));
  let afterUrl = beforeUrl;
  let navigated = false;
  try {
    const fresh = await chrome.tabs.get(tab.id);
    afterUrl = fresh.url;
    navigated = afterUrl !== beforeUrl;
  } catch (_) {}

  return { ok: true, submitted: !!params.submit, navigated, url: afterUrl };
}

// ─── 用户确认（通过通知实现，避免被页面遮挡） ────────────────────────────────

const pendingConfirms = new Map();

/**
 * 二次确认弹窗等多久算用户拒绝。
 * 插件传的是它自己愿意等多久（CONFIRM_WAIT_SECONDS），这里留 5 秒提前量 ——
 * 让扩展先判超时并回一个明确的「拒绝」，比插件单方面超时更好排查。
 */
function confirmTimeoutMs(params) {
  const waits = Number(params && params.confirm_timeout);
  const base = waits > 0 ? waits * 1000 : DEFAULT_CONFIRM_TIMEOUT_MS;
  return Math.max(5000, base - 5000);
}

async function askUser(title, message, meta = {}) {
  const id = "kira-confirm-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);

  // 插件会把它那边的命令超时算好传过来（CONFIRM_WAIT_SECONDS + 余量），
  // 两边必须一致：这里比插件先超时才是安全的，反之用户还没点，
  // AI 那边已经报"超时失败"了。
  const waitMs = Number(meta.timeout_ms) > 0
    ? Number(meta.timeout_ms)
    : DEFAULT_CONFIRM_TIMEOUT_MS;

  const decision = new Promise((resolve) => {
    // 无论用户点按钮、点通知本体还是超时，都统一走 settle，
    // 保证「允许/拒绝」这件事一定会回传一条审计事件给插件。
    const settle = (allowed, reason) => {
      if (!pendingConfirms.has(id)) return;
      pendingConfirms.delete(id);
      sendEvent(EVT.USER_CONFIRMED, {
        confirm_id: id,
        allowed: allowed,
        reason: reason,
        command: meta.command || "",
        action: meta.action || title,
        url: meta.url || "",
      });
      resolve(allowed);
    };
    pendingConfirms.set(id, settle);

    // 超时视为拒绝，避免工具调用悬挂
    setTimeout(() => settle(false, "timeout"), waitMs);
  });

  await chrome.notifications.create(id, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "KiraAI · " + title,
    message: message,
    buttons: [{ title: "允许" }, { title: "拒绝" }],
    requireInteraction: true,
    priority: 2,
  });

  await setStatus({ pendingConfirm: { id, title, message } });
  return decision;
}

chrome.notifications.onButtonClicked.addListener((notifId, btnIdx) => {
  const settle = pendingConfirms.get(notifId);
  if (settle) {
    settle(btnIdx === 0, btnIdx === 0 ? "approved" : "denied");
    chrome.notifications.clear(notifId);
  }
});

chrome.notifications.onClicked.addListener((notifId) => {
  const settle = pendingConfirms.get(notifId);
  if (settle) {
    // 点通知本体（未点按钮）视为放弃本次操作
    settle(false, "dismissed");
    chrome.notifications.clear(notifId);
  }
});

// ─── 事件上报（可感知） ──────────────────────────────────────────────────────

chrome.webNavigation.onCompleted.addListener(async (details) => {
  if (details.frameId !== 0) return; // 只关心主框架
  try {
    const tab = await chrome.tabs.get(details.tabId);
    sendEvent(EVT.PAGE_LOADED, {
      tab_id: tab.id,
      title: tab.title || "",
      url: tab.url || "",
    });
  } catch (_) {}
});

chrome.tabs.onActivated.addListener(async (info) => {
  try {
    const tab = await chrome.tabs.get(info.tabId);
    const all = await chrome.tabs.query({});
    sendEvent(EVT.TAB_ACTIVATED, {
      tab_id: tab.id,
      title: tab.title || "",
      url: tab.url || "",
      tab_count: all.length,
    });
  } catch (_) {}
});

chrome.tabs.onRemoved.addListener((tabId) => {
  sendEvent(EVT.TAB_CLOSED, { tab_id: tabId });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.url) {
    sendEvent(EVT.NAVIGATED, {
      tab_id: tabId,
      title: tab.title || "",
      url: tab.url || "",
    });
  }
});

// ─── 生命周期与保活 ──────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(async () => {
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  const cfg = await getConfig();
  if (cfg.token && cfg.autoConnect) {
    await connect();
  } else {
    await setStatus({ connected: false, error: "尚未配置令牌" });
  }
});

chrome.runtime.onStartup.addListener(async () => {
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  const cfg = await getConfig();
  if (cfg.token && cfg.autoConnect) {
    await connect();
  }
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === KEEPALIVE_ALARM) {
    await ensureAlive();
  }
});

// popup 发来的控制指令
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.action) {
      case "connect":
        sendResponse(await connect({ manual: true }));
        break;
      case "disconnect":
        sendResponse(await disconnect());
        break;
      case "status": {
        const st = (await chrome.storage.local.get(STORE.LAST_STATUS))[STORE.LAST_STATUS] || {};
        sendResponse({
          connected: !!socket && socket.readyState === WebSocket.OPEN,
          readyState: socket ? socket.readyState : -1,
          ...st,
        });
        break;
      }
      case "test_ping":
        // 让 popup 能验证链路是否真的通
        try {
          const tabs = await listTabs();
          sendResponse({ ok: true, tab_count: tabs.tab_count });
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
        break;
      default:
        sendResponse({ ok: false, error: "unknown action" });
    }
  })();
  return true; // 异步响应
});

// SW 启动时确保 alarm 存在（被回收后重启会走到这里）
(async () => {
  const existing = await chrome.alarms.get(KEEPALIVE_ALARM);
  if (!existing) {
    chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  }
  const cfg = await getConfig();
  if (cfg.token && cfg.autoConnect) {
    connect();
  }
})();

console.log("[KiraBridge] Service Worker 已启动");
