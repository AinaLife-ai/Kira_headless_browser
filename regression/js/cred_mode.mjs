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
// ⚠️ 截取范围要卡在**下一个顶层函数**处，不能用固定的 4000 字符：
//    这个函数有 3634 字符，固定窗口会**切进后面那个函数**，
//    于是后一个函数里的声明可能被当成前一个函数的 —— 判据就会给出错的结果
//    （而且函数变长以后还会继续漂）。
let seg = "";
if (dlIdx >= 0) {
  seg = cap.slice(dlIdx);
  const nxt = seg.search(/\nasync function |\nfunction /);
  if (nxt > 0) seg = seg.slice(0, nxt);
}

if (!seg) {
  results.push({ name: "可定位 downloadViaSession", ok: false,
                 detail: "找不到该函数" });
} else {
  // ① 抽出 isHttps 的**真实定义行**（生产代码那一行）
  const mHttps = seg.match(/const\s+isHttps\s*=\s*([^;]+);/);
  // ⚠️ 生产代码现在**逐跳**重新判断协议（手动跟随重定向），
  //    所以 credentials 用的是 `hopIsHttps`。两处都要能识别：
  //    判据的本质是"凭据由**该跳的 URL**推导"，名字随实现变。
  const mHop = seg.match(/const\s+hopIsHttps\s*=\s*([^;]+);/);
  // ② 抽出 credentials 表达式
  const mCred = seg.match(/credentials\s*:\s*([^,\n]+)/);
  // ③ 用于替换的变量名：生产用哪个，就注入哪个
  const credVar = mCred && mCred[1].includes("hopIsHttps") ? "hopIsHttps"
                : mCred && mCred[1].includes("isHttps") ? "isHttps" : "";

  if (!mHttps || !mCred) {
    results.push({ name: "可提取 isHttps 与 credentials", ok: false,
                   detail: `isHttps=${!!mHttps} credentials=${!!mCred}` });
  } else {
    const httpsExpr = mHttps[1].trim();
    const credExpr = mCred[1].trim();

    results.push({
      name: "凭据表达式引用了由 URL 推导出的 isHttps",
      // ⚠️ 认 `hopIsHttps`（逐跳版）与 `isHttps` 两种 —— 判据的本质是
      //    "凭据条件引用了**由 URL 推出来的**那个布尔"，名字随实现变。
      ok: credExpr.includes("hopIsHttps") || credExpr.includes("isHttps"),
      detail: `isHttps = ${httpsExpr};`
              + (mHop ? ` hopIsHttps = ${mHop[1]};` : "")
              + ` credentials: ${credExpr}`,
    });

    // ③ 用真实 URL 跑一遍**生产的推导 + 凭据选择**
    const decide = (url) => {
      try {
        // ⚠️ 注入的变量名要跟生产一致（逐跳版注入 hopIsHttps）；
        //    逐跳版的推导表达式里引用的是 currentUrl，这里用 url 代替它
        //    （单跳场景下 currentUrl === url，语义一致）。
        // ⚠️ 选哪个表达式要**看 credentials 引用的是谁**：
        //    `credentials` 里写 `hopIsHttps` 才该用逐跳表达式；
        //    写的是 `isHttps`（初始请求那套）就必须用 httpsExpr。
        //    原来写成 `mHop ? mHop[1] : httpsExpr` —— 两个声明都在时
        //    永远走前者，于是**重定向表达式正确就能掩盖初始请求表达式坏掉**，
        //    真正的回归从检查里溜走。
        const protocolExpr =
          credVar === "hopIsHttps" && mHop ? mHop[1] : httpsExpr;
        const _expr = protocolExpr.replace(/currentUrl/g, "url");
        // eslint-disable-next-line no-new-func
        const f = new Function("url",
          `const ${credVar || "isHttps"} = ${_expr};\nreturn (${credExpr});`);
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
