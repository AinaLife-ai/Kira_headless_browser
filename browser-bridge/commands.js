import {
  resolveTab, assertInjectable, callContent, detectBrowser, state,
} from "./shared.js";
import { ensureUserScripts } from "./capabilities.js";

/**
 * 补齐的命令实现：把无头后端有、扩展桥原先缺的能力都做出来。
 * 挂在 background.js 的 execute() 分发里。
 */

// ─── 信息 ────────────────────────────────────────────────────────────

async function getInfo(params) {
  const tab = await resolveTab(params.tab_id);
  return { title: tab.title || "", url: tab.url || "" };
}

// ─── 导航 ────────────────────────────────────────────────────────────

async function goBack(params) {
  const tab = await resolveTab(params.tab_id);
  await chrome.tabs.goBack(tab.id);
  await new Promise((r) => setTimeout(r, 400));
  const fresh = await chrome.tabs.get(tab.id);
  return { url: fresh.url, title: fresh.title || "" };
}

async function refresh(params) {
  const tab = await resolveTab(params.tab_id);
  await chrome.tabs.reload(tab.id);
  return { url: tab.url };
}

// ─── 悬停 ────────────────────────────────────────────────────────────

async function hover(params) {
  const tab = await resolveTab(params.tab_id);
  const res = await callContent(tab, "hover", { selector: params.selector }, 15000);
  return { ok: true, url: tab.url, match: res && res.match };
}

// ─── 键盘（页面内派发，不需要 CDP）────────────────────────────────────

async function keyPress(params) {
  const tab = await resolveTab(params.tab_id);
  const res = await callContent(tab, "key_press", { key: params.key }, 15000);
  return { ok: true, url: tab.url, match: res && res.match };
}

async function keyDownUp(params, down) {
  const tab = await resolveTab(params.tab_id);
  await callContent(tab, down ? "key_down" : "key_up", { key: params.key }, 15000);
  return { ok: true, url: tab.url };
}

// ─── 鼠标（页面内派发，带真实坐标）────────────────────────────────────

async function mouseMove(params) {
  const tab = await resolveTab(params.tab_id);
  await callContent(tab, "mouse_move", {
    x: params.x, y: params.y, steps: params.steps || 1,
  }, 15000);
  return { ok: true, x: params.x, y: params.y };
}

async function mouseClick(params) {
  const tab = await resolveTab(params.tab_id);
  const before = tab.url;
  const res = await callContent(tab, "mouse_click", {
    x: params.x, y: params.y,
    button: params.button || "left",
    click_count: params.click_count || 1,
  }, 20000);
  await new Promise((r) => setTimeout(r, 600));
  let after = before, navigated = false;
  try {
    const fresh = await chrome.tabs.get(tab.id);
    after = fresh.url;
    navigated = after !== before;
  } catch (_) {}
  return { ok: true, navigated, url: after, match: res && res.match };
}

async function mouseDownUp(params, down) {
  const tab = await resolveTab(params.tab_id);
  await callContent(tab, down ? "mouse_down" : "mouse_up",
                    { button: params.button || "left" }, 15000);
  return { ok: true, url: tab.url };
}

async function mouseWheel(params) {
  const tab = await resolveTab(params.tab_id);
  await callContent(tab, "mouse_wheel", {
    delta_x: params.delta_x || 0, delta_y: params.delta_y || 0,
  }, 15000);
  return { ok: true };
}

async function mouseDrag(params) {
  const tab = await resolveTab(params.tab_id);
  await callContent(tab, "mouse_drag", {
    start_x: params.start_x, start_y: params.start_y,
    end_x: params.end_x, end_y: params.end_y,
    button: params.button || "left", steps: params.steps || 10,
  }, 30000);
  return { ok: true, url: tab.url };
}

// ─── 文件列表（列浏览器自己的下载记录，而非插件目录）──────────────────

async function listFiles(params) {
  if (params.dir_type === "downloads" && chrome.downloads) {
    const items = await chrome.downloads.search({ limit: params.limit || 20 });
    return {
      dir: "(浏览器下载目录)",
      files: items
        .filter((d) => d.filename)
        .map((d) => ({
          name: d.filename.split(/[\\/]/).pop(),
          path: d.filename,
          size: d.totalBytes || d.fileSize || 0,
          mtime: d.endTime ? Date.parse(d.endTime) / 1000 : 0,
        })),
    };
  }
  return { dir: "(扩展侧无此目录)", files: [] };
}

// ─── 调试 ────────────────────────────────────────────────────────────

async function debugInfo(params) {
  const chk = await ensureUserScripts();
  return {
    backend: "extension",
    browser: detectBrowser(),
    connected: !!(state.socket && state.socket.readyState === WebSocket.OPEN),
    user_scripts: chk.ok ? "可用" : `不可用(${chk.reason})`,
    has_downloads_api: !!chrome.downloads,
    has_cookies_api: !!chrome.cookies,
  };
}

export {
  getInfo, goBack, refresh, hover, keyPress, keyDownUp,
  mouseMove, mouseClick, mouseDownUp, mouseWheel, mouseDrag,
  listFiles, debugInfo,
};
