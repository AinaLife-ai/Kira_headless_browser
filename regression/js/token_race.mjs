/**
 * 行为级验证：`loadToken` 的**请求序号**守卫（web/index.html）。
 *
 * ⚠️ 要防的竞态：
 *    定期轮询 `loadToken(false)` 与用户点「重新生成」的 `loadToken(true)`
 *    是**并发**的。`/token` 处理器可能在强制轮换**之前**就把旧令牌给了
 *    轮询那条，它再晚一点返回 —— 面板上于是又显示回一枚**已经作废**的
 *    旧令牌。没有序号守卫时，"后到的响应无条件覆盖"，这个覆盖就会发生。
 *
 * 做法：从 web/index.html 里**抽出真的 loadToken**（不复制一份），
 * 配上受控的 `api()` 桩，显式安排返回顺序。
 *
 * 输出最后一行是 JSON（供 Python 侧解析）。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const html = readFileSync(`${PLUGIN}/web/index.html`, "utf8");

const results = [];
const push = (name, ok, detail = "") => results.push({ name, ok, detail });

// ── 抽出 loadToken 源码（连同它前面的 _tokenSeq 声明）────────────────
const startMarker = "async function loadToken(";
const at = html.indexOf(startMarker);
let src = "";
if (at < 0) {
  push("可定位 loadToken", false, "index.html 里找不到该函数");
} else {
  // 函数体结束：从起点往后第一个顶格的 `}`
  const rest = html.slice(at);
  const end = rest.indexOf("\n}\n");
  src = end >= 0 ? rest.slice(0, end + 3) : "";
  // 连**声明**一起抽（`let _tokenSeq = 0;`）—— 序号状态就在那里
  const declIdx = html.lastIndexOf("let _tokenSeq", at);
  if (declIdx >= 0) {
    const declEnd = html.indexOf("\n", declIdx);
    src = html.slice(declIdx, declEnd) + "\n" + src;
  }
  if (!src) push("可定位 loadToken", false, "函数体没取到（找不到顶格 }）");
}

if (src) {
  const fields = {};
  const grab = (id) => {
    if (!fields[id]) fields[id] = { value: "", textContent: "", innerHTML: "" };
    return fields[id];
  };
  const $ = (id) => grab(id);
  const tokenFingerprint = (t) => String(t || "").slice(-4);

  // 受控的 api()：每次调用挂起，由用例显式 resolve
  const pending = [];
  const api = (_path) => new Promise((resolve) => { pending.push(resolve); });

  const build = new Function("$", "api", "tokenFingerprint",
    src + "\nreturn loadToken;");
  const loadToken = build($, api, tokenFingerprint);

  // ── 场景：轮询先发（拿到旧令牌）→ 强制的后发先回（新令牌）
  //          → 轮询的响应**最后**才到 ─────────────────────────────
  const p1 = loadToken(false);          // 轮询
  const p2 = loadToken(true);           // 用户点了「重新生成」

  // 强制的先返回：新令牌
  pending[1]({ ok: true, token: "BRANDNEW", changed: true, never_expires: true });
  await p2;
  const afterRegen = fields["token"].value;

  // 轮询的**迟到**响应：旧令牌（已经是作废的了）
  pending[0]({ ok: true, token: "STALEOLD", changed: false, never_expires: true });
  await p1;
  const afterLate = fields["token"].value;

  push("强制请求先返回时显示新令牌", afterRegen === "BRANDNEW",
       `token=${afterRegen}`);
  push("迟到的旧响应不得覆盖新令牌", afterLate === "BRANDNEW",
       `token=${afterLate}（若为 STALEOLD 说明序号守卫失效）`);
  // tokenFingerprint 取末 4 位：BRANDNEW → "DNEW"、STALEOLD → "TOLD"
  push("指纹也来自新令牌（不是迟到的那枚）",
       String(fields["fingerprint"].innerHTML).includes("DNEW")
       && !String(fields["fingerprint"].innerHTML).includes("TOLD"),
       String(fields["fingerprint"].innerHTML).slice(0, 60));

  // ── 对照：逆序（轮询先回，再回强制的）—— 后者本就该赢 ───────────
  const q1 = loadToken(false);
  const q2 = loadToken(true);
  pending[2]({ ok: true, token: "OLD2", changed: false, never_expires: true });
  await q1;
  pending[3]({ ok: true, token: "NEW2", changed: true, never_expires: true });
  await q2;
  push("正常顺序下新令牌生效", fields["token"].value === "NEW2",
       `token=${fields["token"].value}`);

  // ── 失败响应同样不许覆盖更新的成功结果 ───────────────────────────
  const r1 = loadToken(false);
  const r2 = loadToken(true);
  pending[5]({ ok: true, token: "NEW3", changed: true, never_expires: true });
  await r2;
  pending[4]({ ok: false, error: "boom" });
  await r1;
  push("迟到的失败响应不得覆盖新令牌", fields["token"].value === "NEW3",
       `token=${fields["token"].value}`);
}

console.log(JSON.stringify(results));
