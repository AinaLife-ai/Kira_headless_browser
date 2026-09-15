"""运行时行为：生命周期、路由、并发、内存。

用**真实插件源码 + 语义忠实的假 Playwright** 跑，所以能对生命周期做真断言
（例如「页面被关掉后能不能自愈」），而不需要真的装 Chromium。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from ..harness import PLUGIN_DIR, STUBS_DIR, install_stubs, section

TITLE = "运行时行为（生命周期 / 路由 / 内存）"


# ─── 加载真实插件 ────────────────────────────────────────────────────

def _load_plugin():
    install_stubs()
    if str(PLUGIN_DIR.parent) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR.parent))

    import types
    pkg_name = "kirabrowser_rt"
    if pkg_name not in sys.modules:
        m = types.ModuleType(pkg_name)
        m.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg_name] = m

    def load(name, path):
        spec = importlib.util.spec_from_file_location(f"{pkg_name}.{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    from core.plugin import logger as _noop  # noqa: F401
    load("setup_guide", PLUGIN_DIR / "setup_guide.py")
    load("protocol", PLUGIN_DIR / "protocol.py")
    load("security", PLUGIN_DIR / "security.py")
    load("backends", PLUGIN_DIR / "backends" / "__init__.py")
    load("backends.base", PLUGIN_DIR / "backends" / "base.py")
    load("backends.router", PLUGIN_DIR / "backends" / "router.py")
    load("backends.headless_backend", PLUGIN_DIR / "backends" / "headless_backend.py")
    return load("backends.headless_backend", PLUGIN_DIR / "backends" / "headless_backend.py")


class _Ctx:
    def __init__(self, d):
        self._d = d
        self.adapter_mgr = SimpleNamespace(get_adapter=lambda n: None)

    def get_plugin_data_dir(self):
        return Path(self._d)


def _mk(mod, tmp, **cfg):
    base = {
        "headless": True,
        "browser_channel": "chrome",
        "headless_profile_mode": "temp",
        "screenshot_dir": os.path.join(tmp, "shots"),
        "download_dir": os.path.join(tmp, "dl"),
        "timeout": 30,
        "op_timeout": 5,
        "action_timeout": 3,
        "idle_close_seconds": 0,
    }
    base.update(cfg)
    return mod.HeadlessBackend(Path(tmp), base)


def run(r) -> None:
    hbmod = _load_plugin()
    import playwright.async_api as pw
    STATS = pw.STATS

    def reset():
        for k in STATS:
            STATS[k] = 0

    tmp = tempfile.mkdtemp(prefix="kira_reg_")

    # ── R1 页面失效后自愈 ────────────────────────────────────────────
    async def r1():
        reset()
        p = _mk(hbmod, tmp)
        err = await p.start()
        if err:
            return False, err
        page = p._page
        page._die_by_itself()                 # 模拟用户关掉标签页
        err = await p._ensure_page()
        if err:
            return False, f"未自愈: {err}"
        alive = await p._page_alive()
        return alive, f"页面已自愈（新页面 = {p._page is not page}）"

    ok, detail = asyncio.run(r1())
    r.ok("R1 页面被关掉后能自愈", ok, detail)

    # ── R2 弹窗回收 ─────────────────────────────────────────────────
    async def r2():
        reset()
        p = _mk(hbmod, tmp)
        await p.start()
        for _ in range(5):
            p._page._open_popup("https://ad.example/")
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        n = len(p._context.pages)
        await p.close()
        return n <= 2, f"上下文里剩 {n} 张页面（不回收会是 6 张）"

    ok, detail = asyncio.run(r2())
    r.ok("R2 弹窗/新标签被回收", ok, detail)

    # ── R3 空闲自动关闭 ─────────────────────────────────────────────
    async def r3():
        reset()
        p = _mk(hbmod, tmp, idle_close_seconds=1)
        await p.start()
        p.idle_close_seconds = 1
        p._last_used = 0
        try:
            await asyncio.wait_for(p._idle_watchdog(), timeout=15)
        except asyncio.TimeoutError:
            pass
        closed = not p.available
        return closed, f"空闲后浏览器已关闭={closed}"

    ok, detail = asyncio.run(r3())
    r.ok("R3 空闲后自动关闭（释放 CPU/内存）", ok, detail)

    # ── R4 元素截图参与清理 ─────────────────────────────────────────
    reset()
    p = _mk(hbmod, tmp, screenshot_max_count=3)
    os.makedirs(p.screenshot_dir, exist_ok=True)
    for pre in ("element", "screenshot"):
        for i in range(10):
            with open(os.path.join(p.screenshot_dir, f"{pre}_{i}.png"), "wb") as f:
                f.write(b"x")
    p._clean_screenshots()
    left = os.listdir(p.screenshot_dir)
    n_elem = len([f for f in left if f.startswith("element_")])
    r.ok("R4 元素截图参与清理", n_elem <= 1, f"element_*.png 剩 {n_elem} 张")

    # ── R5 下载目录清理 ─────────────────────────────────────────────
    p = _mk(hbmod, tmp, download_max_count=5)
    os.makedirs(p.download_dir, exist_ok=True)
    for i in range(20):
        with open(os.path.join(p.download_dir, f"f{i}.bin"), "wb") as f:
            f.write(b"y")
    p._clean_downloads()
    r.ok("R5 下载目录自动清理",
         len(os.listdir(p.download_dir)) <= 6,
         f"剩 {len(os.listdir(p.download_dir))} 个（原来 20 个从不清理）")

    # ── R6 CPU 参数构造 + 注入防护 ──────────────────────────────────
    hl = hbmod.build_launch_args(True)
    hf = hbmod.build_launch_args(False)
    bad = [a for a in hbmod.FORBIDDEN_ARGS if a in hl or a in hf]
    need = ["--disable-dev-shm-usage", "--disable-gpu",
            "--disable-software-rasterizer", "--renderer-process-limit=4",
            "--js-flags=--max-old-space-size=512"]
    miss = [a for a in need if a not in hl]
    r.ok("R6 无头启动参数：无反向参数 + 防护齐全",
         not bad and not miss,
         f"反向残留={bad or '无'}；缺={miss or '无'}；共 {len(hl)} 个参数")
    injected = hbmod.build_launch_args(
        True, extra=["--disable-renderer-backgrounding", "--custom-flag"])
    r.ok("R7 从外部注入的反向参数会被过滤掉",
         "--disable-renderer-backgrounding" not in injected
         and "--custom-flag" in injected)

    # ── R8 后端路由 ─────────────────────────────────────────────────
    rt = sys.modules.get("kirabrowser_rt.backends.router")
    if rt is None:
        spec = importlib.util.spec_from_file_location(
            "kirabrowser_rt.backends.router", PLUGIN_DIR / "backends" / "router.py")
        rt = importlib.util.module_from_spec(spec)
        sys.modules["kirabrowser_rt.backends.router"] = rt
        spec.loader.exec_module(rt)

    class FakeExt(rt.Backend):
        name, is_user_browser = "extension", True

        def __init__(self, up):
            self.up = up

        @property
        def available(self):
            return self.up

        @property
        def display(self):
            return "用户浏览器"

    class FakeHL(rt.Backend):
        name, is_user_browser = "headless", False

        @property
        def available(self):
            return True

        @property
        def display(self):
            return "无头浏览器"

    ext = FakeExt(True)
    router = rt.BackendRouter("auto")
    router.register(ext)
    router.register(FakeHL())
    a1 = router.active.name if router.active else None
    ext.up = False
    a2 = router.active.name if router.active else None
    strict = rt.BackendRouter("extension")
    strict.register(FakeExt(False))
    strict.register(FakeHL())
    a3 = strict.active
    r.ok("R8 路由：扩展优先，断开自动回退，strict 不偷偷降级",
         a1 == "extension" and a2 == "headless" and a3 is None,
         f"连上→{a1}；断开→{a2}；strict 未连接→{a3}")
