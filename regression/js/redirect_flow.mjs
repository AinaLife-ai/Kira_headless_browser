/**
 * 行为级验证：下载的**重定向跟随**与**最终协议守卫**。
 *
 * ⚠️ 先说清楚这个测试**能**与**不能**验证什么：
 *
 *   **能**：
 *     · 我们的选项（`redirect: "follow"`）在**真实的重定向端点**上确实能跟到底
 *       —— 用本地起的 HTTP 服务器跑一条 302 链，断言拿到最终正文；
 *     · "起 HTTPS 却落在 HTTP 就中止"这条**纯逻辑**守卫的判断正确。
 *
 *   **不能**（沙箱限制，必须真机）：
 *     · 浏览器在**每一跳**如何选择 Cookie —— 那是**浏览器保证**的行为
 *       （Secure cookie 绝不上 HTTP、域不匹配的不发），不是我们实现的代码。
 *     · 尤其是 `redirect:"manual"` 在浏览器里返回 **opaqueredirect**
 *       （status=0、响应头全空、**读不到 Location**）——
 *       而 **Node 的 undici 不实现这层过滤**：它照样返回 302 与 Location。
 *       所以 **Node 测不出"manual 在浏览器里不可用"**，
 *       这正是上一版踩的坑（自己跟重定向 → 读不到下一跳 → 必然失败）。
 *       这条只能靠 B2.5 / C8c 的**静态守卫**拦。
 *
 * 输出最后一行是 JSON（供 Python 侧解析）。
 */
import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const cap = readFileSync(`${PLUGIN}/browser-bridge/capabilities.js`, "utf8");

const results = [];
const push = (name, ok, detail = "") => results.push({ name, ok, detail });

// ── ① 起一个真实的重定向服务器 ───────────────────────────────────────
const hits = [];
const srv = http.createServer((req, res) => {
  hits.push(req.url);
  if (req.url === "/start") {
    res.writeHead(302, { Location: "/mid" });
    res.end();
  } else if (req.url === "/mid") {
    res.writeHead(302, { Location: "/final" });
    res.end();
  } else {
    res.writeHead(200, { "Content-Type": "application/octet-stream" });
    res.end("DOWNLOAD-BODY");
  }
});
await new Promise((r) => srv.listen(0, "127.0.0.1", r));
const port = srv.address().port;
const startUrl = `http://127.0.0.1:${port}/start`;

// ── ② 从**生产代码**里抽出它用的 redirect 模式 ────────────────────────
const dlIdx = cap.indexOf("async function downloadViaSession(");
// ⚠️ 和 cred_mode.mjs 一样：截取范围卡在**下一个顶层函数**，不用固定字符数。
//    固定窗口会切进后面那个函数，后者的声明可能被当成本函数的；
//    而且函数一变长就会继续漂。
let dlSeg = "";
if (dlIdx >= 0) {
  dlSeg = cap.slice(dlIdx);
  const _nxt = dlSeg.search(/\nasync function |\nfunction /);
  if (_nxt > 0) dlSeg = dlSeg.slice(0, _nxt);
}
const mRedirect = dlSeg.match(/redirect:\s*"([a-z]+)"/);

if (!mRedirect) {
  push("可提取生产代码的 redirect 模式", false, "找不到 redirect: \"...\"");
} else {
  const mode = mRedirect[1];
  push("生产代码用 follow（浏览器跟随）", mode === "follow",
       `实际 redirect: "${mode}"`);

  // ── ③ 用**真实的** fetch 跑那条 302 链 ────────────────────────────
  try {
    const resp = await fetch(startUrl, { redirect: mode });
    const body = await resp.text();
    const okChain = resp.status === 200 && body === "DOWNLOAD-BODY";
    const okFinal = String(resp.url || "").endsWith("/final");
    push("真实 302 链能跟到底（拿到最终正文）", okChain,
         `status=${resp.status} body=${body.slice(0, 16)}`);
    push("跟随后的 resp.url 指向最终地址", okFinal, `url=${resp.url}`);
    push("确实经过了两次重定向", hits.filter((h) => h === "/mid").length === 1,
         `服务器收到=${JSON.stringify(hits)}`);
  } catch (e) {
    push("真实 302 链能跟到底（拿到最终正文）", false, e.message);
  }
}

// ── ④ 最终协议守卫：**纯逻辑**（与生产代码同形）─────────────────────
//  生产里那段是：
//    const finalUrl = resp.url || url;
//    const finalIsHttps = finalUrl.toLowerCase().startsWith("https://");
//    if (isHttps && !finalIsHttps) throw ...
//  这里把**同一条判断**抽出来，喂不同组合验证语义。
const hasGuard = /finalIsHttps/.test(dlSeg) && /resp\.url/.test(dlSeg);
push("生产代码里有「最终 URL 协议」守卫", hasGuard,
     hasGuard ? "" : "没找到 finalIsHttps / resp.url");

const guardRejects = (startHttps, finalUrl) => {
  const isHttps = startHttps;
  const finalIsHttps = String(finalUrl).toLowerCase().startsWith("https://");
  return isHttps && !finalIsHttps;
};
push("起 HTTPS 落在 HTTP → 拒绝",
     guardRejects(true, "http://evil.example/x") === true);
push("起 HTTPS 仍落在 HTTPS → 放行",
     guardRejects(true, "https://cdn.example/x") === false);
push("起 HTTP（本来就没凭据）→ 不看这条守卫",
     guardRejects(false, "http://cdn.example/x") === false);

srv.close();
console.log(JSON.stringify(results));
