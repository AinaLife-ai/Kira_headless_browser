import { MSG } from "./protocol.js";
import {
  resolveTab, assertInjectable, callContent, sendRaw, sendChunk,
} from "./shared.js";

/**
 * 扩展桥补齐的三项能力（在 background.js 里实现）：
 *
 *   1. exec_js    —— 执行任意 JS。MV3 下 `chrome.scripting.executeScript` 的
 *                    eval 会被**页面 CSP** 挡掉（MV3 起不再豁免），所以走
 *                    `chrome.userScripts` API —— 官方专为"运行任意代码字符串"
 *                    设计、明确豁免远端代码策略。
 *                    ⚠️ Chrome 138+ 需要用户在扩展详情页手动打开
 *                    「Allow User Scripts」开关，否则会报错。这里会把这种情况
 *                    翻译成一句用户能照做的话。
 *
 *   2. upload     —— 把本地文件塞进 input[type=file]。MV3 拿不到真实文件路径，
 *                    所以改由**插件把文件内容分块送过来**，扩展在页面里
 *                    用 `DataTransfer` + `new File([blob])` 构造 FileList 塞进去。
 *                    （这正好绕开了"扩展读不到本地文件"的限制，因为内容是从
 *                    插件那边来的。）
 *
 *   3. download   —— 用**用户浏览器的会话**去抓 URL，再分块回传给插件落盘。
 *                    比 `chrome.downloads` 好：能带用户的 Cookie/登录态，
 *                    也不会把文件塞进 Chrome 的下载目录。
 */

// ─── 1. 执行任意 JS ──────────────────────────────────────────────────────

// ⚠️ 只缓存**成功**结果。
//    如果连"开关没打开"也缓存，用户按提示去打开开关之后再试，
//    仍然会拿到缓存的失败结果 —— 只能重载扩展才行，体验很糟。
let userScriptsUsable = null;

async function ensureUserScripts() {
  if (userScriptsUsable && userScriptsUsable.ok) return userScriptsUsable;
  if (!chrome.userScripts) {
    userScriptsUsable = { ok: false, reason: "no_api" };
    return userScriptsUsable;
  }
  try {
    // 探测开关是否已打开：读一下配置即可，没权限会抛
    await chrome.userScripts.getScripts({});
    userScriptsUsable = { ok: true };
  } catch (e) {
    userScriptsUsable = { ok: false, reason: "toggle_off", detail: e.message };
  }
  return userScriptsUsable;
}

