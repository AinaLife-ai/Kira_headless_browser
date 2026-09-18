"""运行时行为：生命周期、路由、并发、内存。

用**真实插件源码 + 语义忠实的假 Playwright** 跑，所以能对生命周期做真断言
（例如「页面被关掉后能不能自愈」），而不需要真的装 Chromium。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from ..harness import (PLUGIN_DIR, ext_manifest, install_stubs,
                       src_safe)

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
    # ⚠️ 插件模块加载失败时**明确报出来**并停在这里：运行时行为检查
    #    全都建立在"能把插件 import 起来"之上（结构性前提）。
    #    重点是给出清楚的原因，而不是抛裸 ModuleNotFoundError。
    try:
        hbmod = _load_plugin()
    except Exception as e:
        r.ok("能加载插件后端模块（否则运行时检查无从谈起）", False,
             f"{type(e).__name__}: {e}")
        return
    # ⚠️ 桩缺失要**明确报因**再停 —— 否则 ModuleNotFoundError 会让本段
    #    后面的生命周期 / 路由 / 内存检查一条都不跑，报告上只有一句
    #    "未抛异常"（矩阵抓出来的第一处）。
    try:
        import playwright.async_api as pw
    except Exception as e:
        r.ok("Playwright 桩可用（本组检查的前提）", False,
             f"{type(e).__name__}: {e} —— 缺少 regression/stubs/playwright/")
        return
    STATS = pw.STATS

    def reset():
        for k in STATS:
            STATS[k] = 0

    # 用 TemporaryDirectory：正常结束与异常退出都会清理，
    # 不会在系统临时目录里堆一堆 kira_reg_* 残留。
    _tmpdir = tempfile.TemporaryDirectory(prefix="kira_reg_")
    tmp = _tmpdir.name
    _ = _tmpdir      # 持有引用，别被 GC 提前回收

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
        # ⚠️ 先判启动是否成功再解引用 p._page —— 启动失败时 _page 是 None，
        #    这里会抛 AttributeError，把"启动失败"伪装成一条无关的崩溃。
        err = await p.start()
        if err or not p.available or p._page is None:
            return False, f"浏览器未能启动（{err or 'available/_page 不可用'}）"
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
        # ⚠️ start() 失败时返回错误字符串、p.available 会是 False ——
        #    若忽略它，下面的 "closed = not p.available" 会把**启动失败**
        #    误报成"空闲清理成功"（假绿）。
        err = await p.start()
        if err or not p.available:
            return False, f"浏览器未能启动（{err or 'available=False'}）"
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
    # 两类各 5 张，并**显式区分 mtime** —— 同一 tick 内创建的文件 mtime 相同，
    # 排序结果会取决于文件名，导致"剩哪几张"随机、断言忽过忽不过。
    _now = time.time()
    for i in range(5):
        fp = os.path.join(p.screenshot_dir, f"screenshot_{i}.png")
        with open(fp, "wb") as f:
            f.write(b"x")
        os.utime(fp, (_now - 100, _now - 100))     # 普通截图调旧
    for i in range(5):
        fp = os.path.join(p.screenshot_dir, f"element_{i}.png")
        with open(fp, "wb") as f:
            f.write(b"x")
        os.utime(fp, (_now, _now))                 # 元素截图最新
    p._clean_screenshots()
    left = os.listdir(p.screenshot_dir)
    n_elem = len([f for f in left if f.startswith("element_")])
    n_shot = len([f for f in left if f.startswith("screenshot_")])
    # 本次构造：element_ 最新 → 留下的应当**正好是 3 张 element_**。
    r.ok("R4 元素截图参与清理（到上限 + 全留最新的 element_）",
         len(left) == 3 and n_elem == 3 and n_shot == 0,
         f"总计 {len(left)}（期望 3）；element_* {n_elem}（期望 3）；"
         f"screenshot_* {n_shot}（期望 0）")

    # ⚠️ 反过来再跑一次：screenshot_ 最新 → 应留下 3 张 screenshot_。
    #    只测一个方向的话，"永远优先保留 element_"的 bug 也能通过；
    #    两个方向都测才能证明是**按时间**清理。
    reset()
    p = _mk(hbmod, tmp, screenshot_max_count=3)
    os.makedirs(p.screenshot_dir, exist_ok=True)
    _t2 = time.time()
    for i in range(5):
        fp = os.path.join(p.screenshot_dir, f"element_{i}.png")
        with open(fp, "wb") as f:
            f.write(b"x")
        os.utime(fp, (_t2 - 100, _t2 - 100))       # 元素截图调旧
    for i in range(5):
        fp = os.path.join(p.screenshot_dir, f"screenshot_{i}.png")
        with open(fp, "wb") as f:
            f.write(b"x")
        os.utime(fp, (_t2, _t2))                   # 普通截图最新
    p._clean_screenshots()
    left2 = os.listdir(p.screenshot_dir)
    n_elem2 = len([f for f in left2 if f.startswith("element_")])
    n_shot2 = len([f for f in left2 if f.startswith("screenshot_")])
    r.ok("R4b 反向：screenshot_ 最新时留下 3 张 screenshot_（证明按时间而非按类型）",
         len(left2) == 3 and n_shot2 == 3 and n_elem2 == 0,
         f"总计 {len(left2)}（期望 3）；element_* {n_elem2}（期望 0）；"
         f"screenshot_* {n_shot2}（期望 3）")

    # ── R5 下载目录清理 ─────────────────────────────────────────────
    p = _mk(hbmod, tmp, download_max_count=5)
    os.makedirs(p.download_dir, exist_ok=True)
    for i in range(20):
        with open(os.path.join(p.download_dir, f"f{i}.bin"), "wb") as f:
            f.write(b"y")
    p._clean_downloads()
    _left = len(os.listdir(p.download_dir))
    # 精确断言：设了上限 5 就应**恰好**保留 5。
    # 用 <= 6 会同时放过"多留一个"和"删多了"两种错误。
    r.ok("R5 下载目录自动清理（恰好保留到上限）",
         _left == 5, f"剩 {_left} 个，期望 5（原来 20 个从不清理）")

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

    # ── R9 页面操作串行化 ───────────────────────────────────────────
    hbsrc = src_safe("backends/headless_backend.py")
    r.ok("R9 页面操作有独立互斥锁（并发工具调用不会互相踩）",
         "_op_lock" in hbsrc and "async with self._op_lock" in hbsrc,
         "模型一轮里并发调 navigate+click 时，共用同一张页面")

    # ── R9b 浏览器版本号：setup_guide 必须与 manifest 一致 ───────────
    # ⚠️ 这条一直没人盯，漂了十几轮才被外部审查发现：
    #    manifest 的 minimum_chrome_version 从 120 提到 135，
    #    而 setup_guide 还在告诉用户"Chrome 120+ 可以" →
    #    用户在 128 上照着装，浏览器**直接拒绝加载扩展**，
    #    且失败提示不会提到版本，根本无从排查。
    #
    # ⚠️ 只看 `compatibility_report()` 里那句**兼容性声明** ——
    #    setup_guide 里另有一处 "Chrome 138+（userScripts 开关默认关闭）"
    #    讲的是另一件事，不能拿来跟 manifest 比。
    # ⚠️ 用 ext_manifest()（内部走 load_json_safe）——
    #    裸 open() 在 `browser-bridge/manifest.json` 缺失时抛
    #    FileNotFoundError，会**中断整组**（矩阵抓出来的第四处）。
    _ext_mf = ext_manifest()
    _min_chrome = str((_ext_mf or {}).get("minimum_chrome_version") or "").strip()
    _sg_src = src_safe("setup_guide.py")
    _cr = ""
    if "def compatibility_report(" in _sg_src:
        _cr = _sg_src.split("def compatibility_report(")[1]
        # 到下一个顶层 def 或文件尾
        for _stop in ("\ndef ", "\nclass "):
            if _stop in _cr:
                _cr = _cr.split(_stop)[0]
    _sg_ver = re.findall(r"Chrome\s+(\d+)\+", _cr)
    r.ok("R9b 扩展兼容性声明的 Chrome 版本与 manifest 一致",
         bool(_min_chrome) and bool(_sg_ver)
         and all(v == _min_chrome for v in _sg_ver),
         f"manifest minimum_chrome_version={_min_chrome}；"
         f"compatibility_report 里写的={_sg_ver or '没写'}")

    # ── R10 扩展 scroll 用瞬时行为 ──────────────────────────────────
    # ⚠️ 用 src_safe 而不是裸 open() —— 缺文件时抛 FileNotFoundError 会
    #    **中断整组**，R8/R9/R11 这些本该跑的检查一条都不执行，
    #    报告上只剩一句笼统的"未抛异常"。
    #    （"逐个删文件跑全套"矩阵抓出来的第三处。）
    cjs = src_safe("browser-bridge/content.js")
    # ⚠️ 先判断分隔符在不在 —— 处理器被删/改名时 split 会 IndexError，
    #    那会直接中断整组检查、把后面的断言全跳过（假绿）。
    if "scroll(payload) {" not in cjs:
        r.ok("R10 扩展 scroll 用瞬时滚动", False,
             "content.js 里找不到 scroll(payload) 处理器（被删或改名了？）")
        seg = ""
    else:
        seg = cjs.split("scroll(payload) {")[1][:1200]
    # 只看真正的代码，注释里提到 smooth 是正常的（说明为什么不用它）
    seg_code = "\n".join(ln for ln in seg.splitlines()
                         if not ln.strip().startswith(("//", "*", "/*")))
    if seg:
        r.ok("R10 扩展 scroll 用瞬时滚动（smooth 是异步的会读到旧位置）",
             'behavior: "auto"' in seg_code and "smooth" not in seg_code,
             "且返回真实位移而不是无条件 changed:true")

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
