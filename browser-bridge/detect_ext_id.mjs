/**
 * 扩展 ID 探测 —— 让无头后端能用 --load-extension 预装扩展。
 *
 * 背景：我们没法把扩展自动装进**用户自己的**浏览器（那是 Chromium 的硬限制）。
 * 但对我们**自己拉起的**那个 Chromium 实例，可以用 --load-extension 把扩展
 * 直接预装进去。这样即使用户完全不装扩展，无头后端也能走扩展桥协议，
 * 复用同一套命令实现。
 *
 * 要让用户「已经装好的」扩展在无头实例里也生效，需要知道它的 ID。
 * Chrome/Edge 把每个扩展的 ID 存在 Preferences 里，这里去读。
 */

import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const EXT_NAME_HINTS = ["kira browser bridge", "kira-bridge"];

function userDataDirs() {
  const home = os.homedir();
  const p = os.platform();
  if (p === "win32") {
    const local = process.env.LOCALAPPDATA || path.join(home, "AppData/Local");
    return [
      path.join(local, "Google/Chrome/User Data"),
      path.join(local, "Microsoft/Edge/User Data"),
      path.join(local, "BraveSoftware/Brave-Browser/User Data"),
    ];
  }
  if (p === "darwin") {
    return [
      path.join(home, "Library/Application Support/Google/Chrome"),
      path.join(home, "Library/Application Support/Microsoft Edge"),
      path.join(home, "Library/Application Support/BraveSoftware/Brave-Browser"),
    ];
  }
  return [
    path.join(home, ".config/google-chrome"),
    path.join(home, ".config/microsoft-edge"),
    path.join(home, ".config/chromium"),
  ];
}

/**
 * 在用户的浏览器配置里找出已安装的 Kira Browser Bridge 扩展 ID。
 * 返回 {extId, browser, profile} 或 null。
 */
export function findInstalledExtensionId() {
  for (const base of userDataDirs()) {
    if (!fs.existsSync(base)) continue;

    // 每个 profile（Default / Profile 1 / ...）都看一下
    let profiles = [];
    try {
      profiles = fs.readdirSync(base).filter(
        (d) => d === "Default" || /^Profile \d+$/.test(d)
      );
    } catch {
      continue;
    }

    for (const prof of profiles) {
      const prefsPath = path.join(base, prof, "Preferences");
      let prefs;
      try {
        prefs = JSON.parse(fs.readFileSync(prefsPath, "utf8"));
      } catch {
        continue;
      }
      const exts = (prefs.extensions && prefs.extensions.settings) || {};
      for (const [id, cfg] of Object.entries(exts)) {
        const name = String(
          (cfg.manifest && cfg.manifest.name) || cfg.name || ""
        ).toLowerCase();
        if (EXT_NAME_HINTS.some((h) => name.includes(h))) {
          return { extId: id, browser: path.basename(base), profile: prof };
        }
      }
    }
  }
  return null;
}

// 允许直接 `node detect_ext_id.mjs` 跑
if (import.meta.url === `file://${process.argv[1]}`) {
  const r = findInstalledExtensionId();
  console.log(JSON.stringify(r, null, 2));
}
