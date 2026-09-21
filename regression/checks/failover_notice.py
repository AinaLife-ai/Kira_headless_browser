"""后端切换：**只有显式说要换才换，中途绝不自动换**。

## 为什么有它（用户定的规矩，2026-09-22）

用户原话：
- "除了一开始使用用户浏览器不了，才会提示并直接降级使用无头，其他时候尤其是操作中，
   肯定不能回退或者直接切换。"
- "所有这种操作中肯定不能自动呀，那不就中途断掉出错了吗？"

起因是一个真事故：截图在扩展侧失败（窗口最小化）→ 老 `_call()` 会**接着试无头后端**
→ 无头拍的是**它自己那个浏览器**（常常是一张空白页）→ 照样返回「✅ 截图已保存」。
模型拿到一张空白图还以为那是用户的浏览器。中途换后端就是这个后果：
**换了操作对象，前面那步的结果也就断了。**

## 现在的规矩（这个检查逐条钉住）

1. **首选可用但这次调用失败 → 如实失败**，绝不试下一个；并且把
   "怎么显式换"告诉对方（`browser_backend`）。
2. 唯一例外：首选**从一开始就不可用**（没连上/没启动）→ 才落到下一个，
   而且**必须在结果里明说**（"两套独立的浏览器"）。
3. **显式切换入口** `browser_backend`：`status` 看当前用哪个；`use` 切换
   （extension / headless / auto）。
4. **用户把「后端策略」固定成某一类时，这个入口必须拒绝** —— 那是用户的决定。

做法：真造一个 `BrowserPlugin` 实例（`__new__`，不跑 `__init__`）+ 假后端，
**真调 `_call` / 真调工具**，看它们返回什么。带反向自检。
"""

from __future__ import annotations

import importlib
import re
import sys
import types

from ..harness import PLUGIN_DIR, src_safe

TITLE = "后端切换：只有显式能换，中途绝不自动换"


def _load_main():
    """按依赖顺序把插件模块挂到包下面加载，返回 main 模块（与 ext_update 同一套）。"""
    from ..harness import install_stubs, load_module
    install_stubs()
    pkg = "hb_failover_main"
    if pkg in sys.modules:
        return sys.modules.get(pkg + ".main")
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
    """够 `_call` / `browser_backend` 用的最小后端桩。"""

    #: 由 run() 注入真的 OpResult 类（走真类，不自己造形状）
    OpResult = None

    def __init__(self, name, display, ok=True, error="扩展挂了", available=True):
        self.name = name
        self.display = display
        self.available = available
        self.is_user_browser = name == "extension"
        self._ok = ok
        self._error = error
        self.calls = 0
        self.started = 0

    async def start(self):
        self.started += 1
        self.available = True
        return None

    async def _op(self, **kw):
        OpResult = FakeBackend.OpResult
        self.calls += 1
        if self._ok:
            return OpResult(data={"path": kw.get("path", "/tmp/x.png"),
                                  "url": "https://a/", "text": "页面正文"},
                            backend=self.name)
        return OpResult.fail(self._error, self.name)

    screenshot = _op
    get_page = _op


class FakeRouter:
    def __init__(self, strategy, backends):
        self._strategy = strategy
        self._ext, self._headless = backends

    def candidates(self):
        if self._strategy == "extension":
            return [self._ext] if self._ext else []
        if self._strategy == "headless":
            return [self._headless] if self._headless else []
        return [b for b in (self._ext, self._headless) if b]

    def by_name(self, name):
        for b in (self._ext, self._headless):
            if b is not None and b.name == name:
                return b
        return None

    @property
    def active(self):
        c = self.candidates()
        return c[0] if c else None

    def describe(self):
        return "假路由"

    def hint(self):
        return "没有可用的浏览器"


