"""扩展分发与首次运行引导。

## 能做到什么、做不到什么（先说清楚）

**做不到**：像装软件那样「自动装进用户的浏览器」。
Chromium 的扩展安装有硬性限制，没有任何正规途径能让一个本地程序静默地把
扩展塞进用户的 Chrome/Edge：

  * `--load-extension` 命令行开关：只对**我们自己拉起的那个 Chromium 实例**有效，
    管不到用户已经在用的浏览器；而且 Google 从 2025 起在收紧它
    （官方明说"曾被大量用于加载恶意扩展"），新版可能失效。
  * `ExtensionInstallForcelist` 企业策略：确实能静默安装，但需要改注册表 /
    组策略，属于管理员操作，不该由插件去动。
  * 上架应用商店：那要审核，且用户仍需自己点「添加」。

**能做到**：把「找扩展 → 怎么装 → 装完怎么连」这条链路的每一步都替用户想好：

  1. 扩展**随插件一起打包**（`browser-bridge/` 就在插件目录里），不用另外下载
  2. 首次运行、扩展又没连上时，插件**主动**告诉用户：
     - 扩展在哪个绝对路径（复制即用）
     - 当前用的是哪个浏览器（Chrome / Edge / 其它 Chromium 系）
     - 针对该浏览器的具体步骤
     - 令牌在哪、怎么填
  3. 面板上提供「打开扩展文件夹」「复制路径」按钮
  4. 无头后端启动时可以用 `--load-extension` 把扩展预装进**插件自己的**
     浏览器里（不影响用户的浏览器），作为「不想装扩展」时的替代路径
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from core.logging_manager import get_logger

logger = get_logger("browser_merged", "cyan")

#: 扩展目录名（随插件打包）
EXT_DIR_NAME = "browser-bridge"


def extension_dir(plugin_dir: Path) -> Path:
    return Path(plugin_dir) / EXT_DIR_NAME


def extension_exists(plugin_dir: Path) -> bool:
    d = extension_dir(plugin_dir)
    return (d / "manifest.json").is_file()


def detect_os() -> str:
    s = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(s, "unknown")


def known_chromium_browsers() -> List[Dict[str, str]]:
    """列出这台机器上可能存在的 Chromium 系浏览器（用于给用户点名）。"""
    home = Path.home()
    osname = detect_os()
    # ⚠️ Windows 的安装位置有多个，且**都不在 PATH 上** ——
    #    只查一个环境变量会漏掉大多数用户：
    #      * Chrome 常见于 %PROGRAMFILES%\Google\Chrome\...（64 位）
    #        或 %PROGRAMFILES(X86)%\...（32 位 / 老安装）
    #      * Edge 装在 %PROGRAMFILES(X86)%\Microsoft\Edge\...（即使系统是 64 位）
    #    另外环境变量为空时 `Path("") / "x"` 会得到**相对路径**，
    #    可能误判本机存在某个无关目录 —— 所以空值要直接跳过。
    def _win_paths(env_names, rel):
        out = []
        for ev in env_names:
            base = os.environ.get(ev)
            if not base:
                continue
            out.append(Path(base) / rel)
        return out

    cands = {
        "chrome": {
            "windows": (
                _win_paths(["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"],
                           "Google/Chrome/Application/chrome.exe")
            ),
            "macos": [Path("/Applications/Google Chrome.app")],
            "linux": [Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable")],
        },
        "edge": {
            "windows": (
                _win_paths(["PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"],
                           "Microsoft/Edge/Application/msedge.exe")
            ),
            "macos": [Path("/Applications/Microsoft Edge.app")],
            "linux": [Path("/usr/bin/microsoft-edge"), Path("/usr/bin/microsoft-edge-stable")],
        },
        "brave": {
            "windows": (
                _win_paths(["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"],
                           "BraveSoftware/Brave-Browser/Application/brave.exe")
            ),
            "macos": [Path("/Applications/Brave Browser.app")],
            "linux": [Path("/usr/bin/brave-browser")],
        },
        "chromium": {
            "windows": _win_paths(["LOCALAPPDATA"], "Chromium/Application/chrome.exe"),
            "macos": [Path("/Applications/Chromium.app")],
            "linux": [Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser")],
        },
    }
    found = []
    for name, per_os in cands.items():
        for p in per_os.get(osname, []):
            try:
                if str(p) and Path(p).exists():
                    found.append({"name": name, "path": str(p)})
                    break
            except Exception:
                continue
    # ⚠️ PATH 里也找一遍，并**合并**结果（不是"没找到才兜底"）。
    #    有些发行版只把浏览器装在 PATH 上而不在标准位置，
    #    只走兜底路径的话会漏报"检测到你机器上有 Chrome"。
    have = {b["name"] for b in found}
    for exe, name in (("google-chrome", "chrome"), ("google-chrome-stable", "chrome"),
                      ("chromium", "chromium"), ("chromium-browser", "chromium"),
                      ("msedge", "edge"), ("microsoft-edge", "edge"),
                      ("brave-browser", "brave")):
        if name in have:
            continue
        w = shutil.which(exe)
        if w:
            found.append({"name": name, "path": w})
            have.add(name)
    return found


#: 各浏览器的加载地址（打开即到扩展页）
EXTENSIONS_URL = {
    "chrome": "chrome://extensions",
    "edge": "edge://extensions",
    "brave": "brave://extensions",
    "chromium": "chrome://extensions",
}


def steps_for(browser: str, ext_path: str) -> List[str]:
    """按浏览器给出**具体**的加载步骤。"""
    b = (browser or "chrome").lower()
    url = EXTENSIONS_URL.get(b, "chrome://extensions")
    label = {"chrome": "Chrome", "edge": "Edge", "brave": "Brave",
             "chromium": "Chromium"}.get(b, "Chromium 系浏览器")

    common = [
        f"在 {label} 地址栏打开：{url}",
        "打开右上角的「开发者模式 / Developer mode」开关",
        "点「加载已解压的扩展程序 / Load unpacked」",
        f"选择这个文件夹：\n     {ext_path}",
        "扩展出现后，点它的图标，把「接入令牌」粘进去，点连接",
    ]
    # 令牌从哪儿拿，各浏览器一样（都在插件自己的面板里），单独说一句
    common.append(
        "（令牌在 KiraAI 的插件面板里：「浏览器」→ 复制接入令牌）")
    if b == "edge":
        common.append("（Edge 与 Chrome 用同一套扩展机制，这个扩展在 Edge 上可以直接用）")
    return common


def first_run_notice(plugin_dir: Path, connected: bool,
                     browsers: Optional[List[Dict[str, str]]] = None,
                     profile_mode: str = "inherit") -> Optional[str]:
    """扩展没连上时，返回一段给用户看的引导；不需要时返回 None。

    这段文字有两个去处：工具返回值（模型会转述给用户）和插件面板。
    """
    if connected:
        return None
    d = extension_dir(plugin_dir)
    if not extension_exists(plugin_dir):
        return ("⚠️ 浏览器插件自带的扩展文件夹缺失了（应该在 "
                f"{d}）。请重新安装插件包。")

    browsers = browsers if browsers is not None else known_chromium_browsers()
    lines = ["🔌 想让 AI 直接操作**你正在用的浏览器**？需要装一个随插件附带的扩展：", ""]
    lines.append(f"📁 扩展就在插件目录里，不用另外下载：\n     {d}")
    lines.append("")

    if browsers:
        names = "、".join(b["name"].capitalize() for b in browsers)
        lines.append(f"🖥️ 检测到你机器上有：{names}")
        lines.append("")

    lines.append("📋 安装步骤（约 1 分钟）：")
    primary = browsers[0]["name"] if browsers else "chrome"
    for i, s in enumerate(steps_for(primary, str(d)), 1):
        lines.append(f"  {i}. {s}")

    lines.append("")
    # ⚠️ 措辞要跟着 profile 模式走：
    #    inherit 会**复制**真实浏览器数据（含 cookie/登录态），
    #    说成"干净的浏览器环境、没有登录态"是错的。
    if (profile_mode or "inherit").lower() == "inherit":
        lines.append("💡 不想装扩展也完全可以：AI 会用插件自带的无头浏览器，"
                     "并且会**复制你的浏览器数据**（登录态/Cookie 都在），"
                     "只是它不是「你眼前那个浏览器」，看不到你当前打开的标签页。")
    else:
        lines.append("💡 不想装扩展也完全可以：AI 会自动用插件自带的无头浏览器干活，"
                     "只是那样用的是干净的浏览器环境，没有你的登录态。")
    lines.append("   装了扩展的好处：用的是**你眼前这个浏览器**，登录态、"
                 "已打开的标签页都能直接用，而且不会和你的浏览器抢锁。")
    return "\n".join(lines)


def compatibility_report() -> str:
    """扩展的浏览器兼容性说明。"""
    return (
        "扩展兼容性：\n"
        "  ✅ Chrome 120+（执行 JS 依赖 chrome.userScripts，它从 120 起提供）\n"
        "  ✅ Edge 120+（同为 Chromium 内核，扩展机制一致，可直接加载）\n"
        "  ✅ Brave / Vivaldi / Opera 等 Chromium 系浏览器\n"
        "  ❌ Firefox —— 不支持（Firefox 的 MV3 用的是 event page，\n"
        "     不接受 manifest 里的 background.service_worker）\n"
        "  ❌ Safari —— 不支持（扩展格式完全不同）"
    )
