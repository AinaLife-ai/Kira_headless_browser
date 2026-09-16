/**
 * 行为级验证：downloadViaSession 的凭据模式在 HTTP / HTTPS 下分别是什么。
 *
 * ⚠️ 只做语法检查（"表达式里有 include 和 omit 两个分支"）是不够的 ——
 *    把条件写反（HTTPS→omit、HTTP→include）或者条件恒为真，语法检查照样过，
 *    但**会话 Cookie 会在明文 HTTP 上被发出去**。
 *    这里真的把那段表达式跑一遍，两种协议各验一次。
 *
 * 输出最后一行是 JSON（供 Python 侧解析）。
 */
import { readFileSync } from "node:fs";

const PLUGIN = process.env.KIRA_PLUGIN_DIR
  || new URL("../..", import.meta.url).pathname;
const cap = readFileSync(`${PLUGIN}/browser-bridge/capabilities.js`, "utf8");

const results = [];

// 抽出 downloadViaSession 内计算 credentials 的那一行
const dlIdx = cap.indexOf("async function downloadViaSession(");
const seg = dlIdx >= 0 ? cap.slice(dlIdx, dlIdx + 3000) : "";
const m = seg.match(/credentials\s*:\s*([^,\n]+)/);

if (!m) {
  results.push({ name: "凭据表达式可提取", ok: false, detail: "找不到 credentials: ..." });
} else {
  const expr = m[1].trim();
  // 找出表达式里用到的 isHttps / is_https 之类的变量名
  // 这里按两种协议各跑一次
  const runFor = (httpsMode) => {
    try {
      // 表达式里通常写的是 isHttps。构造一个作用域把它喂进去。
      const vars = {};
      for (const nm of ["isHttps", "is_https", "https", "isSecure", "secure"]) {
        vars[nm] = httpsMode;
      }
      // eslint-disable-next-line no-new-func
      const f = new Function(...Object.keys(vars), `return (${expr});`);
      return f(...Object.values(vars));
    } catch (e) {
      return "__ERR__" + e.message;
    }
  };
  const http = runFor(false);
  const https = runFor(true);
  results.push({
    name: "HTTPS 下带凭据（include）",
    ok: https === "include",
    detail: `expr=${expr} -> ${JSON.stringify(https)}`,
  });
  results.push({
    name: "HTTP 下不带凭据（omit）",
    ok: http === "omit",
    detail: `expr=${expr} -> ${JSON.stringify(http)}`,
  });
}

console.log(JSON.stringify(results));
process.exit(0);
