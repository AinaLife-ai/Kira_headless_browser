/**
 * 多连接下的**响应路由** —— 这是这次重构最要命的地方。
 *
 * 为什么必须单独测：扩展现在同时连多个 KiraAI 实例，而命令是 **async** 的。
 * 如果发送时读一个"当前连接"全局变量，那么
 *   A（实例1）在 await 期间 → B（实例2）的命令把它改掉
 *   → A 恢复后 sendResult 就把**自己的响应发给了 B**
 * 现象是"跟 A 说话 A 没反应，B 却收到一堆不属于它的结果" ——
 * 只在两个实例同时忙时出现，最难查。
 *
 * 所以这里**并发交错**地发两条命令，验证各自的响应确实回到了各自的连接。
 */
// ESM 的静态 import 只接受字面量路径 → 用动态 import（顶层 await 在 ESM 里可用）
const { links, sendResult, sendEvent, sendRaw } =
  await import(process.env.KIRA_PLUGIN_DIR + "/browser-bridge/shared.js");

const sent = new Map();          // linkKey -> [msg, ...]
function fakeLink(key) {
  const box = [];
  sent.set(key, box);
  return {
    key,
    open: true,
    ws: { readyState: 1, send: (s) => box.push(JSON.parse(s)) },
  };
}

const results = [];
const push = (name, ok, detail) => results.push({ name, ok, detail });

const A = fakeLink("kira-a:5267");
const B = fakeLink("kira-b:8080");
links.clear();
links.set("kira-a:5267", A);
links.set("kira-b:8080", B);

// ① 并发交错：A 先发、await 期间 B 发、然后 A 才真正 write
await Promise.all([
  (async () => {
    await new Promise((r) => setTimeout(r, 20));      // A 在 await
    sendResult("id-A", true, { who: "A" }, null, null, A);
  })(),
  (async () => {
    sendResult("id-B", true, { who: "B" }, null, null, B);
  })(),
]);

const aMsgs = sent.get("kira-a:5267");
const bMsgs = sent.get("kira-b:8080");
push("A 的响应发给了 A（没串到 B）",
     aMsgs.length === 1 && aMsgs[0].id === "id-A" && aMsgs[0].data.who === "A",
     JSON.stringify(aMsgs));
push("B 的响应发给了 B",
     bMsgs.length === 1 && bMsgs[0].id === "id-B" && bMsgs[0].data.who === "B",
     JSON.stringify(bMsgs));

// ② 浏览器事件要**广播**给所有实例（否则另一个实例的"当前页"会和真实脱节）
sent.forEach((v) => (v.length = 0));
sendEvent("tab_activated", { url: "https://x.test/" });
push("事件广播到 A", sent.get("kira-a:5267").length === 1,
     JSON.stringify(sent.get("kira-a:5267")));
push("事件广播到 B", sent.get("kira-b:8080").length === 1,
     JSON.stringify(sent.get("kira-b:8080")));

// ③ 指定 link 时只发那一条
sent.forEach((v) => (v.length = 0));
sendEvent("only_a", {}, A);
push("指定连接时只发那一条",
     sent.get("kira-a:5267").length === 1 && sent.get("kira-b:8080").length === 0,
     `A=${sent.get("kira-a:5267").length} B=${sent.get("kira-b:8080").length}`);

// ④ 连接已关时返回 false（调用方要靠它判断"其实没发出去"）
B.open = false;
push("连接关闭时 sendRaw 返回 false", sendRaw({ x: 1 }, B) === false, "");
B.open = true;

console.log(JSON.stringify(results));
