"""启动 / 停止生命周期 —— **真跑一遍** `initialize()` 和 `terminate()`。

⚠️ 这一组补的是一个存在很久的口子：之前所有检查都是**静态扫描**或者
**直接调用工具函数**，没有一条真的执行过插件的启动路径。

于是 `main.py` 的 `initialize()` 里那行

    self.bridge.clear_event_listeners()      # BrowserBridge 里没有这个方法

一直没人发现 —— 直到用户真的加载插件，才以

    Failed to initialize plugin headless_browser:
    'BrowserBridge' object has no attribute 'clear_event_listeners'

的形式炸出来（**整个插件起不来**，不是某个功能坏了）。

静态检查确实能扫出这一类（本次也补了 callgraph **B1**：查协作者对象上的
方法调用），但"能把 `initialize()` 真跑通"是另一层保险 —— 它顺带覆盖
构造参数、配置读取、后端注册、引导文案这些**只有在启动时才会走到**的路径。

本组做两轮 init → terminate：
  ① 第一轮：启动路径不抛异常
  ② 第二轮：**热重载不累积事件回调**（这正是那个 bug 想做的事 ——
     原注释写着"先清再注册，热重载不会重复累积"，而方法根本没实现）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from ..harness import PLUGIN_DIR, section

TITLE = "启动 / 停止生命周期（冒烟）"

#: 在子进程里跑完整的生命周期。
#  用子进程而不是同进程 import：插件模块一旦进 `sys.modules` 就会污染
#  同一进程里其它检查（它们也各有各的插件加载方式），而且启动路径里
#  有全局状态（日志器、tokens），跑在干净进程里更接近真实加载。
_PROBE = r'''
import asyncio, json, os, sys, types, tempfile, traceback
from pathlib import Path

ROOT = os.environ["KIRA_PLUGIN_DIR"]
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "regression", "stubs"))
import regression.harness as h
h.install_stubs()

pkg = types.ModuleType("hb_lifecycle")
pkg.__path__ = [ROOT]
sys.modules["hb_lifecycle"] = pkg

out = {"load": None, "init1": None, "term1": None, "init2": None, "term2": None,
       "listeners1": None, "listeners2": None, "router": None}

try:
    import hb_lifecycle.main as M
    out["load"] = "ok"
except Exception as e:
    out["load"] = f"{type(e).__name__}: {e}"
    print("RESULT:" + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


class FakeConfig:
    def get_config(self, key, default=None):
        return {"bot_config.agent.tool_call_timeout": 60.0}.get(key, default)


class FakeCtx:
    def __init__(self, d):
        self._d = d
        self.config = FakeConfig()

    def get_plugin_data_dir(self):
        return Path(self._d)


def run_lifecycle(plugin):
    """跑一遍 setup → teardown。"""
    asyncio.run(plugin.initialize())
    asyncio.run(plugin.terminate())


def main():
    tmp = tempfile.mkdtemp(prefix="smoke_")
    cfg = {
        "enabled": True, "headless": True, "headless_profile_mode": "temp",
        "browser_channel": "chrome",
        "screenshot_dir": os.path.join(tmp, "shots"),
        "download_dir": os.path.join(tmp, "dl"),
        "timeout": 30, "op_timeout": 5, "action_timeout": 3,
        "idle_close_seconds": 0,
    }
    p = M.BrowserPlugin(FakeCtx(tmp), cfg)

    # ① 第一轮
    try:
        asyncio.run(p.initialize())
        out["init1"] = "ok"
        out["listeners1"] = sum(len(v) for v in p.bridge._event_listeners.values())
        out["router"] = (p.router.describe() if p.router else "")
    except Exception as e:
        out["init1"] = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc().strip().splitlines()
        out["init1_where"] = " | ".join(tb[-4:])[:300]
        print("RESULT:" + json.dumps(out, ensure_ascii=False))
        return
    try:
        asyncio.run(p.terminate())
        out["term1"] = "ok"
    except Exception as e:
        out["term1"] = f"{type(e).__name__}: {e}"

    # ② 第二轮：模拟热重载 —— 回调**不该累积**
    try:
        asyncio.run(p.initialize())
        out["init2"] = "ok"
        out["listeners2"] = sum(len(v) for v in p.bridge._event_listeners.values())
    except Exception as e:
        out["init2"] = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc().strip().splitlines()
        out["init2_where"] = " | ".join(tb[-4:])[:300]
        print("RESULT:" + json.dumps(out, ensure_ascii=False))
        return
    try:
        asyncio.run(p.terminate())
        out["term2"] = "ok"
    except Exception as e:
        out["term2"] = f"{type(e).__name__}: {e}"

    # ── 跨实例提醒：**平时一个字都不多**（这是它省 token 的关键）──
    class _R:
        def __init__(self, d):
            self.data = d
    out["note_silent"] = p._cross_actor_note(_R({})) == ""
    out["note_silent2"] = p._cross_actor_note(_R(None)) == ""
    out["note_silent3"] = p._cross_actor_note(_R({"url": "x"})) == ""
    _n = p._cross_actor_note(_R({"other_writer": "kira-b"}))
    out["note_fires"] = ("kira-b" in _n) and 0 < len(_n) < 120

    print("RESULT:" + json.dumps(out, ensure_ascii=False))


main()
'''


def _run(timeout: int = 180) -> dict:
    fd, path = tempfile.mkstemp(suffix="_lifecycle.py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_PROBE)
        env = dict(os.environ)
        env["KIRA_PLUGIN_DIR"] = str(PLUGIN_DIR)
        env["KIRA_FW_DIR"] = os.environ.get("KIRA_FW_DIR", "/tmp/kiraai_latest")
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           env=env, timeout=timeout, cwd=str(PLUGIN_DIR))
        line = next((ln for ln in (p.stdout or "").splitlines()
                     if ln.startswith("RESULT:")), "")
        if not line:
            return {"__error__": (p.stderr or p.stdout or "")[-400:]}
        return json.loads(line[len("RESULT:"):])
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run(r) -> None:
    section("L. 启动 / 停止生命周期（真跑 initialize）")
    res = _run()
    if "__error__" in res:
        r.ok("能跑完插件生命周期探针", False, res["__error__"][:200])
        return

    r.ok("插件模块能加载（import main）",
         res.get("load") == "ok", str(res.get("load"))[:200])
    if res.get("load") != "ok":
        return

    # ① 第一轮
    r.ok("initialize() 不抛异常（插件真正起得来）",
         res.get("init1") == "ok",
         f"{res.get('init1')}  {res.get('init1_where', '')}")
    if res.get("init1") != "ok":
        # 起不来就没必要继续查后面 —— 但要把"没查"说清楚，不能静默跳过
        r.ok("terminate() 不抛异常", False, "未执行（initialize 已失败）")
        r.ok("热重载再 initialize() 不影响", False, "未执行（initialize 已失败）")
        return

    r.ok("initialize() 之后 router 已装配",
         bool(res.get("router")), f"router={res.get('router')!r}")
    r.ok("terminate() 不抛异常", res.get("term1") == "ok",
         str(res.get("term1"))[:200])

    # ② 第二轮（热重载）
    r.ok("热重载再 initialize() 不抛异常", res.get("init2") == "ok",
         f"{res.get('init2')}  {res.get('init2_where', '')}")

    # 跨实例提醒：**平时一个字都不多**（这是它省 token 的关键）
    for _k, _d in (
        ("note_silent", "没有别的实例操作时不加任何提示（零 token 开销）"),
        ("note_silent2", "data 为 None 时也不炸、不加提示"),
        ("note_silent3", "其它字段不会误触发提示"),
        ("note_fires", "别的实例操作过 → 追加一句短提示"),
    ):
        r.ok(f"L.{_k} {_d}", res.get(_k) is True, f"结果={res.get(_k)!r}")
    r.ok("terminate()（第二轮）不抛异常", res.get("term2") == "ok",
         str(res.get("term2"))[:200])

    # 回调不该累积 —— 这正是 `clear_event_listeners` 存在的理由
    n1, n2 = res.get("listeners1"), res.get("listeners2")
    r.ok("热重载后事件回调不累积（clear_event_listeners 真的在起作用）",
         isinstance(n1, int) and isinstance(n2, int) and n1 == n2 and n1 > 0,
         f"第一轮={n1} 第二轮={n2}（应相等且 >0；变大说明每次 init 都在叠加）")
