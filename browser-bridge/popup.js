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

  const list = r.instances || [];
  const n = r.count || 0;
  renderInstances(list);
  if (n > 0) {
    setDot("on");
    setStatus(`已连接 ${n} 个实例` + (list.length > n ? `（共配对 ${list.length} 个）` : ""));
  } else {
    setDot(r.error ? "err" : "");
    setStatus(r.error || "未连接", !!r.error);
    // 发现失败时把**试过哪些端口**列出来 —— 用户一眼就能看出
    // "我的端口压根不在这个列表里"，而不是对着一句"没找到"干猜。
    // （这是最常见的失败原因：KiraAI 用了不常见的端口。）
    if (r.triedPorts && r.triedPorts.length) {
      setTriedPorts(r.triedPorts);
    }
  }
}

/** 在状态下面补一行"试过的端口"（只在发现失败时出现）。 */
function setTriedPorts(ports) {
  let el = $("tried");
  if (!el) {
    el = document.createElement("div");
    el.id = "tried";
    el.className = "hint tried";
    const st = $("status");
    if (st && st.parentNode) st.parentNode.insertBefore(el, st.nextSibling);
    else document.body.appendChild(el);
  }
  el.innerHTML = "已试过的端口（都不通）：" + escapeHtml(ports.join(", "))
    + "<br>KiraAI 的端口不在这里？在 KiraAI 数据目录的 <code>webui.json</code> "
    + "里能看到 <code>port</code>；把它填到上面的「端口」框里即可。";
}

/** 渲染实例列表。
 *
 *  现在可以**同时连多个** KiraAI —— 它们共用一个浏览器，都看得到同一个页面。
 *  所以弹窗要列出来"连上了哪几个"，而不是只显示一个状态。 */
function renderInstances(list) {
  const box = $("instances");
  if (!box) return;
  box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = '<div class="hint" style="margin:0">还没有配对任何 KiraAI 实例。</div>';
    return;
  }
  for (const it of list) {
    const on = it.status === "connected";
    const row = document.createElement("div");
    row.className = "inst";
    const label = it.label && it.label !== it.key ? `${it.label}（${it.key}）` : it.key;
    row.innerHTML = `<span class="dot ${on ? "on" : (it.error ? "err" : "")}"></span>`
      + `<span class="nm">${escapeHtml(label)}</span>`
      + `<span class="st">${on ? "已连接" : escapeHtml(it.error || "未连接")}</span>`;
    box.appendChild(row);
  }
}

function escapeHtml(t) {
  return String(t == null ? "" : t).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ─── 配置读写 ────────────────────────────────────────────────────────────────

async function loadConfig() {
  const s = await chrome.storage.local.get([STORE.INSTANCES, STORE.AUTO_CONNECT]);
  const first = (s[STORE.INSTANCES] || [])[0] || {};
  // 手动添加框：回填第一个实例，方便"再加一个"
  $("host").value = first.host || "127.0.0.1";
  $("port").value = first.port || "";
  $("token").value = first.token || "";
  $("autoConnect").checked = s[STORE.AUTO_CONNECT] !== false;
}

/** 手动把一个实例加进列表（自动探测覆盖不到的端口用这个兜底）。 */
async function addInstance() {
  const token = $("token").value.trim();
  const host = $("host").value.trim() || "127.0.0.1";
  const port = Number($("port").value);
  const autoConnect = $("autoConnect").checked;

  await chrome.storage.local.set({ [STORE.AUTO_CONNECT]: autoConnect });
  if (!token || !(port > 0)) {
    setStatus("要填「端口」和「令牌」才能加实例", true);
    return false;
  }
  let r;
  try {
    r = await chrome.runtime.sendMessage({
      action: "add_instance", inst: { host, port, token, label: "" },
    });
  } catch (e) {
    _renderBackendUnavailable(e);
    return false;
  }
  if (!r || !r.ok) {
    setStatus((r && r.error) || "添加失败", true);
    return false;
  }
  setStatus(`已添加 ${host}:${port}`);
  setTimeout(refresh, 600);
  return true;
}

// ─── 事件绑定 ────────────────────────────────────────────────────────────────

$("btnAdd").addEventListener("click", async () => {
  await addInstance();
});

$("btnDiscover").addEventListener("click", async () => {
  setStatus("正在本机寻找 KiraAI…");
  setDot("");
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
  if (r.ok) {
    setStatus(`找到 ${r.found} 个实例，已登记并连接。`);
    setTimeout(refresh, 600);
    return;
  }
  setDot("err");
  setStatus(r.error || "没有找到", true);
});

$("btnConnect").addEventListener("click", async () => {
  // ⚠️ 这里**不再要求令牌非空**：没有令牌时后台会先跑「自动检测」，
  //    把端口和令牌找出来再连。首次安装的用户什么都不用填。
  // 表单里填了端口/令牌就当作"手动加一个实例"，没填就直接连已登记的
  if ($("port").value.trim() && $("token").value.trim()) {
    await addInstance();
  }

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

/** 检查"允许用户使用脚本"开关是否打开。
 *
 *  ⚠️ 这个开关**扩展自己打不开** —— 它是 Chrome/Edge 的刻意设计：
 *     `userScripts` 必须由用户在扩展详情页手动开启，防止扩展静默执行任意代码。
 *     所以这里只能**检测 + 告诉用户去哪儿开**，不能替他开。
 *
 *  ⚠️ 它只影响「执行 JS」这一个能力：不开的话点击 / 输入 / 截图 / 列标签页
 *     等等全部照常工作。很多人根本用不到它 —— 所以只提示，不报警。
 */
async function checkUserScripts() {
  try {
    if (!chrome.userScripts) return { ok: false, why: "浏览器版本太老（需 120+）" };
    await chrome.userScripts.getScripts({});
    return { ok: true };
  } catch (_) {
    return { ok: false, why: "未开启「允许用户使用脚本」" };
  }
}

async function renderUserScriptsHint() {
  const el = $("usHint");
  if (!el) return;
  const st = await checkUserScripts();
  if (st.ok) { el.style.display = "none"; return; }
  el.style.display = "";
  el.innerHTML = "ℹ️ 执行 JS 不可用：" + escapeHtml(st.why)
    + "。<br>需要用的话，去 <b>edge://extensions</b> → 本扩展 →「详细信息」，"
    + "把「<b>允许用户使用脚本</b>」打开即可（<b>其它功能不受影响</b>，不用也不开没关系）。";
}

// 版本号：装在弹窗标题旁。
// ⚠️ 用户报"装了还是连不上"时，第一件要问清楚的就是**他装的是哪一版** ——
//    以前弹窗不显示版本，没法确认他是没更新、还是新版真有 bug。
try {
  const v = chrome.runtime.getManifest().version;
  if (v) $("ver").textContent = "v" + v;
} catch (_) { /* 拿不到就不显示，不影响其它功能 */ }

loadConfig().then(refresh);
renderUserScriptsHint();
setInterval(refresh, 2000);