async function execJs(params) {
  const { script, tab_id } = params;
  if (!script || !script.trim()) throw new Error("缺少 script");

  const tab = await resolveTab(tab_id);
  assertInjectable(tab);

  const chk = await ensureUserScripts();
  if (!chk.ok) {
    if (chk.reason === "no_api") {
      throw new Error(
        "chrome.userScripts 不可用。可能的原因：\n" +
        "  1）浏览器版本低于 Chrome/Edge 120；\n" +
        "  2）Chrome 138+ 下**没有打开**扩展详情页里的「允许用户脚本 / " +
        "Allow User Scripts」开关 —— 关掉它会让 chrome.userScripts " +
        "直接变成 undefined，\n" +
        "     这种情况看起来就像是「浏览器不支持」。\n" +
        "请先在 chrome://extensions → Kira Browser Bridge → 详情 里检查该开关；\n" +
        "若确认已打开仍不行，可改用无头后端执行 JavaScript。"
      );
    }
    throw new Error(
      "执行任意 JavaScript 需要在扩展页手动打开一个开关：\n" +
      "  打开 chrome://extensions → 找到 Kira Browser Bridge → 详情 → " +
      "打开「允许用户脚本 / Allow User Scripts」，然后重试。\n" +
      "（这是 Chrome 138+ 的安全要求，扩展无法代劳。）"
    );
  }

  // ⚠️ 不要强制包成表达式 `(${script})` ——
  //    那样只能接受**单条表达式**，多语句（`const a=1; return a;`）会语法错误。
  //    正确做法：整段当**函数体**执行，用户既可以直接写表达式
  //    （自动补 return），也可以写多语句 + 显式 return。
  // ⚠️ **只在 SyntaxError 时**才退回函数体模式。
  //    如果对任何异常都退回，一条"能解析但运行到一半抛错"的表达式
  //    （例如 `items.forEach(i => post(i))`）会被**执行两遍** ——
  //    副作用重复，这比报错危险得多。
  const wrapped = [
    "(function(){",
    "  const __src = " + JSON.stringify(script) + ";",
    "  let expr;",
    "  try {",
    "    // 用 new Function 只做**语法检查**，不执行",
    "    new Function('return (' + __src + ')');",
    "    expr = true;",
    "  } catch (e) {",
    "    if (e instanceof SyntaxError) { expr = false; }",
    "    else { return { __error: String(e && e.message || e) }; }",
    "  }",
    "  try {",
    "    if (expr) { return eval('(' + __src + ')'); }",
    "    return (new Function(__src))();",
    "  } catch (e) { return { __error: String(e && e.message || e) }; }",
    "})()",
  ].join("\n");

  const jsPayload = [{ code: wrapped }];
  let results;
  try {
    results = await chrome.userScripts.execute({
      target: { tabId: tab.id },
      js: jsPayload,
      world: "USER_SCRIPT",
      injectImmediately: true,
    });
  } catch (e) {
    // 有些版本不支持 world 参数，退回默认 world
    results = await chrome.userScripts.execute({
      target: { tabId: tab.id },
      js: jsPayload,
    });
  }

  const first = (results && results[0]) || {};
  if (first.error) throw new Error("执行出错：" + first.error);
  // ⚠️ 注入的包装器会把**脚本自身的异常**吞成 `{ __error: "..." }` 返回
  //    （见上面的 wrap）。first.error 只能反映 chrome.userScripts.execute
  //    这一层失败，脚本抛错时 first.error 是空的 —— 若不在这里翻出来，
  //    就会以"成功"回给插件（sendResult(id, true, ...)），
  //    和 shared.js 里 callContent 把 __error 当错误的做法**不一致**。
  const res = first.result ?? null;
  if (res && typeof res === "object" && "__error" in res) {
    throw new Error(String(res.__error));
  }
  return { url: tab.url, result: res };
}

// ─── 2. 上传文件（内容由插件分块送来）──────────────────────────────────

/**
 * 上传会话表。
 *
 * ⚠️ 为什么要有这个：整份文件塞进**一条** WebSocket 消息是行不通的 ——
 *    uvicorn 的 ws_max_size 默认 16 MiB，KiraAI 没覆盖它；实测整条帧
 *    超过 16 MiB 时对端会回 1009 并**关闭整个连接**，一次超大上传会把
 *    连接打断、连带后面所有命令一起崩。
 *    base64 又放大 4/3，所以"一条消息"的文件上限其实只有 ~12 MiB。
 *
 * → 改成：upload 只建立会话，之后每个分块单独一条消息送过来，
 *   扩展侧边收边拼，最后 finish 时一次性构造 File。
 *   这样单帧大小恒定（~340KB），内存与帧尺寸都与文件大小无关。
 */
const _uploads = new Map();

function _uploadKey(id) {
  return String(id || "");
}

/** 开始一次上传：登记会话，把文件大小/上限先校验掉。 */
async function upload(params) {
  const { selector, name, mime, limit, size, tab_id } = params;
  if (!selector) throw new Error("缺少 selector");
  if (!name) throw new Error("缺少文件名");

  // 先按声明的大小做一次快速否决，避免白传一遍
  const declared = Number(size) || 0;
  const cap = Number(limit) || 0;
  if (cap > 0 && declared > cap) {
    throw new Error(`文件过大（${declared} > 上限 ${cap} 字节），已拒绝上传`);
  }

  const tab = await resolveTab(tab_id);
  assertInjectable(tab);

  const id = "up_" + Date.now().toString(36) + "_"
    + Math.random().toString(36).slice(2, 10);
  _uploads.set(_uploadKey(id), {
    id, tabId: tab.id, selector, name,
    path: params.path || name,
    mime: mime || "application/octet-stream",
    parts: [], received: 0, cap, expectIndex: 0, created: Date.now(),
  });
  // 顺手清掉过期的会话（比如插件中途崩了）
  for (const [k, v] of _uploads) {
    if (Date.now() - v.created > 10 * 60 * 1000) _uploads.delete(k);
  }
  return { ok: true, upload_id: id, url: tab.url };
}

