"""cookie 目录的自动加载。

**这不是新功能** —— 原版 `headless_browser` 有（`_load_cookies()`），
v2.1.0 重写双后端架构时**被整段丢掉**了：`data/files/cookie/` 这个目录
还留在 `.gitignore` 里，但已经没有任何代码去读它，成了死目录。

## 它解决什么问题

用户在同一个插件里登过多个站点（ChatGPT / Claude / Gemini …），
重装插件、换机器、或者无头后端用了临时 profile 之后，
**登录态就没了**。这个功能把 cookie 以 JSON 落盘到固定目录，
启动时自动灌回去 —— 不用每次重新登录。

配合 `browser_cookie(action="export")` 导出、`action="import"` 手动导入，
构成完整的"登录态搬运"链路。

## 文件格式

每个站点一个 ``<站点>.json``，两种形态都接受：

* 裸数组 —— ``[{"name": ..., "value": ..., "domain": ...}, ...]``
* 包了一层 —— ``{"cookies": [...]}``（``browser_cookie`` 导出的就是这种）

cookie 字段用浏览器扩展那套命名（``expirationDate`` / ``sameSite`` /
``httpOnly``），这里转成 Playwright 要的形式。
"""
from __future__ import annotations

import glob
import json
import os
from typing import List, Tuple

from core.logging_manager import get_logger

logger = get_logger("headless_browser.cookies", "cyan")

#: 浏览器扩展的 sameSite 取值 → Playwright 的取值
_SAME_SITE = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "Lax",
}

#: cookie 必须具备的字段（缺了没法用）
_REQUIRED = ("name", "value", "domain")


def cookie_file_to_playwright(raw) -> Tuple[List[dict], List[str]]:
    """把一个 cookie JSON 的内容转成 Playwright 的 add_cookies 格式。

    Returns:
        ``(可用的 cookie 列表, 跳过的原因列表)``
    """
    # 兼容嵌套格式：`browser_cookie(action="export")` 导出的就是 {"cookies": [...]}
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    if not isinstance(raw, list):
        raw = [raw]

    out: List[dict] = []
    skipped: List[str] = []
    for c in raw:
        if not isinstance(c, dict):
            skipped.append(f"不是对象：{type(c).__name__}")
            continue
        miss = [k for k in _REQUIRED if k not in c]
        if miss:
            skipped.append(f"缺字段 {miss}")
            continue

        ss_raw = str(c.get("sameSite", "Lax")).lower()
        _same_site = _SAME_SITE.get(ss_raw, "Lax")
        # ⚠️ `sameSite=None` 的 cookie **必须**带 `Secure`，否则浏览器直接拒绝
        #    （Playwright 的 add_cookies 会抛错，或浏览器静默丢弃）。
        #    导出文件里若只有 sameSite=None 没标 secure（有些导出工具就是这样），
        #    照原样写进去 = 这个 cookie 白导了。
        _secure = bool(c.get("secure", False))
        if _same_site == "None" and not _secure:
            _secure = True
        item = {
            "name": str(c["name"]),
            "value": str(c["value"]),
            "domain": str(c["domain"]),
            "path": c.get("path", "/") or "/",
            "secure": _secure,
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": _same_site,
        }
        # 过期时间：扩展用 `expirationDate`（浮点秒），Playwright 用 `expires`（整数秒）
        exp = c.get("expirationDate", c.get("expires"))
        if exp:
            try:
                item["expires"] = int(float(exp))
            except (ValueError, TypeError):
                pass
        out.append(item)
    return out, skipped


def iter_cookie_files(cookies_dir: str) -> List[str]:
    """列出目录下所有 .json（按名字排序，行为可预期）。"""
    try:
        return sorted(glob.glob(os.path.join(cookies_dir, "*.json")))
    except Exception as e:
        logger.warning(f"扫描 cookie 目录失败（{cookies_dir}）：{e}")
        return []


async def load_into_context(context, cookies_dir: str) -> dict:
    """把 cookie 目录里的东西全部灌进一个 Playwright context。

    **失败不影响浏览器使用** —— 任何一个文件坏掉都只跳过它。

    Returns: ``{"files": n, "loaded": n, "cookies": n, "errors": [...]}``
    """
    stats = {"files": 0, "loaded": 0, "cookies": 0, "errors": []}
    if context is None or not cookies_dir:
        return stats

    try:
        os.makedirs(cookies_dir, exist_ok=True)
    except Exception as e:
        stats["errors"].append(f"建目录失败：{e}")
        return stats

    files = iter_cookie_files(cookies_dir)
    stats["files"] = len(files)
    if not files:
        logger.debug(f"cookie 目录为空，跳过自动加载：{cookies_dir}")
        return stats

    for fp in files:
        name = os.path.basename(fp)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            # 坏文件只跳过这一个，不能连累其它站点
            stats["errors"].append(f"{name}: 读取/解析失败 {e}")
            continue

        cookies, skipped = cookie_file_to_playwright(raw)
        if skipped:
            logger.warning(f"{name}: 跳过 {len(skipped)} 条无效 cookie（{skipped[:3]}）")
        if not cookies:
            stats["errors"].append(f"{name}: 没有可用的 cookie")
            continue

        try:
            await context.add_cookies(cookies)
        except Exception as e:
            stats["errors"].append(f"{name}: 写入失败 {e}")
            continue
        stats["loaded"] += 1
        stats["cookies"] += len(cookies)
        logger.info(f"已自动加载 cookie：{name}（{len(cookies)} 条）")

    if stats["loaded"]:
        logger.info(
            f"cookie 自动加载完成：{stats['loaded']}/{stats['files']} 个文件，"
            f"共 {stats['cookies']} 条")
    return stats
