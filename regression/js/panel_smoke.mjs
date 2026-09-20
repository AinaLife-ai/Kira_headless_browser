/**
 * 行为级验证：**侧边栏面板**（web/index.html + web/app.js）在**真 DOM** 里跑一遍。
 *
 * 为什么要它：这一轮用户报的 4 个问题，静态检查全绿、却全都真实存在 ——
 * 因为它们都是"代码之间的形状对不上"，正则扫不出来：
 *   ① 开屏动画不出现（减少动效把整层干掉 / 之前还会按时间戳跳播）
 *   ② 令牌永远"读取失败"（`$("tokBox")` 的那个 id 在 HTML 里根本不存在
 *      → `null.dataset` 抛错 → 被 catch 吞掉）
 *   ③ 保存成功之后保存条又弹回来（`finally` 里无条件 `display="flex"`）
 *   ④ 模型下拉改了没反应（监听只绑了 input/textarea；`collect()` 也漏了 select）
 *
 * 做法：用 jsdom 加载**真的 index.html**，把 fetch 换成受控桩，
 * 再 `eval` **真的 app.js**（不复制一份）。所有断言都打在真行为上。
 *
 * 输出最后一行是 JSON（供 Python 侧解析）。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));

const results = [];
const push = (name, ok, detail = "") => results.push({ name, ok, detail });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch (e) {
  push("__skip__", false, `未安装 jsdom：${e.message}`);
  console.log(JSON.stringify(results));
  process.exit(0);
}

const html = readFileSync(`${PLUGIN}/web/index.html`, "utf8");
const appjs = readFileSync(`${PLUGIN}/web/app.js`, "utf8");

const TOKEN = "e2e-token-abcdef0123456789";
const FIELD_NAME = "vlm_model";
const PICK = "p-openai:gpt-4o-mini";

// 受控的 API 桩：形状照真后端（/status、/token、/config、/models）
const calls = [];
function makeFetch(window) {
  return async (url, opts = {}) => {
    const u = String(url);
    const method = String(opts.method || "GET").toUpperCase();
    let body = null;
    try { body = opts.body ? JSON.parse(opts.body) : null; } catch (_) { body = null; }
    calls.push({ url: u, method, body });
    const json = (obj) => ({
      ok: true, status: 200, json: async () => obj,
    });
    if (u.includes("/status")) {
      return json({
        plugin_version: "2.1.78", connected: false,
        bridge: { connected: false, commands_sent: 0, commands_failed: 0 },
        router: { strategy: "auto", describe: "auto" },
        policy: { allowed_domains: [], blocked_domains: [], local_access: true },
        confirm_log: [],
      });
    }
    if (u.includes("/token")) {
      return json({ ok: true, token: TOKEN, changed: false, never_expires: true });
    }
    if (u.includes("/models")) {
      return json({ models: [
        { id: "p-openai:gpt-4o", name: "gpt-4o (OpenAI 官方)" },
        { id: PICK, name: "gpt-4o-mini (OpenAI 官方)" },
      ] });
    }
    if (u.includes("/config")) {
      if (method === "POST") {
        return json({ ok: true, values: (body && body.values) || {}, overrides: [] });
      }
      return json({
        ok: true,
        // ⚠️ 故意**不给** command_timeout 的值：真后端一定会回退成 schema 默认值，
        //    这里模拟"后端没给"，看前端是不是至少用占位符把默认值亮出来。
        values: { backend_strategy: "auto", vlm_model: "", read_only: false },
        fields: {
          backend_strategy: { type: "string", name: "Backend", default: "auto",
                              options: ["auto", "extension", "headless"] },
          browser_channel: { type: "string", name: "Channel", default: "bundled",
                             options: ["auto", "bundled"] },
          command_timeout: { type: "float", name: "Command timeout", default: 20 },
          vlm_model: { type: "model_select", name: "VLM model", default: "" },
          read_only: { type: "switch", name: "Read only", default: false },
          blocked_domains: { type: "list", name: "Blocked", default: ["*.bank*"] },
        },
      });
    }
    return json({ ok: true });
  };
}

let dom, window, doc;
try {
  dom = new JSDOM(html, {
    url: "http://127.0.0.1:5275/page/plugin/headless_browser/panel",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  window = dom.window;
  doc = window.document;
  window.fetch = makeFetch(window);
  // app.js 是普通脚本（非 module）：直接在 window 作用域里跑
  window.eval(appjs);
} catch (e) {
  push("面板能在真 DOM 里起来（jsdom + app.js）", false,
       `${e.name}: ${e.message}`);
  console.log(JSON.stringify(results));
  process.exit(0);
}

const $ = (id) => doc.getElementById(id);
const has = (el, cls) => !!(el && el.classList && el.classList.contains(cls));

// ── ① 开屏动画：默认可见，而且**不靠 CSS 动画也会被收掉** ────────────
await sleep(150);
const bootEarly = $("boot");
push("载入动画默认可见（不是被减少动效整块干掉）",
     !!bootEarly && !bootEarly.hidden
       && window.getComputedStyle(bootEarly).display !== "none",
     bootEarly ? `hidden=${bootEarly.hidden}` : "找不到 #boot");

// jsdom 不跑 CSS 动画 → 永远不会触发 animationend：
// 正好用来验证"JS 兜底"这一条（没有它，这层会永远盖住面板）
await sleep(5200);
const bootLate = $("boot");
push("载入动画会被兜底收掉（不依赖 CSS 动画跑起来）",
     !!bootLate && bootLate.hidden === true,
     bootLate ? `hidden=${bootLate.hidden}` : "找不到 #boot");

// ── ② 令牌：要把**真值**拿出来显示，不能是"读取失败" ────────────────
await sleep(200);
const tokText = ($("tok") || {}).textContent || "";
const tokBox = $("tokBox");
push("令牌显示真值（不是'读取失败'）",
     !!tokBox && tokBox.dataset.token === TOKEN
       && tokText !== "读取失败" && !/失败/.test(tokText),
     `dataset=${tokBox ? String(tokBox.dataset.token).slice(0, 12) : "无 #tokBox"}…`
     + ` text=${JSON.stringify(tokText.slice(0, 24))}`);
push("令牌默认是遮住的（不是明文摊在屏幕上）",
     tokText.length > 0 && !tokText.includes(TOKEN),
     `text=${JSON.stringify(tokText.slice(0, 24))}`);

// ── ③ 模型下拉：改一下要**点亮保存条**并计数 ────────────────────────
await sleep(300);
const sel = doc.querySelector(`#config select[data-k="${FIELD_NAME}"]`);
if (!sel) {
  push("模型字段渲染成下拉（拿到了模型列表）", false, "找不到 select[data-k=vlm_model]");
} else {
  push("模型字段渲染成下拉（拿到了模型列表）", true,
       `选项=${sel.options.length}`);
  sel.value = PICK;
  sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(60);
  const bar = $("saveBar");
  const lab = $("saveBarLabel");
  push("改模型下拉会点亮保存条（含计数）",
       !!bar && bar.style.display === "flex"
         && !!lab && /1 项/.test(lab.textContent),
       `bar=${bar && bar.style.display} label=${lab && lab.textContent}`);
  push("改动的字段被标出来（用户看得见改了哪一项）",
       has(sel.closest(".field"), "changed"),
       `class=${sel.closest(".field") && sel.closest(".field").className}`);
}

// 对照组：文本框也要照旧能点亮（不能只顾下拉）
// ⚠️ 顺序要紧：先跑这条对照组，再**清掉标记**，最后才改模型下拉 ——
//    这样保存时就只剩"模型"一项是改动过的，"只提交改动项"那条断言才有意义。
const txt = doc.querySelector('#config input[type="text"], #config textarea');
if (txt) {
  txt.value = "changed-by-probe";
  txt.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(40);
  push("文本框改动同样点亮保存条（对照组）",
       $("saveBar").style.display === "flex" && has(txt.closest(".field"), "changed"),
       `bar=${$("saveBar").style.display}`);
}
// 清掉所有"已改动"标记，回到干净状态
doc.getElementById("config").querySelectorAll(".field.changed")
  .forEach((f) => f.classList.remove("changed"));
$("saveBar").style.display = "none";
// 再把"改过模型"这件事重新标上 —— 保存时应当只提交它一项
if (sel) {
  sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(60);
}

// ── ③b 枚举字段必须是**下拉**（用户报：后端策略等被当成了填写框）──────
const bsel = doc.querySelector('#config select[data-k="backend_strategy"]');
push("枚举字段渲染成下拉（不是让用户手打）",
     !!bsel && bsel.tagName === "SELECT" && bsel.options.length === 3,
     bsel ? `选项=${[...bsel.options].map((o) => o.value).join("/")}` : "找不到 select");
push("下拉里标出了默认项",
     !!bsel && [...bsel.options].some((o) => o.value === "auto" && /默认/.test(o.textContent)),
     bsel ? [...bsel.options].map((o) => o.textContent).join(" | ") : "");

// ── ③c 数值字段：框里要有值，或者至少把默认值亮出来 ─────────────────
const num = doc.querySelector('#config input[data-k="command_timeout"]');
push("数值字段是数字输入框（float 也认）",
     !!num && num.type === "number",
     num ? `type=${num.type}` : "找不到该输入框");
push("数值字段空着时要把默认值显示出来",
     !!num && (num.value === "20" || /默认/.test(num.placeholder || "")),
     num ? `value=${JSON.stringify(num.value)} placeholder=${JSON.stringify(num.placeholder)}` : "");

// ── ③d 眼睛：点一下要能看到明文，再点遮回去 ─────────────────────────
const tokEl = $("tok");
const eyeEl = $("eye");
const before = tokEl.textContent;
if (eyeEl) eyeEl.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await sleep(60);
const shown = tokEl.textContent;
if (eyeEl) eyeEl.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await sleep(60);
push("点眼睛能看到明文令牌（再点遮回去）",
     !!eyeEl && before !== shown && shown.includes(TOKEN)
       && tokEl.textContent !== shown && !tokEl.textContent.includes(TOKEN),
     `前=${JSON.stringify(before.slice(0, 10))} 点后=${JSON.stringify(shown.slice(0, 10))}`);

// ── ④ 保存：下拉的值要真的提交；保存条要收起；提示条要自己消失 ──────
$("save").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await sleep(400);
const post = calls.filter((c) => c.method === "POST"
  && c.url.includes("/config")).pop();
const sent = (post && post.body && post.body.values) || {};
push("保存时把下拉的值一起提交",
     sent[FIELD_NAME] === PICK,
     `${FIELD_NAME}=${JSON.stringify(sent[FIELD_NAME])}`);
// ⚠️ 只该提交**用户改过**的那一项：把没动过的也存下去，等于把"当前默认值"
//    钉成覆盖值，以后插件改默认值这些用户就再也跟不上了 ✗
push("只提交改动过的字段（不把默认值一起钉死）",
     Object.keys(sent).length === 1 && sent[FIELD_NAME] === PICK,
     `提交了 ${JSON.stringify(sent)}`);
push("保存成功后保存条立刻收起",
     $("saveBar").style.display === "none",
     `display=${$("saveBar").style.display}`);
push("保存成功有提示（toast 亮起）",
     has($("toast"), "show") && /已保存/.test($("toast").textContent),
     `class=${$("toast").className} text=${JSON.stringify($("toast").textContent)}`);

await sleep(2600);
push("提示条会自己消失（不赖在屏幕上）",
     !has($("toast"), "show"),
     `class=${$("toast").className}`);

// ── ⑤ 自检那一行必须存在（出问题时用户截图就够）────────────────────
push("面板自检行可用（能显示载入动画 / 减少动效 / 最近错误）",
     !!$("diag") && /面板自检/.test($("diag").textContent || ""),
     JSON.stringify(($("diag") || {}).textContent || "").slice(0, 80));

try { window.close(); } catch (_) { /* 忽略 */ }
console.log(JSON.stringify(results));
process.exit(0);
