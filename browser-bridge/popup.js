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

/** 后台（service worker）不可用时的统一渲染。 */
function _renderBackendUnavailable(e) {
  setDot("err");
  const msg = (e && e.message) ? e.message : String(e);
  setStatus("无法连接扩展后台（" + msg + "）", true);
}

async function refresh() {
  // ⚠️ sendMessage 在 service worker 被回收/未唤醒时会 **reject**。
  //    不接住的话，每 3 秒一次的轮询会不断产生 unhandled rejection，
  //    而且弹窗会一直停在旧状态（看不到"已断开"）。
  let r;
  try {
    r = await chrome.runtime.sendMessage({ action: "status" });
  } catch (e) {
    _renderBackendUnavailable(e);
    return;
  }
  if (!r) {
    _renderBackendUnavailable(new Error("扩展后台没有响应"));
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

  const patch = {
    [STORE.HOST]: host,
    [STORE.PORT]: port,
    [STORE.AUTO_CONNECT]: autoConnect,
  };
  // ⚠️ 令牌为空时**不要写进去**。
  //    「自动检测」会把令牌写进存储，而弹窗若还开着、输入框仍是空的，
  //    这时点「连接」会走到这里 —— 写空值就把刚检测到的令牌**擦掉**了，
  //    表现为"点了自动检测，一连接又变成未配置"。
  if (token) patch[STORE.TOKEN] = token;

  await chrome.storage.local.set(patch);
  setStatus("配置已保存");
  return token;
}

// ─── 事件绑定 ────────────────────────────────────────────────────────────────

$("btnSave").addEventListener("click", async () => {
  await saveConfig();
  setTimeout(refresh, 300);
});

$("btnDiscover").addEventListener("click", async () => {
  setStatus("正在本机寻找 KiraAI…");
  setDot("");
  $("candidates").style.display = "none";
  let r;
  try {
    r = await chrome.runtime.sendMessage({ action: "discover" });
  } catch (e) {
    _renderBackendUnavailable(e);
    return;
  }
  if (!r) {
    _renderBackendUnavailable(new Error("扩展后台没有响应"));
    return;
  }
  if (r.ok && r.single) {
    applyToForm(r.single);
    setStatus(`已找到 KiraAI（${r.single.host}:${r.single.port}），`
              + "端口和令牌都填好了，点「连接」即可。");
    return;
  }
  // ⚠️ 多个实例时**不替用户猜** —— 猜错的后果是
  //    "我对 A 说话，B 却动了我的浏览器"，比多点一下严重得多。
  if (r.multiple && r.multiple.length) {
    renderCandidates(r.multiple);
    setStatus(`本机找到 ${r.multiple.length} 个 KiraAI 实例，请选一个`
              + "（选错会连到另一个机器人）。");
    return;
  }
  setDot("err");
  setStatus(r.error || "没有找到", true);
});

/** 把接入信息回填到表单，让用户看得见填了什么。 */
function applyToForm(u) {
  $("host").value = u.host;
  $("port").value = u.port;
  $("token").value = u.token;
}

/** 多实例选择列表。 */
function renderCandidates(list) {
  const box = $("candidates");
  box.innerHTML = "";
  box.style.display = "flex";
  for (const c of list) {
    const label = c.instance ? `${c.instance}（端口 ${c.port}）` : `端口 ${c.port}`;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cand";
    b.textContent = label;
    if (c.data_dir) b.title = c.data_dir;
    b.addEventListener("click", async () => {
      let r2;
      try {
        r2 = await chrome.runtime.sendMessage({ action: "use_instance", hit: c });
      } catch (e) {
        _renderBackendUnavailable(e);
        return;
      }
      if (r2 && r2.ok && r2.single) {
        applyToForm(r2.single);
        box.style.display = "none";
        setStatus(`已选择 ${label}，点「连接」即可。`);
      }
    });
    box.appendChild(b);
  }
}

$("btnConnect").addEventListener("click", async () => {
  // ⚠️ 这里**不再要求令牌非空**：没有令牌时后台会先跑「自动检测」，
  //    把端口和令牌找出来再连。首次安装的用户什么都不用填。
  await saveConfig();

  setStatus("正在连接…");
  let r;
  try {
    r = await chrome.runtime.sendMessage({ action: "connect" });
  } catch (e) {
    // 与 refresh() 用同一套"后台不可用"渲染，避免弹窗卡在"连接中…"
    _renderBackendUnavailable(e);
    return;
  }
  // ⚠️ 先判空：background 没返回内容时 r 是 undefined，
  //    直接读 r.ok 会抛 TypeError，弹窗卡在"连接中…"不回来。
  if (!r) {
    _renderBackendUnavailable(new Error("扩展后台没有响应"));
    return;
  }
  if (!r.ok) {
    setDot("err");
    setStatus(r.error || "连接失败", true);
  } else {
    setTimeout(refresh, 400);
  }
});

$("btnDisconnect").addEventListener("click", async () => {
  try {
    await chrome.runtime.sendMessage({ action: "disconnect" });
  } catch (e) {
    _renderBackendUnavailable(e);
    return;
  }
  setStatus("已手动断开（自动重连已暂停，重新连接请点「连接」）");
  setDot("");
});

$("btnTest").addEventListener("click", async () => {
  setStatus("正在测试…");
  let r;
  try {
    r = await chrome.runtime.sendMessage({ action: "test_ping" });
  } catch (e) {
    _renderBackendUnavailable(e);
    return;
  }
  // ⚠️ 同 connect：background 没返回内容时 r 是 undefined，
  //    读 r.ok 会抛 TypeError，界面永远停在"正在测试…"。
  if (!r) {
    _renderBackendUnavailable(new Error("扩展后台没有响应"));
    return;
  }
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