/** 收一个分块。只做长度累加与拼接，**不逐块解码**（省内存）。 */
async function uploadChunk(params) {
  const st = _uploads.get(_uploadKey(params.upload_id));
  if (!st) throw new Error("上传会话不存在或已过期（请重新发起上传）");
  const { index, data } = params;
  if (typeof data !== "string" || !data) throw new Error("分块内容为空");

  // 顺序校验：乱序/丢块会让拼出来的文件损坏，宁可明确报错
  const idx = Number(index) || 0;
  if (idx !== st.expectIndex) {
    throw new Error(`分块顺序不对（期望 ${st.expectIndex}，收到 ${idx}）`);
  }
  // 扣掉 padding 再累加，得到**精确**的已收字节数
  const pad = (data.match(/=+$/) || [""])[0].length;
  st.received += Math.floor(((data.length - pad) * 3) / 4);
  if (st.cap > 0 && st.received > st.cap) {
    _uploads.delete(_uploadKey(st.id));
    throw new Error(`文件超过上限 ${st.cap} 字节，已中止上传`);
  }
  st.parts.push(data);
  st.expectIndex = idx + 1;
  return { ok: true, received: st.received };
}

/** 收尾：此时才拼成完整 base64 并塞进 input[type=file]。 */
async function uploadFinish(params) {
  const key = _uploadKey(params.upload_id);
  const st = _uploads.get(key);
  if (!st) throw new Error("上传会话不存在或已过期（请重新发起上传）");
  _uploads.delete(key);

  // 拼接前去掉各块末尾的 padding（块长已是 3 的倍数，正常不会有）
  const fileBase64 = st.parts.map((x) => x.replace(/=+$/, "")).join("");
  st.parts = [];   // 尽快释放

  const tab = await resolveTab(st.tabId);
  assertInjectable(tab);
  const res = await callContent(tab, "upload_blob", {
    selector: st.selector,
    name: st.name,
    mime: st.mime,
    base64: fileBase64,
  }, 60000);

  // ⚠️ 字段要与无头后端的 upload_file 返回**对齐**（契约检查 F1 会核对）：
  //    path / name / size / url 一个都不能少。
  //    扩展侧拿不到本地路径，这里回传插件声明的路径供上层显示。
  return { ok: true, url: tab.url, name: st.name,
           path: st.path || st.name, size: st.received,
           matched: res.matched };
}

/** 放弃这次上传（插件侧发块失败时会调）。 */
async function uploadAbort(params) {
  _uploads.delete(_uploadKey(params.upload_id));
  return { ok: true };
}

// ─── 3. 下载（用用户会话抓取，分块回传）────────────────────────────────

const MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024;

