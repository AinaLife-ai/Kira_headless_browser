"""换后端必须让 bot 有感知（不许静默回退）。

## 为什么有它（用户报的）

日志里只有一行 WARNING：
`[screenshot] 用户浏览器（Edge · 扩展 v1.5.2）失败：Failed to capture tab:
image readback failed` —— 然后**就没有然后了**。`_call()` 会继续试下一个候选后端，
而无头后端截的是**它自己那个浏览器**（`self._page`）。于是：

    扩展截图失败 → 自动换成无头 → 照样返回「✅ 截图已保存」

模型拿到一张空白图，还以为那是用户的浏览器。这正是仓库一直在防的"假成功"。

用户的要求：**不要静默回退；如果切换了，必须是 bot 有感知的。**

## 这里盯三件事

1. **切换必须说出来**：结果不是首选后端给的 → 返回文本以「🔁 后端已切换」开头，
   写清原因，并说明"两者是两套浏览器"；`screenshot` 还要专门说
   "这张图不是用户浏览器当前画面"。
2. **换后端就换语义的操作不许换**（`no_fallback`）：截"用户浏览器可视区域"时，
   无头后端拍的是另一个浏览器 —— 宁可直接失败，也不给一张看起来成功的错图。
3. **正常路径一个字都不多**：首选后端成功时不许出现切换提示。

做法：真造一个 `BrowserPlugin` 实例（`__new__`，不跑 `__init__`）+ 假后端，
**真调 `_call`** 看它返回什么。带反向自检。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

from ..harness import PLUGIN_DIR, src_safe

TITLE = "换后端必须被 bot 感知（不许静默回退）"


def _load_main():
    """按依赖顺序把插件模块挂到包下面加载，返回 main 模块（与 ext_update 同一套）。"""
    from ..harness import install_stubs, load_module
    install_stubs()
    pkg = "hb_failover_main"
    if pkg in sys.modules:
        return sys.modules[pkg + ".main"]
    m = types.ModuleType(pkg)
    m.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg] = m
    mods = {}
    for name in ["setup_guide", "protocol", "security", "cookies", "vlm", "tokens",
                 "backends", "backends.base", "backends.router",
                 "backends.headless_backend", "bridge", "main"]:
        path = PLUGIN_DIR / (name.replace(".", "/") + ".py")
        if path.is_file():
            mods[name] = load_module(name, path, pkg, PLUGIN_DIR)
    return mods.get("main")


class FakeBackend:
    """够 `_call` 用的最小后端桩。"""

    def __init__(self, name, display, ok=True, error="扩展挂了", supports=True):
        self.name = name
        self.display = display
        self.available = True
        self.is_user_browser = name == "extension"
        self._ok = ok
        self._error = error
        self.calls = 0
        self.supports = supports

    #: 由 run() 注入真的 OpResult 类（走真类，不自己造形状）
    OpResult = None

    async def _op(self, **kw):
        OpResult = FakeBackend.OpResult
        self.calls += 1
        if self._ok:
            return OpResult(data={"path": kw.get("path", "/tmp/x.png"), "url": "https://a/"},
                            backend=self.name)
        return OpResult.fail(self._error, self.name)

    # 截图与读页面各来一个（方法名即 op 名）
    screenshot = _op
    get_page = _op


class FakeRouter:
    def __init__(self, backends):
        self._b = backends

    def candidates(self):
        return list(self._b)

    @property
    def active(self):
        return self._b[0] if self._b else None

    def hint(self):
        return "没有可用的浏览器"


def _run(coro):
    """跑一个协程（检查是同步的，这里自己开一个事件循环）。"""
    import asyncio
    return asyncio.run(coro)


def _make_plugin(main_mod, backends):
    obj = main_mod.BrowserPlugin.__new__(main_mod.BrowserPlugin)
    obj.router = FakeRouter(backends)
    obj.return_page_after_write = False
    obj._setup_notified = True          # 别让"装扩展引导"混进断言
    obj._setup_notice = ""
    obj.enabled = True
    return obj


def run(r) -> None:
    from ..harness import section
    section("J. 换后端必须被 bot 感知（不许静默回退）")

    try:
        main_mod = _load_main()
        if main_mod is None:
            raise RuntimeError("加载不到 main 模块")
    except Exception as e:
        r.ok("J0 能加载插件 main（换后端判据所在模块）", False,
             f"{type(e).__name__}: {e}"[:160])
        return

    # 注入真的 OpResult（从刚加载的包里取，确保形状与产品一致）
    try:
        import importlib
        OpResult = importlib.import_module("hb_failover_main.backends.base").OpResult
        FakeBackend.OpResult = OpResult
    except Exception as e:
        r.ok("J0b 拿到真的 OpResult 类", False, f"{type(e).__name__}: {e}"[:120])
        return

    # ── J1/J2 截图：不许静默换；要换必须说清"那是另一个浏览器" ────────
    try:
        ext = FakeBackend("extension", "用户浏览器（Edge · 扩展 v1.5.2）",
                        ok=False, error="Failed to capture tab: image readback failed")
        headless = FakeBackend("headless", "无头浏览器")
        obj = _make_plugin(main_mod, [ext, headless])

        # ① 允许回退的路径（整页截图那种"扩展本来做不到"的）：必须显式告知
        out = _run(
            obj._call("screenshot", path="/tmp/x.png", full_page=True))
        head_ok = isinstance(out, str) and out.startswith("🔁 后端已切换")
        why_ok = "原因" in out
        browser_ok = ("另一个浏览器" in out or "独立的一套" in out
                      or "另一套" in out or "两套" in out)
        shot_ok = "用户浏览器当前画面" in out
        r.ok("J1 截图换了后端 → 结果开头就写明「后端已切换」+ 原因 + 那是另一个浏览器",
             head_ok and why_ok and browser_ok and shot_ok,
             f"开头={out[:40]!r} 原因={'有' if why_ok else '缺'} "
             f"说明是另一个浏览器={'有' if browser_ok else '缺'} "
             f"画面说明={'有' if shot_ok else '缺'}")

        # ② 可视区域截图：**不许**回退，宁可失败
        ext2 = FakeBackend("extension", "用户浏览器（Edge · 扩展 v1.5.2）",
                        ok=False, error="Failed to capture tab: image readback failed")
        headless2 = FakeBackend("headless", "无头浏览器")
        obj2 = _make_plugin(main_mod, [ext2, headless2])
        out2 = _run(
            obj2._call("screenshot", path="/tmp/x.png", no_fallback=True))
        r.ok("J2 可视区域截图失败时不换后端（不给一张“看起来成功”的错图）",
             headless2.calls == 0 and isinstance(out2, str) and not out2.startswith("✅")
             and "已尝试" in out2,
             f"无头被调用={headless2.calls} 次；回话={out2[:46]!r}")

    except Exception as e:
        r.ok("J1 截图换了后端 → 结果开头就写明「后端已切换」+ 原因 + 那是另一个浏览器",
             False, f"{type(e).__name__}: {e}"[:160])
        r.ok("J2 可视区域截图失败时不换后端（不给一张“看起来成功”的错图）",
             False, f"{type(e).__name__}: {e}"[:160])

    # ── J3 非截图方法：也要说清是"两套浏览器"（但别塞截图专有的话）────
    try:
        ext3 = FakeBackend("extension", "用户浏览器（Edge · 扩展 v1.5.2）",
                        ok=False, error="Failed to capture tab: image readback failed")
        head3 = FakeBackend("headless", "无头浏览器")
        obj3 = _make_plugin(main_mod, [ext3, head3])
        out3 = _run(
            obj3._call("get_page", chars=100))
        r.ok("J3 其它能力换后端同样明说（且不误塞截图专有文案）",
             out3.startswith("🔁 后端已切换") and "两套" in out3
             and "用户浏览器当前画面" not in out3,
             out3[:60])
    except Exception as e:
        r.ok("J3 其它能力换后端同样明说（且不误塞截图专有文案）", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J4 首选后端成功 → 一个字的提示都不该多 ────────────────────
    try:
        ext4 = FakeBackend("extension", "用户浏览器（Edge · 扩展 v1.5.2）", ok=True)
        head4 = FakeBackend("headless", "无头浏览器")
        obj4 = _make_plugin(main_mod, [ext4, head4])
        out4 = _run(
            obj4._call("screenshot", path="/tmp/x.png", no_fallback=True))
        r.ok("J4 首选后端成功时不许出现「后端已切换」（否则每轮都刷）",
             "🔁" not in out4 and out4.startswith("✅"),
             out4[:50])
    except Exception as e:
        r.ok("J4 首选后端成功时不许出现「后端已切换」（否则每轮都刷）", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J5 反向自检：把提示打成空串 → 判据必须报红 ────────────────
    try:
        ext5 = FakeBackend("extension", "用户浏览器（Edge · 扩展 v1.5.2）",
                        ok=False, error="Failed to capture tab: image readback failed")
        head5 = FakeBackend("headless", "无头浏览器")
        obj5 = _make_plugin(main_mod, [ext5, head5])
        obj5._switch_notice = lambda *a, **k: ""          # 模拟"静默回退"
        out5 = _run(
            obj5._call("screenshot", path="/tmp/x.png", full_page=True))
        caught = not out5.startswith("🔁 后端已切换")
        r.ok("J5 反向自检：静默回退（不给提示）必须被判据抓到", caught,
             "把 _switch_notice 打成空串后判据确实报红" if caught else "没抓到 ✗")
    except Exception as e:
        r.ok("J5 反向自检：静默回退（不给提示）必须被判据抓到", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J6 静态：可视区域截图必须带 no_fallback（形状守卫） ───────────
    try:
        src = src_safe("main.py")
        has_param = re.search(r"no_fallback:\s*bool\s*=\s*False", src) is not None
        has_call = re.search(r"no_fallback\s*=\s*visible_only", src) is not None
        has_visible = re.search(r"visible_only\s*=\s*not\s+full_page\s+and\s+not\s+selector",
                                src) is not None
        r.ok("J6 静态：_call 有 no_fallback 形参，且可视区域截图传了它",
             has_param and has_call and has_visible,
             f"形参={'有' if has_param else '缺'} 传参={'有' if has_call else '缺'} "
             f"visible_only={'有' if has_visible else '缺'}")
    except Exception as e:
        r.ok("J6 静态：_call 有 no_fallback 形参，且可视区域截图传了它", False,
             f"{type(e).__name__}: {e}"[:160])
