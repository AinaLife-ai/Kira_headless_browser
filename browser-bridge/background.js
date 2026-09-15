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
  state, sendRaw, sendResult, sendEvent, sendChunk,
  resolveTab, assertInjectable, callContent, detectBrowser,
  askUser, confirmTimeoutMs, resolveConfirm,
  PRIVILEGED_COMMANDS, confirmPromptFor,
} from "./shared.js";
import {
  getInfo, goBack, refresh, hover, keyPress, keyDownUp,
  mouseMove, mouseClick, mouseDownUp, mouseWheel, mouseDrag,
  listFiles, debugInfo,
} from "./commands.js";

// ─── 连接状态 ────────────────────────────────────────────────────────────────







/**
 * 用户是否手动点过「断开」。
 *
 * 与 state.intentionalClose 的区别：state.intentionalClose 只在本次 close 事件里有效，
 * 而 alarms 保活每 30 秒就会调用一次 ensureAlive() —— 如果它不看这个标记，
 * 用户点完「断开」，半分钟后连接又自己回来了，弹窗里那句"自动重连已暂停"
 * 就成了假话。只有手动点「连接」才会清掉它。
 */


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
    state.lastError = "尚未配置令牌";
    await setStatus({ connected: false, error: state.lastError });
    return { ok: false, error: state.lastError };
  }

  // 已经是打开状态就不重复连
  if (state.socket && (state.socket.readyState === WebSocket.OPEN || state.socket.readyState === WebSocket.CONNECTING)) {
    return { ok: true, already: true };
  }

  // 手动连接是用户明确表达「我要连」，允许覆盖上一次的手动断开；
  // 自动重连/保活则必须尊重它，否则「断开」形同虚设。
  if (manual) {
    state.userDisconnected = false;
  } else if (state.userDisconnected) {
    return { ok: false, error: "用户已手动断开" };
  }

  state.intentionalClose = false;
  clearTimeout(state.reconnectTimer);

  const url = buildWsUrl(cfg.host, cfg.port, cfg.token);
  console.log("[KiraBridge] 正在连接", url.replace(/token=.*/, "token=***"));

  try {
    state.socket = new WebSocket(url);
  } catch (e) {
    state.lastError = "创建连接失败：" + e.message;
    await setStatus({ connected: false, error: state.lastError });
    scheduleReconnect();
    return { ok: false, error: state.lastError };
  }

  return new Promise((resolve) => {
    let settled = false;
    const settle = (v) => { if (!settled) { settled = true; resolve(v); } };

    const openTimeout = setTimeout(() => {
      state.lastError = "连接超时：确认 KiraAI 正在运行，且插件已启用";
      settle({ ok: false, error: state.lastError });
    }, 8000);

    state.socket.onopen = () => {
      clearTimeout(openTimeout);
      state.reconnectAttempt = 0;
      state.lastError = "";
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

    state.socket.onmessage = (ev) => {
      handleMessage(ev.data).catch((e) => console.error("[KiraBridge] 消息处理异常", e));
    };

    state.socket.onerror = () => {
      // onerror 后必然跟 onclose，这里不做重连，避免双触发
      state.lastError = "连接出错，请确认 KiraAI 正在运行";
    };

    state.socket.onclose = (ev) => {
      clearTimeout(openTimeout);
      const wasOpen = ev.wasClean;
      console.log("[KiraBridge] 连接关闭", ev.code, ev.reason);

      chrome.action.setBadgeText({ text: "" });
      setStatus({ connected: false, error: state.lastError || `连接已断开 (${ev.code})` });

      state.socket = null;
      settle({ ok: false, error: state.lastError || `连接已断开 (${ev.code})` });

      if (!state.intentionalClose) scheduleReconnect();
    };
  });
}

export async function disconnect() {
  state.intentionalClose = true;
  state.userDisconnected = true;
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = null;

  if (state.socket) {
    try { state.socket.close(1000, "user disconnected"); } catch (_) {}
    state.socket = null;
  }

  chrome.action.setBadgeText({ text: "" });
  await setStatus({ connected: false, error: "" });
  return { ok: true };
}

