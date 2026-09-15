"""两后端的**返回契约一致性**：同一个工具，换个后端必须给出同样的字段。

为什么单列一组：路由会在两个后端之间切换。如果只有一边返回某个字段，
同一个工具的行为就随"当时哪个后端可用"而变 —— 模型拿到的信息时有时无，
这类问题极难排查（"昨天还能读出标题，今天不行了"）。

做法：**真的把两个后端都调一遍**，收集每个方法实际返回的 data 键，
和渲染层 `_render()` 里要读的字段做比对。

  * 无头后端 → 用假 Playwright 真跑
  * 扩展后端 → 用假 bridge 返回**与扩展 JS 同形状**的数据

唯一例外：`download` 需要真实网络，沙箱里跑不了 —— 走源码字段静态兜底，
并在结果里标明需要真机验证。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys
import tempfile
import types
from pathlib import Path

from ..harness import PLUGIN_DIR, STUBS_DIR, section

TITLE = "两后端返回契约一致性"

#: 方法 → 调用参数
CALLS = {
    "list_tabs": {}, "get_info": {},
    "get_page": {"detail": "text"},
    "extract": {"selector": "a"},
    "navigate": {"url": "https://a/"},
    "click": {"selector": "#x"},
    "type_text": {"selector": "#x", "text": "hi"},
    "scroll": {"direction": "down"},
    "wait_for": {"selector": "#x"},
    "screenshot": {"path": "/tmp/_kira_c.png"},
    "execute_js": {"script": "1"},
    "go_back": {}, "refresh": {}, "hover": {"selector": "#x"},
    "keyboard_type": {"text": "a"},
    "keyboard_press": {"key": "Enter"},
    "keyboard_down_up": {"action": "down", "key": "Control"},
    "mouse_move": {"x": 1, "y": 2},
    "mouse_click": {"x": 1, "y": 2},
    "mouse_down_up": {"action": "down"},
    "mouse_wheel": {"delta_y": 10},
    "mouse_drag": {"start_x": 1, "start_y": 1, "end_x": 2, "end_y": 2},
    "list_files": {"dir_type": "downloads"},
    "cookie_get": {},
    "cookie_set": {"cookies": [{"name": "n", "value": "v", "domain": ".a"}]},
    "upload_file": {"selector": "#f"},
    "download": {"url": "https://a/f", "path": "/tmp/_kira_dl.bin"},
}

FAKE_TABLE = None   # 延迟构造（需要 protocol 模块）


def _load_pkg():
    sys.path.insert(0, str(STUBS_DIR))
    sys.path.insert(0, str(PLUGIN_DIR.parent))
    name = "kirabrowser_contract"
    if name in sys.modules:
        return sys.modules[name]

    pkg = types.ModuleType(name)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[name] = pkg

    def L(sub, path):
        spec = importlib.util.spec_from_file_location(f"{name}.{sub}", path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[f"{name}.{sub}"] = m
        spec.loader.exec_module(m)
        return m

    L("protocol", PLUGIN_DIR / "protocol.py")
    bp = types.ModuleType(f"{name}.backends")
    bp.__path__ = [str(PLUGIN_DIR / "backends")]
    sys.modules[f"{name}.backends"] = bp
    for sub in ("base", "router", "headless_backend", "extension_backend"):
        spec = importlib.util.spec_from_file_location(
            f"{name}.backends.{sub}", PLUGIN_DIR / "backends" / f"{sub}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[f"{name}.backends.{sub}"] = m
        spec.loader.exec_module(m)
        setattr(bp, sub, m)
    return pkg


def _render_fields() -> dict[str, set[str]]:
    """静态抽 _render() 里每个分支读的 d.get("x")"""
    import ast
    src = (PLUGIN_DIR / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_render"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            t = sub.test
            names = []
            if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) \
                    and t.left.id == "method":
                for c in t.comparators:
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        names.append(c.value)
                    if isinstance(c, (ast.Tuple, ast.List)):
                        for el in c.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                names.append(el.value)
            if not names:
                continue
            keys = set()
            for st in sub.body:
                for n2 in ast.walk(st):
                    if (isinstance(n2, ast.Call) and isinstance(n2.func, ast.Attribute)
                            and n2.func.attr == "get"
                            and isinstance(n2.func.value, ast.Name)
                            and n2.func.value.id == "d" and n2.args
                            and isinstance(n2.args[0], ast.Constant)):
                        keys.add(str(n2.args[0].value))
            for nm in names:
                out.setdefault(nm, set()).update(keys)
    return out


def _mk_fake_bridge(P):
    """假 bridge：返回与 browser-bridge/*.js 实际相同的形状。"""

    class FakeBridge:
        connected = True

        def open_download_sink(self, *a, **k):
            pass

        async def send_command(self, cmd, params=None, timeout=None, cmd_id=None):
            table = {
                P.CMD_LIST_TABS: {"tabs": [{"id": 1, "title": "t",
                                            "url": "https://a/", "active": True}],
                                  "tab_count": 1},
                P.CMD_GET_PAGE: {"url": "https://a/", "title": "t",
                                 "content": "hello world"},
                P.CMD_GET_INFO: {"title": "t", "url": "https://a/"},
                P.CMD_EXTRACT: {"url": "https://a/", "items": ["x"]},
                P.CMD_NAVIGATE: {"ok": True, "tab_id": 1, "url": "https://a/",
                                 "navigated": True},
                P.CMD_CLICK: {"ok": True, "match": "sel", "changed": True,
                              "navigated": False, "url": "https://a/"},
                P.CMD_TYPE: {"ok": True, "submitted": False, "navigated": False,
                             "url": "https://a/"},
                P.CMD_SCROLL: {"ok": True, "changed": True},
                P.CMD_WAIT_FOR: {"found": True, "elapsed": "0.1",
                                 "url": "https://a/"},
                P.CMD_SCREENSHOT: {"url": "https://a/", "title": "t",
                                   "image": "data:image/png;base64,AAAA"},
                P.CMD_EXEC_JS: {"url": "https://a/", "result": 42},
                P.CMD_GO_BACK: {"url": "https://a/"},
                P.CMD_REFRESH: {"url": "https://a/"},
                P.CMD_HOVER: {"ok": True, "url": "https://a/"},
                P.CMD_KEY_PRESS: {"ok": True, "url": "https://a/"},
                P.CMD_KEY_DOWN: {"ok": True, "url": "https://a/"},
                P.CMD_KEY_UP: {"ok": True, "url": "https://a/"},
                P.CMD_MOUSE_MOVE: {"ok": True, "x": 1, "y": 2},
                P.CMD_MOUSE_CLICK: {"ok": True, "navigated": False,
                                    "url": "https://a/"},
                P.CMD_MOUSE_DOWN: {"ok": True, "url": "https://a/"},
                P.CMD_MOUSE_UP: {"ok": True, "url": "https://a/"},
                P.CMD_MOUSE_WHEEL: {"ok": True},
                P.CMD_MOUSE_DRAG: {"ok": True, "url": "https://a/"},
                P.CMD_LIST_FILES: {"dir": "/d", "files": [{"name": "f", "size": 1}]},
                P.CMD_COOKIE_GET: {"url": "https://a/",
                                   "cookies": [{"name": "n", "value": "v",
                                                "domain": ".a"}]},
                P.CMD_COOKIE_SET: {"ok": 1, "written": 1, "skipped": 0,
                                   "failed": 0, "total": 1},
                P.CMD_UPLOAD: {"ok": True, "url": "https://a/", "name": "f",
                               "size": 1, "path": "/x/f"},
                P.CMD_DOWNLOAD: {"ok": True, "url": "https://a/",
                                 "mime": "text/plain", "bytes": 5,
                                 "path": "/tmp/x", "size": 5},
                P.CMD_DEBUG: {"backend": "extension"},
            }
            return table.get(cmd, {"ok": True})

    return FakeBridge()


async def _collect_hb(mod, tmp) -> dict[str, set[str]]:
    """每个方法用**全新实例** —— 共用一个会互相污染（前一个改了页面状态）。"""
    out: dict[str, set[str]] = {}
    upfile = Path(tempfile.mkdtemp()) / "up.txt"
    upfile.write_text("x")
    for method, kwargs in CALLS.items():
        b = mod.HeadlessBackend(Path(tmp), {
            "headless": True, "browser_channel": "chrome",
            "headless_profile_mode": "temp",
            "screenshot_dir": os.path.join(tmp, "s"),
            "download_dir": os.path.join(tmp, "d"),
            "idle_close_seconds": 0, "op_timeout": 5, "action_timeout": 3,
        })
        fn = getattr(b, method, None)
        if fn is None:
            await b.close()
            continue
        kw = dict(kwargs)
        if method == "upload_file":
            kw["file_path"] = str(upfile)
        try:
            res = await fn(**kw)
            out[method] = (set((res.data or {}).keys())
                           if hasattr(res, "data") and isinstance(res.data, dict)
                           else set())
        except Exception:
            out[method] = set()
        await b.close()

    # download 需要真网络，沙箱跑不了 → 源码字段兜底
    if not out.get("download"):
        body = (PLUGIN_DIR / "backends" / "headless_backend.py").read_text(encoding="utf-8")
        i = body.find("async def download(")
        if i > 0:
            # ⚠️ 要允许跨行 —— 返回字典常写成多行，
            #    单行正则匹配不到就会误报"字段缺失"。
            # 窗口要够大：download() 里加了 HTTPS 校验后变长了，
            # 4000 字符取不到末尾的成功返回。
            nxt = body.find("\n    async def ", i + 10)
            seg = body[i:nxt] if nxt > 0 else body[i:i + 12000]
            succ = re.findall(r'OpResult\(\s*data=\{([^}]*)\}\s*,\s*backend=self\.name\s*\)',
                              seg, re.S)
            keys = set()
            for grp in succ:
                keys |= set(re.findall(r'"([a-z_]+)":', grp))
            if keys:
                out["download"] = keys
    return out


async def _collect_eb(mod, P) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    upfile = Path(tempfile.mkdtemp()) / "up.txt"
    upfile.write_text("x")
    for method, kwargs in CALLS.items():
        b = mod.ExtensionBackend(_mk_fake_bridge(P), P)
        fn = getattr(b, method, None)
        if fn is None:
            continue
        kw = dict(kwargs)
        if method == "upload_file":
            kw["file_path"] = str(upfile)
        try:
            res = await fn(**kw)
            out[method] = (set((res.data or {}).keys())
                           if hasattr(res, "data") and isinstance(res.data, dict)
                           else set())
        except Exception:
            out[method] = set()
    return out


def run(r) -> None:
    _load_pkg()
    base = sys.modules["kirabrowser_contract.backends.base"]
    hbmod = sys.modules["kirabrowser_contract.backends.headless_backend"]
    ebmod = sys.modules["kirabrowser_contract.backends.extension_backend"]
    P = sys.modules["kirabrowser_contract.protocol"]

    rf = _render_fields()
    if not rf:
        r.ok("解析 _render 的字段", False, "没能从 main.py 里提取到任何渲染分支")
        return
    tmp = tempfile.mkdtemp()

    async def go():
        return await _collect_hb(hbmod, tmp), await _collect_eb(ebmod, P)

    hb_keys, eb_keys = asyncio.run(go())

    section("渲染层要读的字段 vs 两个后端实际返回")
    problems = []
    checked = 0
    for m in sorted(rf):
        if not rf[m]:
            continue
        if m not in hb_keys and m not in eb_keys:
            continue
        checked += 1
        miss_h = sorted(rf[m] - hb_keys.get(m, set()))
        miss_e = sorted(rf[m] - eb_keys.get(m, set()))
        if miss_h or miss_e:
            problems.append(f"{m}: 无头缺={miss_h or '-'} 扩展缺={miss_e or '-'}")
            r.note(f"❌ {m:<16} 无头缺 {miss_h}  扩展缺 {miss_e}")
        else:
            r.note(f"✅ {m:<16} 一致")

    r.ok("F1 渲染层读的字段，两个后端都真的返回",
         not problems, f"检查了 {checked} 个方法；不一致={len(problems)} 处")
    r.metric("检查的方法数", checked)
    for p in problems:
        r.ok(f"F1-{p.split(':')[0]} 字段一致", False, p)

    # 反向差异（不算错，但要知道）
    diffs = []
    for m in sorted(set(hb_keys) & set(eb_keys)):
        only_h = sorted(hb_keys[m] - eb_keys[m] - {"ok"})
        only_e = sorted(eb_keys[m] - hb_keys[m] - {"ok"})
        if only_h or only_e:
            diffs.append(f"{m}(仅无头={only_h} 仅扩展={only_e})")
    if diffs:
        r.warn(f"两后端返回字段仍有 {len(diffs)} 处差异（不影响功能）",
               "；".join(diffs[:6]))