async function downloadViaSession(params, cmdId) {
  const { url, max_bytes } = params;
  if (!url) throw new Error("缺少 url");

  const limit = Number(max_bytes) > 0 ? Number(max_bytes) : MAX_DOWNLOAD_BYTES;

  // 在扩展自己的上下文里 fetch —— 会带上浏览器已存的 Cookie（同源）
  // ⚠️ 凭据只能走 HTTPS。
  //    明文 HTTP 或在重定向里跳到 HTTP，都会把非 Secure 的 Cookie
  //    暴露在网络上（CWE-319）。这里直接用 redirect:"error" ——
  //    与其"跟随后再检查"（已经发出去了），不如根本不让它跳。
  const isHttps = url.toLowerCase().startsWith("https://");
  const resp = await fetch(url, {
    credentials: isHttps ? "include" : "omit",
    redirect: "error",           // 不跟随重定向，杜绝跨协议泄漏
  });
  if (!resp.ok) throw new Error(`下载失败，HTTP ${resp.status}`);

  const declared = Number(resp.headers.get("Content-Length") || 0);
  if (declared && declared > limit) {
    throw new Error(`文件过大（${declared} 字节 > 上限 ${limit}），已拒绝`);
  }

  const mime = resp.headers.get("Content-Type") || "application/octet-stream";
  const reader = resp.body.getReader();
  const CHUNK = 256 * 1024;          // 每块 256KB
  let total = 0;
  let buf = new Uint8Array(0);

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // 把新到的拼到残留缓冲后面
    const merged = new Uint8Array(buf.length + value.length);
    merged.set(buf, 0);
    merged.set(value, buf.length);
    buf = merged;

    while (buf.length >= CHUNK) {
      const slice = buf.slice(0, CHUNK);
      buf = buf.slice(CHUNK);
      total += slice.length;
      if (total > limit) {
        try { await reader.cancel(); } catch (_) {}
        throw new Error(`文件超过上限 ${limit} 字节，已中止`);
      }
      sendChunk(cmdId, slice);
    }
  }
  if (buf.length) {
    total += buf.length;
    // ⚠️ 最后这一块同样要在**回传前**查上限 ——
    //    否则一个刚好卡在边界外的文件会绕过限制被完整传出。
    if (limit > 0 && total > limit) {
      throw new Error(`文件超过上限 ${limit} 字节，已中止`);
    }
    sendChunk(cmdId, buf);
  }

  return { ok: true, url, mime, bytes: total };
}

// sendChunk 由 shared.js 提供（这里不能再声明一次：
// 与 import 的同名绑定冲突会让整个模块语法错误）

// ─── 4. Cookie 导出 / 导入（打通两个后端的登录态）──────────────────────

async function cookieGet(params) {
  const tab = await resolveTab(params.tab_id);
  const url = params.url || tab.url;
  if (!url || !/^https?:/i.test(url)) {
    throw new Error("只能导出 http/https 页面的 cookie");
  }
  const cookies = await chrome.cookies.getAll({ url });
  return {
    url,
    cookies: cookies.map((c) => ({
      name: c.name, value: c.value, domain: c.domain, path: c.path,
      secure: c.secure, httpOnly: c.httpOnly, sameSite: c.sameSite,
      expirationDate: c.expirationDate,
    })),
  };
}

async function cookieSet(params) {
  const list = params.cookies || [];
  let ok = 0, failed = 0;
  for (const c of list) {
    if (!c || !c.name || !c.domain) { failed++; continue; }
    const host = String(c.domain).replace(/^\./, "");
    const scheme = c.secure ? "https" : "http";
    const details = {
      url: `${scheme}://${host}${c.path || "/"}`,
      name: c.name,
      value: c.value == null ? "" : String(c.value),
      domain: c.domain,
      path: c.path || "/",
      secure: !!c.secure,
      httpOnly: !!c.httpOnly,
    };
    const ss = String(c.sameSite || "").toLowerCase();
    if (ss === "strict") details.sameSite = "strict";
    else if (ss === "lax") details.sameSite = "lax";
    else if (ss === "none" || ss === "no_restriction") details.sameSite = "no_restriction";
    if (c.expirationDate) details.expirationDate = Number(c.expirationDate);
    try {
      await chrome.cookies.set(details);
      ok++;
    } catch (_) {
      failed++;
    }
  }
  // 字段名与无头后端保持一致：written/skipped/failed/total
  // （否则同一个工具因为路由到不同后端而给出不同形状的结果）
  return { ok, written: ok, skipped: 0, failed, total: list.length };
}

export { execJs, upload, uploadChunk, uploadFinish, uploadAbort,
         downloadViaSession, cookieGet, cookieSet, ensureUserScripts };
