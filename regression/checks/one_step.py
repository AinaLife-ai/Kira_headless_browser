"""一步到位：写操作要**连页面结果一起**返回；无头后端要有安全 UA。

两件事放一起，是因为它们回答的是同一类问题 —— "bot 用起来顺不顺"：
  · 调用次数：框架每轮只给 5 次工具调用，"点一下 → 再看一眼"各占一次就太快用光
  · 环境可信度：无头浏览器默认 UA 带 Headless，B站这类站点会直接拦
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..harness import PLUGIN_DIR, section, src_safe

TITLE = "一步到位（动作带结果 / 安全 UA）"

sys.path.insert(0, str(PLUGIN_DIR.parent))


def run(r) -> None:
    section("一步到位")
    main = src_safe("main.py")
    head = src_safe("backends/headless_backend.py")

    # ── S1：写操作后必须把页面内容一起带回来 ──────────────────────────
    # ⚠️ 用户明确要求："尽量大部分都一步到位，动作结构和页面结果这种一起返回"。
    #    框架有 `bot_config.agent.max_tool_calls_per_turn`（默认 **5**），
    #    "点一下 → 再看一眼"各占一次的话 5 次只够两轮半，bot 还没干完就被
    #    限流（日志里满屏 Tool call limit exceeded ... 'browser_page'）。
    #    ⚠️ 而**扩展内部命令不计次**（计的是 LLM 发起的工具调用），
    #       所以在 `_call` 里顺手取页面是白赚的 —— 必须落在那里，
    #       才能一处覆盖所有写操作（也才不会"新加个工具就忘了带结果"）。
    _bad1 = []
    if "_with_page_after" not in main:
        _bad1.append("没有'带页面一起返回'的实现")
    if "for_write and self.return_page_after_write" not in main:
        _bad1.append("没有挂在 for_write 上（新加的写工具会漏掉）")
    if 'cfg.get("return_page_after_write"' not in main:
        _bad1.append("没有开关（用户想省 token 时关不掉）")
    if '"return_page_after_write"' not in src_safe("schema.json"):
        _bad1.append("schema 里没有这一项")
    r.ok("S1 写操作后一并返回页面（挂在 _call 上，覆盖所有写工具）", not _bad1,
         f"问题={_bad1 or '无'}")

    # ── S2：无头后端必须有**不带 Headless**的安全 UA ───────────────────
    #    Playwright 自带浏览器的默认 UA 里带 `HeadlessChrome`，
    #    B站/知乎这类站点会据此直接拦（表现为"页面能开但内容空/弹验证"）。
    _bad2 = []
    if "_safe_default_ua" not in head:
        _bad2.append("没有默认 UA")
    if "Headless" not in head:
        _bad2.append("没说清为什么要去掉 Headless")
    r.ok("S2 无头后端默认给安全 UA（不含 Headless，能正常上 B站）", not _bad2,
         f"问题={_bad2 or '无'}")

    # ── S3：行为验证 —— 真造一个无头后端，看 UA 到底是什么 ─────────────
    #    只查"代码里有没有"会被自己骗；这里直接把对象建出来看值。
    try:
        from . import runtime_behavior
        hb = runtime_behavior._load_plugin()
        b = hb.HeadlessBackend(Path("/tmp/_probe/plugin_data/hb"), {})
        ua = b.user_agent or ""
        _ok3 = ua and "Headless" not in ua and "Mozilla/" in ua
        r.ok("S3 实测默认 UA 不含 Headless、且像真实浏览器", bool(_ok3), f"UA={ua!r}")
        # 用户自己配了就听他的
        b2 = hb.HeadlessBackend(Path("/tmp/_probe/plugin_data/hb"),
                                {"user_agent": "Mozilla/5.0 (custom)"})
        r.ok("S4 用户填了 user_agent 就优先用他的",
             b2.user_agent == "Mozilla/5.0 (custom)", f"实际={b2.user_agent!r}")

    except Exception as e:
        r.warn("S3/S4 真造无头后端看 UA（需要桩环境）", f"{type(e).__name__}: {e}")

    # ── S5 内部页（edge://）的报错必须**可照做** ────────────────────────
    #    ⚠️ 原来只说"不允许注入脚本，请先切换到普通网页" —— 模型只能放弃，
    #       然后回用户一句"扩展没权限访问"，看着像缺陷。
    #       真实情况是**硬边界**（`<all_urls>` 也不含 chrome://），开不了；
    #       但有两条能走通的路必须写出来：截图（不需要注入）+ 书签数据接口。
    _sh = src_safe("browser-bridge/shared.js")
    _bad5 = []
    if "硬边界" not in _sh:
        _bad5.append("没说清这是浏览器的硬边界（不是权限没开）")
    if "browser_screenshot" not in _sh:
        _bad5.append("没告诉模型'截图对内部页照样有效'")
    if "bookmarks" not in _sh:
        _bad5.append("没告诉模型'书签数据有接口'")
    r.ok("S5 内部页报错可照做（硬边界 + 截图 + 书签接口）", not _bad5,
         f"问题={_bad5 or '无'}")

    # ── S6 书签能力与权限 ───────────────────────────────────────────────
    import json as _json
    _mf = _json.loads(src_safe("browser-bridge/manifest.json"))
    _bad6 = []
    if "bookmarks" not in (_mf.get("permissions") or []):
        _bad6.append("manifest 没申请 bookmarks 权限")
    if "async function bookmarks" not in src_safe("browser-bridge/capabilities.js"):
        _bad6.append("capabilities.js 里没有 bookmarks 实现")
    if '"bookmarks"' not in src_safe("main.py"):
        _bad6.append("browser_interact 里没接这个 action")
    r.ok("S6 书签数据能力可用（权限 + 实现 + action 都齐）", not _bad6,
         f"问题={_bad6 or '无'}")

    # ── S7 扩展能执行的命令，插件必须**够得着** ────────────────────────
    #    ⚠️ 这一类 bug 用户抓到过两次：
    #       · close_tab / activate_tab —— 扩展实现了 31 个命令，插件只调了 12 个
    #       · get_selection —— 扩展一直有，插件从没调用
    #       从 bot 的视角看就是"这个能力不存在"，它只能去试 Ctrl+W 这种歪招。
    #    判据：协议里的每个命令，要么 main.py 里调得到，要么在下面的
    #    **内部子步骤**白名单里（那些是别的命令内部用的，不该单独暴露）。
    import re as _re7
    _INTERNAL = {
        # 只有**真正内部**的才在白名单里 —— 别的都该在 main.py 或后端方法里找得到。
        # 白名单放宽 = 这个检查就没牙齿了（第一版把所有东西都列进去，等于白写）。
        "upload_chunk", "upload_finish", "upload_abort",   # upload 的内部步骤
        "exec_js",        # 后端方法叫 execute_js（方法名 ≠ 命令名），下面单独认
        "key_up", "mouse_up",   # 动作走的是 key_down_up / mouse_down_up 组合
    }
    _proto = src_safe("protocol.py")
    _names = set(_re7.findall(r'CMD_\w+ = "([a-z_]+)"', _proto))
    _main = src_safe("main.py")
    _backend = {}
    for _f in ("backends/extension_backend.py", "backends/headless_backend.py"):
        _backend[_f] = src_safe(_f)
    _unreachable = []
    for _n in sorted(_names - _INTERNAL):
        # ⚠️ 判据必须是 **`_call("xxx")`**，不能只查 `"xxx"` 这个字符串 ——
        #    渲染分支里也会出现 `method == "get_selection"`，
        #    只查字符串的话"删掉调用、留下渲染"照样绿（反向验证时抓到的）。
        if f'_call("{_n}"' in _main:
            continue
        # ⚠️ 不看"后端有没有这个方法" —— 方法存在**不等于**插件会调它。
        #    get_selection 就是活例子：后端方法一直有，main.py 从没调过 ✗
        #    （反向验证时正是靠删掉调用才暴露出这一点）。
        # 方法名和命令名不同的（exec_js ↔ execute_js），用"哪个后端方法
        # 发这条命令"反查。
        if any(f'CMD_{_n.upper()} ' in _v or f'CMD_{_n.upper()},' in _v
               or f'CMD_{_n.upper()})' in _v for _v in _backend.values()):
            continue
        _unreachable.append(_n)
    r.ok("S7 扩展能执行的命令，插件都够得着（不会再出现'实现了却没接出来'）",
         not _unreachable, f"够不着的={_unreachable or '无'}")
