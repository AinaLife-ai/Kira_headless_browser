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
  4. 「不想装扩展」时的替代路径是**无头后端**（插件自己拉起一个浏览器，
     不碰用户的浏览器），而不是把扩展预装进去 —— 那条路目前**没有实现**
     （启动参数里反而是 `--disable-extensions`），不要再对外这么说。
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


# ─── 扩展版本对比（面板「该更新扩展了」提示的唯一真源）──────────────────
#  ⚠️ 判断**只在后端做一次**：面板只渲染 state，前端不许出现任何版本号字面量。
#     这样以后插件升到 1.6.0、而用户浏览器里还是 1.5.0 时，提示会自动出现 ——
#     不需要再改前端（"未来都可以检测"）。

def parse_version(v: Optional[str]):
    """把版本串解析成可比较的元组。

    容忍 `v1.5.0` / `1.5` / `1.5.0-beta.2`（后缀记为"更小的预发布"），
    解析不出来返回 None —— **不要**拿字符串比大小，`"1.10" < "1.9"` 是错的。
    """
    if v is None:
        return None
    s = str(v).strip().lstrip("vV")
    if not s:
        return None
    head, _, tail = s.partition("-")
    nums = []
    for p in head.split("."):
        if not p.isdigit():
            return None
        nums.append(int(p))
    if not nums:
        return None
    while len(nums) < 3:
        nums.append(0)
    # 预发布（1.5.0-beta < 1.5.0）：正式版记 1、预发布记 0 ——
    # ⚠️ 别写反：写反了会把 beta 当成"比正式版还新"，提示就永远不出现 ✗
    return (tuple(nums[:3]), 0 if tail else 1)


