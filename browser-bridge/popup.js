/**
 * 扩展弹窗：配置令牌、查看连接状态、手动连接/断开。
 * 只做 UI，实际连接逻辑都在 background service worker 里。
 */

const $ = (id) => document.getElementById(id);

const STORE = {
  TOKEN: "kb_token",
  HOST: "kb_host",
  PORT: "kb_port",
  AUTO_CONNECT: "kb_auto_connect",
};

// ─── 状态渲染 ────────────────────────────────────────────────────────────────

function setDot(state) {
  const dot = $("dot");
  dot.className = "dot";
  if (state === "on") dot.classList.add("on");
  if (state === "err") dot.classList.add("err");
}

function setStatus(text, isError) {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (isError ? " err" : "");
}

const STATE_LABEL = {
  [-1]: "未初始化",
  0: "连接中…",
  1: "已连接",
  2: "关闭中…",
  3: "已断开",
};

async function refresh() {
  // ⚠️ sendMessage 在 service worker 被回收/未唤醒时会 **reject**。
  //    不接住的话，每 3 秒一次的轮询会不断产生 unhandled rejection，
  //    而且弹窗会一直停在旧状态（看不到"已断开"）。
  let r;
  try {
    r = await chrome.runtime.sendMessage({ action: "status" });
  } catch (e) {
    setDot("err");
    setStatus("无法连接扩展后台（" + (e && e.message ? e.message : e) + "）", true);
    return;
  }
  if (!r) {
    setDot("err");
    setStatus("扩展后台没有响应", true);
    return;
  }

  if (r.connected) {
    setDot("on");
    const ver = r.extension_version ? ` · 扩展 v${r.extension_version}` : "";
    const br = r.browser ? ` · ${r.browser}` : "";
    setStatus(`已连接${br}${ver}`);
  } else {
    setDot(r.error ? "err" : "");
    const label = STATE_LABEL[r.readyState] || "未知";
    setStatus(r.error || `未连接（${label}）`, !!r.error);
  }
}

// ─── 配置读写 ────────────────────────────────────────────────────────────────

async function loadConfig() {
  const s = await chrome.storage.local.get([
    STORE.TOKEN, STORE.HOST, STORE.PORT, STORE.AUTO_CONNECT,
  ]);
  $("token").value = s[STORE.TOKEN] || "";
  $("host").value = s[STORE.HOST] || "127.0.0.1";
  $("port").value = s[STORE.PORT] || 5267;
  $("autoConnect").checked = s[STORE.AUTO_CONNECT] !== false;
}

async function saveConfig() {
  const token = $("token").value.trim();
  const host = $("host").value.trim() || "127.0.0.1";
  const port = Number($("port").value) || 5267;
  const autoConnect = $("autoConnect").checked;

  await chrome.storage.local.set({
    [STORE.TOKEN]: token,
    [STORE.HOST]: host,
    [STORE.PORT]: port,
    [STORE.AUTO_CONNECT]: autoConnect,
  });

  setStatus("配置已保存");
  return token;
}

// ─── 事件绑定 ────────────────────────────────────────────────────────────────

$("btnSave").addEventListener("click", async () => {
  await saveConfig();
  setTimeout(refresh, 300);
});

$("btnConnect").addEventListener("click", async () => {
  const token = await saveConfig();
  if (!token) {
    setDot("err");
    setStatus("请先填写接入令牌", true);
    return;
  }

  setStatus("正在连接…");
  const r = await chrome.runtime.sendMessage({ action: "connect" });
  if (!r.ok) {
    setDot("err");
    setStatus(r.error || "连接失败", true);
  } else {
    setTimeout(refresh, 400);
  }
});

$("btnDisconnect").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ action: "disconnect" });
  setStatus("已手动断开（自动重连已暂停，重新连接请点「连接」）");
  setDot("");
});

$("btnTest").addEventListener("click", async () => {
  setStatus("正在测试…");
  const r = await chrome.runtime.sendMessage({ action: "test_ping" });
  if (r.ok) {
    // tab_count 只在 listTabs() 成功时才有；它失败时 background 会把错误
    // 吞掉，这里就会渲染出"可读取到 undefined 个标签页"。
    const n = (typeof r.tab_count === "number") ? r.tab_count : null;
    setStatus(n === null
      ? "链路正常（标签页数量读取失败）"
      : `链路正常，可读取到 ${n} 个标签页`);
  } else {
    setStatus("测试失败：" + (r.error || "未知错误"), true);
  }
});

// 回车即连接
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btnConnect").click();
});

// ─── 启动 ────────────────────────────────────────────────────────────────────

loadConfig().then(refresh);
setInterval(refresh, 2000);