def _make_plugin(main_mod, strategy="auto", ext_ok=True, ext_available=True,
                 headless_ok=True):
    ext = FakeBackend("extension", "用户浏览器（Edge · 扩展 v1.5.3）",
                      ok=ext_ok, available=ext_available,
                      error="Failed to capture tab: image readback failed")
    headless = FakeBackend("headless", "无头浏览器", ok=headless_ok)
    obj = main_mod.BrowserPlugin.__new__(main_mod.BrowserPlugin)
    obj.router = FakeRouter(strategy, [ext, headless])
    obj.backend_strategy = strategy
    obj._backend_override = None
    obj._degrade_announced = False
    obj.return_page_after_write = False
    obj._setup_notified = True
    obj._setup_notice = ""
    obj.enabled = True
    return obj, ext, headless


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def run(r) -> None:
    from ..harness import section
    section("J. 后端切换：只有显式能换，中途绝不自动换")

    try:
        main_mod = _load_main()
        if main_mod is None:
            raise RuntimeError("加载不到 main 模块")
        FakeBackend.OpResult = importlib.import_module(
            "hb_failover_main.backends.base").OpResult
    except Exception as e:
        r.ok("J0 能加载插件 main（换后端判据所在模块）", False,
             f"{type(e).__name__}: {e}"[:160])
        return

    # ── J1 首选可用但失败 → 不许换（这是用户规矩的核心）────────────────
    try:
        obj, ext, headless = _make_plugin(main_mod, ext_ok=False)
        out = _run(obj._call("screenshot", path="/tmp/x.png"))
        ok = (headless.calls == 0 and not out.startswith("✅")
              and "没有" in out and "自动换后端" in out and "browser_backend" in out)
        r.ok("J1 首选可用但这次失败 → 如实失败，绝不试下一个后端",
             ok,
             f"无头被调用={headless.calls} 次；回话={out[:70]!r}")
    except Exception as e:
        r.ok("J1 首选可用但这次失败 → 如实失败，绝不试下一个后端", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J2 首选从一开始就不可用 → 才落到下一个，而且必须说出来 ──────────
    try:
        obj, ext, headless = _make_plugin(main_mod, ext_available=False)
        out = _run(obj._call("get_page", chars=100))
        ok = (headless.calls == 1 and out.startswith("ℹ️ 本次用的是")
              and "两套" in out and "（来源：无头浏览器）" in out)   # 渲染结果本身也要在
        r.ok("J2 首选从一开始不可用 → 降级到无头，并在结果里明说（两套浏览器）",
             ok, f"无头被调用={headless.calls} 次；回话={out[:70]!r}")
    except Exception as e:
        r.ok("J2 首选从一开始不可用 → 降级到无头，并在结果里明说（两套浏览器）", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J3 用户把策略固定成某一类 → bot 也换不动 ──────────────────────
    try:
        obj, ext, headless = _make_plugin(main_mod, strategy="extension", ext_ok=False)
        out = _run(obj._call("get_page", chars=100))
        tool_out = _run(obj.tool_backend(None, action="use", use="headless"))
        ok = (headless.calls == 0 and "固定" in out
              and tool_out.startswith("🚫") and obj._backend_override is None
              and headless.calls == 0)
        r.ok("J3 用户固定策略时：失败不换，且切换入口明确拒绝（那是用户的决定）",
             ok, f"无头被调用={headless.calls} 次；工具回话={tool_out[:60]!r}")
    except Exception as e:
        r.ok("J3 用户固定策略时：失败不换，且切换入口明确拒绝（那是用户的决定）", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J4 auto 下显式切换要真的生效，并且说清"这是运行时 + 另一套" ──────
    try:
        obj, ext, headless = _make_plugin(main_mod, headless_ok=True)
        headless.available = False          # 没启动 → 工具应把它拉起来
        tool_out = _run(obj.tool_backend(None, action="use", use="headless"))
        cands = obj._candidates()
        ok = (tool_out.startswith("✅") and "运行时" in tool_out and "独立的一套" in tool_out
              and cands and cands[0].name == "headless" and headless.started == 1)
        r.ok("J4 auto 下 browser_backend 显式切换：生效 + 说清运行时/另一套浏览器",
             ok, f"当前候选={[b.name for b in cands]}；回话={tool_out[:60]!r}")
    except Exception as e:
        r.ok("J4 auto 下 browser_backend 显式切换：生效 + 说清运行时/另一套浏览器", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J5 一切正常时不许有任何多余提示（别刷屏）──────────────────────
    try:
        obj, ext, headless = _make_plugin(main_mod, ext_ok=True)
        out = _run(obj._call("screenshot", path="/tmp/x.png"))
        r.ok("J5 首选成功时不许出现降级/切换提示（否则每轮都刷）",
             out.startswith("✅") and "ℹ️" not in out and "换后端" not in out,
             out[:60])
    except Exception as e:
        r.ok("J5 首选成功时不许出现降级/切换提示（否则每轮都刷）", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J6 反向自检：把"失败即换"的老行为打开，J1 必须被推翻 ─────────────
    try:
        obj, ext, headless = _make_plugin(main_mod, ext_ok=False)
        obj.allow_failover = True                    # ← 模拟老行为
        out = _run(obj._call("screenshot", path="/tmp/x.png"))
        old_behavior = (headless.calls >= 1 or out.startswith("✅"))
        r.ok("J6 反向自检：一旦允许「失败即换」，J1 的判据必须报红",
             old_behavior,
             f"打开后：无头被调用={headless.calls} 次、回话={out[:40]!r} —— 判据能抓到")
    except Exception as e:
        r.ok("J6 反向自检：一旦允许「失败即换」，J1 的判据必须报红", False,
             f"{type(e).__name__}: {e}"[:160])

    # ── J7 静态形状：策略开关、候选选择、切换工具都在；旧的按操作特例已删 ──
    try:
        m = src_safe("main.py")
        ext_src = src_safe("backends/extension_backend.py")
        checks = {
            "allow_failover 默认关": re.search(r"allow_failover\s*=\s*False", m) is not None,
            "有 _candidates()": "def _candidates(" in m,
            "有 browser_backend 工具": 'name="browser_backend"' in m,
            "旧的按操作特例已删（no_fallback）": "no_fallback" not in m,
            "整页截图提示显式切换": "browser_backend" in ext_src,
        }
        bad = [k for k, v in checks.items() if not v]
        r.ok("J7 静态：策略/候选/切换工具都在，旧的按操作特例已清掉",
             not bad, f"缺={bad or '无'}")
    except Exception as e:
        r.ok("J7 静态：策略/候选/切换工具都在，旧的按操作特例已清掉", False,
             f"{type(e).__name__}: {e}"[:160])
