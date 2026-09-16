/**
 * 行为级验证：downloadViaSession 的凭据模式在 HTTP / HTTPS 下分别是什么。
 *
 * ⚠️ 关键点：要**真的从 URL 推导 isHttps**，而不是把 isHttps 直接注入进去。
 *    上一版把 isHttps 作为变量喂进表达式 —— 那样只验证了"三元表达式本身"，
 *    而**生产里 isHttps 是怎么算出来的完全没被覆盖**。
 *    比如把 `url.startsWith("https://")` 写成永远为真，注入式验证照样通过，
 *    但所有 HTTP 下载都会带上会话 Cookie。
 *
 * 做法：把 capabilities.js 里 downloadViaSession 开头那几行
 * （`const isHttps = ...` 以及它依赖的变量）连同 credentials 表达式一起
 * 抽出来，用**真实 URL** 驱动执行。
 *
 * 输出最后一行是 JSON（供 Python 侧解析）。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || fileURLToPath(new URL("../..", import.meta.url));
const cap = readFileSync(`${PLUGIN}/browser-bridge/capabilities.js`, "utf8");

const results = [];

const dlIdx = cap.indexOf("async function downloadViaSession(");
const seg = dlIdx >= 0 ? cap.slice(dlIdx, dlIdx + 4000) : "";

if (!seg) {
  results.push({ name: "可定位 downloadViaSession", ok: false,
                 detail: "找不到该函数" });
} else {
  // ① 抽出 isHttps 的**真实定义行**（生产代码那一行）
  const mHttps = seg.match(/const\s+isHttps\s*=\s*([^;]+);/);
  // ② 抽出 credentials 表达式
  const mCred = seg.match(/credentials\s*:\s*([^,\n]+)/);

  if (!mHttps || !mCred) {
    results.push({ name: "可提取 isHttps 与 credentials", ok: false,
                   detail: `isHttps=${!!mHttps} credentials=${!!mCred}` });
  } else {
    const httpsExpr = mHttps[1].trim();
    const credExpr = mCred[1].trim();

    results.push({
      name: "凭据表达式引用了由 URL 推导出的 isHttps",
      ok: credExpr.includes("isHttps"),
      detail: `isHttps = ${httpsExpr}; credentials: ${credExpr}`,
    });

    // ③ 用真实 URL 跑一遍**生产的推导 + 凭据选择**
    const decide = (url) => {
      try {
        // eslint-disable-next-line no-new-func
        const f = new Function("url",
          `const isHttps = ${httpsExpr};\nreturn (${credExpr});`);
        return f(url);
      } catch (e) {
        return "__ERR__" + e.message;
      }
    };

    const cases = [
      ["https://example.com/f", "include", "HTTPS 下载带凭据"],
      ["http://example.com/f", "omit", "HTTP 下载不带凭据"],
      ["HTTPS://EXAMPLE.COM/f", "include", "协议大小写不敏感（HTTPS）"],
      ["http://127.0.0.1:8080/f", "omit", "本机 HTTP 也不带凭据"],
    ];
    for (const [u, want, label] of cases) {
      const got = decide(u);
      results.push({
        name: label,
        ok: got === want,
        detail: `${u} -> ${JSON.stringify(got)}（期望 ${want}）`,
      });
    }
  }
}

console.log(JSON.stringify(results));
// 退出码只表示"脚本跑完了"；逐项判定交给 Python 侧
process.exit(0);
