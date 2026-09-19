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