def compare_versions(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """`a` 相对 `b`：1=更新、0=相同、-1=更旧；任一侧解析不了返回 None。"""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return None
    return (pa > pb) - (pa < pb)


def bundled_extension_version(plugin_dir: Path,
                              manifest: Optional[dict] = None) -> Optional[str]:
    """插件**自带**的扩展版本号（`browser-bridge/manifest.json`）。

    这就是"应该是什么版本"的答案：用户浏览器里装的扩展只要比它旧就该提示。
    读不到返回 None —— 拿不到就**不猜**，宁可不说。
    """
    if manifest is not None:
        return str(manifest.get("version") or "").strip() or None
    try:
        import json as _json
        p = extension_dir(plugin_dir) / "manifest.json"
        if not p.is_file():
            return None
        data = _json.loads(p.read_text(encoding="utf-8"))
        return str((data or {}).get("version") or "").strip() or None
    except Exception as e:                                     # pragma: no cover
        logger.warning(f"读取扩展 manifest 版本失败：{e}")
        return None


#: 面板要显示的几种状态（后端只给事实，前端按 state 取自己的文案）
EXT_UP_TO_DATE = "up_to_date"
EXT_UPDATE_AVAILABLE = "update_available"
EXT_OUTDATED_PROTOCOL = "outdated_protocol"
EXT_UNKNOWN = "unknown"
EXT_NEWER = "newer"
EXT_NOT_CONNECTED = "not_connected"

#: 需要**提示用户更新**的那些状态。前端文案必须与这个集合一一对应
#: （有检查盯着，见回归里的 V4：名字对不上 = "提示永远不显示"）。
EXT_NOTICE_STATES = (EXT_UPDATE_AVAILABLE, EXT_OUTDATED_PROTOCOL, EXT_UNKNOWN)


def extension_update_state(bundled: Optional[str],
                           connected: Optional[str],
                           is_connected: bool,
                           protocol_ok: bool = True) -> dict:
    """算出扩展"要不要更新"，给面板用。

    判定顺序（越靠前越确定有问题）：
      1. 没连上              → not_connected（面板本来就有未连接提示，不重复喊）
      2. 连上了但版本读不到   → unknown（很旧的扩展不上报版本 / 握手没成）
      3. 协议版本对不上       → outdated_protocol（版本号可能一样，但能力不匹配）
      4. 比插件自带的旧       → update_available
      5. 比插件自带的还新     → newer（用户自己换了新版，别催他"更新"）
      6. 其它                → up_to_date
    """
    state = EXT_UP_TO_DATE
    if not is_connected:
        state = EXT_NOT_CONNECTED
    elif not connected:
        state = EXT_UNKNOWN
    elif not protocol_ok:
        state = EXT_OUTDATED_PROTOCOL
    else:
        cmp = compare_versions(connected, bundled)
        if cmp is None:
            state = EXT_UNKNOWN
        elif cmp < 0:
            state = EXT_UPDATE_AVAILABLE
        elif cmp > 0:
            state = EXT_NEWER

    return {
        "state": state,
        # 前端据此决定要不要显示提示（= 三个"确实该更新"的状态）
        "needs_update": state in EXT_NOTICE_STATES,
        "bundled_version": bundled,
        "connected_version": connected,
        "protocol_ok": bool(protocol_ok),
    }


def detect_os() -> str:
    s = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(s, "unknown")


def known_chromium_browsers() -> List[Dict[str, str]]:
    """列出这台机器上可能存在的 Chromium 系浏览器（用于给用户点名）。"""
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
    ]
    # ⚠️ 装完这一步的文案必须跟**现在的实际行为**一致。
    #    老版本这里写的是"点它的图标，把「接入令牌」粘进去，点连接"——
    #    那是**手动流程**，早就不是主路径了：现在扩展装完会自己扫常见端口
    #    连上；扫不到也能靠"打开面板即配对"。照老文案做，用户会以为
    #    "必须手填令牌"，跟弹窗里写的"点「自动检测」即可，不用手填"自相矛盾。
    common += [
        "**装好后不用填任何东西** —— 打开一次你的 KiraAI 页面就行："
        "扩展会从**页面自己的地址**认出它在哪个端口，然后自动配对并连上"
        "（所以端口是不是常见的都无所谓，不用手填、也不用改配置）。",
        "找不到时还有个兜底：扩展图标 →「自动检测」；再不行就手动填"
        "「服务地址 / 端口 / 令牌」（令牌在插件面板上点「复制」拿）。",
    ]
    if b == "edge":
        common.append("（Edge 与 Chrome 用同一套扩展机制，这个扩展在 Edge 上可以直接用）")
    # ⚠️ 可选的一步，但**必须提**：Chrome 138+ 起，"执行任意 JavaScript"
    #    需要在扩展详情页单独打开「允许用户脚本 / Allow User Scripts」。
    #    关着的时候 chrome.userScripts 直接是 undefined，报错看起来
    #    就像"浏览器太旧不支持"，用户会白折腾很久。
    common.append(
        "（可选，仅当需要让 AI 执行 JavaScript 时）在扩展详情页打开"
        "「允许用户脚本 / Allow User Scripts」；"
        "Chrome 138+ 默认关闭，关着时该功能会提示「不可用」。")
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
    """扩展的浏览器兼容性说明。

    ⚠️ 版本号必须与 `browser-bridge/manifest.json` 的
    `minimum_chrome_version` 一致 —— 说过低的值会让用户**按提示安装后加载失败**，
    而失败原因（manifest 版本门禁）和安装步骤毫无关系，很难自行排查。
    """
    return (
        "扩展兼容性：\n"
        "  ✅ Chrome 135+（manifest 的 minimum_chrome_version=135；\n"
        "     执行 JS 依赖 chrome.userScripts.execute）\n"
        "  ✅ Edge 135+（同为 Chromium 内核，扩展机制一致，可直接加载）\n"
        "  ✅ Brave / Vivaldi / Opera 等 Chromium 系浏览器（版本同步跟进即可）\n"
        "  ⚠️ Chrome/Edge 120~134：能装扩展，但 manifest 声明的 135 会让\n"
        "     浏览器**拒绝加载**。要么升级浏览器，要么把 manifest 里的\n"
        "     minimum_chrome_version 改小（执行 JS 的能力可能受限）\n"
        "  ❌ Firefox —— 不支持（Firefox 的 MV3 用的是 event page，\n"
        "     不接受 manifest 里的 background.service_worker）\n"
        "  ❌ Safari —— 不支持（扩展格式完全不同）"
    )
