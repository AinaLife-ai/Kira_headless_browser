/**
 * 配对握手（页面 → 扩展）—— 运行在 KiraAI 面板页里。
 *
 * 为什么需要它：
 *   扩展要填 服务地址 / 端口 / 令牌 三样，端口原来还写死 5267，
 *   用户改过端口就连不上。而**扩展读不了本地文件**（浏览器的安全模型），
 *   所以"去扫安装目录"这条路走不通 —— 只能靠"问"或"探"。
 *
 *   而"问"有更好的问法：**KiraAI 的面板页本来就是那个实例自己服务的**，
 *   页面里的 `location.port` 就是它的真实端口。用户在哪个实例的面板里，
 *   配置的就是哪个实例 —— **多实例也不会认错**，比扫端口可靠得多。
 *
 * 安全：
 *   · 只在回环地址（127.0.0.1 / localhost）的页面上运行（见 manifest 的
 *     `matches`），互联网上的页面注入不进来。
 *   · 这里再校验一次 `event.origin` 是不是回环 —— 防止被同页面的
 *     第三方 iframe 之类的消息冒充。
 *   · 令牌本身还要被 KiraAI 服务端校验，写错了也连不上。
 */

const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i;

window.addEventListener("message", (event) => {
  // 只收本窗口发来的（面板自己 post 的），且来源必须是回环
  if (event.source !== window) return;
  if (!LOOPBACK.test(event.origin || "")) return;

  const d = event.data;
  if (!d || d.__kiraPair !== true) return;
  if (!d.token) return;

  // 转发给扩展后台。⚠️ 用 try/catch 包住：扩展被重载时
  // `chrome.runtime` 会短暂失效，抛错会污染页面控制台。
  try {
    chrome.runtime.sendMessage({
      action: "page_pair",
      payload: {
        host: d.host || "127.0.0.1",
        // 端口以**页面实际所在的那个**为准，不信页面传上来的值 ——
        // 这样即使面板写错了，扩展拿到的仍然是它真正在看的实例。
        port: Number(location.port) || Number(d.port) || 5267,
        token: d.token,
        instance: d.instance || "",
        data_dir: d.data_dir || "",
      },
    });
  } catch (e) {
    /* 扩展不在（未安装/被重载）—— 页面那边有轮询兜底，这里静默即可 */
  }
});