function scheduleReconnect() {
  if (state.reconnectTimer) return;
  if (state.userDisconnected) return;

  const delay = RECONNECT_DELAYS[Math.min(state.reconnectAttempt, RECONNECT_DELAYS.length - 1)];
  state.reconnectAttempt += 1;

  console.log(`[KiraBridge] ${delay}ms 后重连（第 ${state.reconnectAttempt} 次）`);
  state.reconnectTimer = setTimeout(async () => {
    state.reconnectTimer = null;
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
  if (state.userDisconnected) return;

  if (!state.socket || state.socket.readyState === WebSocket.CLOSED || state.socket.readyState === WebSocket.CLOSING) {
    console.log("[KiraBridge] 保活检测：连接已断，尝试重连");
    state.reconnectAttempt = 0;
    await connect();
  }
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
    // ⚠️ 二次确认必须**集中在这里**判定。
    //    之前只在 navigate/click/type 里各写一次，导致 exec_js / upload /
    //    cookie_set 等高危命令完全绕过确认 —— 用户明明开了「写操作需确认」，
    //    扩展却静默执行了 JS、传了文件、改了 cookie。
    if (params && params.require_confirm && PRIVILEGED_COMMANDS.has(name)) {
      const [title, message] = confirmPromptFor(name, params);
      const ok = await askUser(title, message, {
        command: name,
        action: title,
        url: params.url || "",
        timeout_ms: confirmTimeoutMs(params),
      });
      if (!ok) {
        sendResult(id, true, { declined: true });
        return;
      }
    }

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
  // ⚠️ captureVisibleTab 截的是**该窗口当前活动标签**，而我们可能被要求
  //    截一张后台标签。那样会把「别的标签的内容」当成目标标签的截图返回，
  //    既是信息泄露，也让 AI 拿到错的画面。
  //    这里先确认目标就是活动标签，不是就报错（不擅自激活，避免打断用户）。
  if (!tab.active) {
    throw new Error(
      `标签页「${tab.title || tab.id}」不在前台，无法截图（截图只能截当前活动标签）。` +
      `请先用 activate_tab 切过去，或改为对当前活动标签截图。`
    );
  }
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

chrome.notifications.onButtonClicked.addListener((notifId, btnIdx) => {
  resolveConfirm(notifId, btnIdx);
});

chrome.notifications.onClicked.addListener((notifId) => {
  // 点通知本体（未点按钮）视为放弃本次操作
  resolveConfirm(notifId, -1);
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

// ⚠️ 这三个回调都是 async，必须自己兜住异常。
//    MV3 的 background 是 Service Worker：回调里抛出的
//    unhandled rejection 会被当成 worker 级错误，可能直接把 worker 干掉，
//    表现就是"扩展莫名其妙掉线了"。
async function safeRun(label, fn) {
  try {
    await fn();
  } catch (e) {
    console.error(`[KiraBridge] ${label} 失败`, e);
    try {
      await setStatus({ connected: false, error: `${label} 失败：${e.message || e}` });
    } catch (_) {}
  }
}

chrome.runtime.onInstalled.addListener(() => safeRun("onInstalled", async () => {
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  const cfg = await getConfig();
  if (cfg.token && cfg.autoConnect) {
    await connect();
  } else {
    await setStatus({ connected: false, error: "尚未配置令牌" });
  }
}));

chrome.runtime.onStartup.addListener(() => safeRun("onStartup", async () => {
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  const cfg = await getConfig();
  if (cfg.token && cfg.autoConnect) {
    await connect();
  }
}));

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === KEEPALIVE_ALARM) {
    return safeRun("keepalive", ensureAlive);
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
          connected: !!state.socket && state.socket.readyState === WebSocket.OPEN,
          readyState: state.socket ? state.socket.readyState : -1,
          ...st,
        });
        break;
      }
      case "test_ping":
        // ⚠️ 必须真的走一遍**服务端往返**。
        //    原来只调本地 listTabs()，就算 WebSocket 早断了也会返回成功，
        //    于是弹窗显示"链路正常"而实际根本连不上。
        try {
          if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
            sendResponse({ ok: false, error: "未连接到 KiraAI（请先点连接）" });
            break;
          }
          const tabs = await listTabs();
          sendResponse({
            ok: true,
            tab_count: tabs.tab_count,
            socket: "OPEN",
            note: "已在扩展侧执行一次真实命令；若插件面板显示未连接，说明链路不通",
          });
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
safeRun("bootstrap", async () => {
  const existing = await chrome.alarms.get(KEEPALIVE_ALARM);
  if (!existing) {
    chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  }
  const cfg = await getConfig();
  if (cfg.token && cfg.autoConnect) {
    await connect();
  }
});

console.log("[KiraBridge] Service Worker 已启动");
