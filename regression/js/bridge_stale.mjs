/**
 * 半开连接的**自愈**逻辑（扩展侧）。
 *
 * 为什么要有它：MV3 的 Service Worker 被回收再唤醒之后，WebSocket 可能变成
 * 半开连接 —— `readyState` 还是 `OPEN`，但服务端的数据永远到不了。
 * 只看 readyState 的话扩展会一直以为"已连接"，而每个命令都卡到超时；
 * 反过来，如果让**服务端**去判死，日志里就会出现那种"判定连接已失效"
 * 的告警刷屏（其实对面 1 秒后就回来了）。
 *
 * 所以：判断逻辑放在扩展侧、写成纯函数（`isLinkStale`），
 * 让它可被测试；接线（什么时候记时间、什么时候主动换连接）在 background.js。
 *
 * 输出最后一行是 JSON（供 Python 侧解析）。
 */
const PLUGIN = process.env.KIRA_PLUGIN_DIR || "..";
const results = [];
const push = (name, ok, detail = "") => results.push({ name, ok, detail });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let S;
try {
  S = await import(`${PLUGIN}/browser-bridge/shared.js`);
} catch (e) {
  push("__skip__", false, `导入 shared.js 失败：${e.message}`);
  console.log(JSON.stringify(results));
  process.exit(0);
}

const { isLinkStale, WS_STALE_MS, staleLogDecision, STALE_LOG_WINDOW_MS } = S;
push("shared.js 导出 isLinkStale / WS_STALE_MS",
     typeof isLinkStale === "function" && typeof WS_STALE_MS === "number",
     `isLinkStale=${typeof isLinkStale} WS_STALE_MS=${WS_STALE_MS}`);

if (typeof isLinkStale === "function") {
  const now = 1_000_000;
  push("刚建连（还没有任何一帧）不算陈旧",
       isLinkStale(0, now) === false, `lastInboundAt=0 → ${isLinkStale(0, now)}`);
  push("刚收到过数据不算陈旧",
       isLinkStale(now - 1000, now) === false);
  push("静默超过阈值判为陈旧（半开连接）",
       isLinkStale(now - (WS_STALE_MS + 1000), now) === true,
       `静默 ${WS_STALE_MS + 1000}ms`);
  push("阈值必须小于服务端判死窗口（否则服务端先开口，日志又会刷屏）",
       WS_STALE_MS < 75_000,
       `WS_STALE_MS=${WS_STALE_MS}ms，服务端空闲上限 75s`);
}

// ── "换一条新连接"这条日志**本身也不许刷屏** ─────────────────────────
//  链路真半开时这个动作每分钟都会发生一次；一条条打出来就是换了个地方刷屏。
if (typeof staleLogDecision === "function") {
  const box = { count: 0, logAt: 0, suppressed: 0 };
  const a = staleLogDecision(box, 1_000_000);
  const b = staleLogDecision(box, 1_000_001);
  const c = staleLogDecision(box, 1_000_002);
  const d = staleLogDecision(box, 1_000_000 + STALE_LOG_WINDOW_MS + 1);
  push("换连接的日志有降噪：同一窗口只喊一次（计数照记）",
       a === true && b === false && c === false && d === true
         && box.count === 4,
       `第1次=${a} 第2次=${b} 第3次=${c} 窗口后=${d}；累计=${box.count}`);
  push("降噪窗口足够长（不至于每分钟又喊一次）",
       STALE_LOG_WINDOW_MS >= 60_000,
       `窗口=${STALE_LOG_WINDOW_MS}ms`);
} else {
  push("shared.js 导出 staleLogDecision（换连接日志的降噪）", false,
       "没有这个函数 → 半开链路会每分钟打一条日志");
}

// ── 接线：background.js 里那三件事必须真的做了 ────────────────────────
import { readFileSync } from "node:fs";
const bg = readFileSync(`${PLUGIN}/browser-bridge/background.js`, "utf8");
const popup = readFileSync(`${PLUGIN}/browser-bridge/popup.js`, "utf8");
const proto = readFileSync(`${PLUGIN}/browser-bridge/protocol.js`, "utf8");
push("建连时记下时间（lastInboundAt）",
     /ws\.onopen[\s\S]{0,400}?this\.lastInboundAt = Date\.now\(\)/.test(bg));
push("每收到一帧都刷新 lastInboundAt",
     /ws\.onmessage[\s\S]{0,300}?this\.lastInboundAt = Date\.now\(\)/.test(bg));
push("保活时用 isLinkStale 判断并主动换连接",
     /isLinkStale\(/.test(bg));
push("保活时发一条 ping 证明自己还活着（服务端就不会误判空闲）",
     /sendRaw\(\{\s*type:\s*MSG\.PING/.test(bg));
push("换连接走降噪入口（不是每次都 console.log）",
     /staleLogDecision\(staleStat\)/.test(bg)
       && /if \(shout\)[\s\S]{0,200}?console\.log/.test(bg),
     "必须是 `if (shout) console.log(...) else console.debug(...)`");
push("自愈次数落盘（弹窗要能显示数字，不受 SW 回收影响）",
     /STALE_RECONNECTS/.test(proto) && /STALE_RECONNECTS/.test(bg));
push("弹窗里能看到这个次数（用户不用翻控制台）",
     /setStaleHint\(/.test(popup) && /staleReconnects/.test(bg));

console.log(JSON.stringify(results));
process.exit(0);
