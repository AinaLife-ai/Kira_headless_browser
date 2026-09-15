"""回归测试共享的夹具：路径解析、桩注入、计数与假 Playwright。

这个文件是给**所有检查脚本**用的，改一处即可让全部检查适配新结构。

## 怎么加一个新检查

1. 在 ``regression/checks/`` 下新建 ``xxx.py``
2. 写一个 ``CHECKS = [(名称, 函数), ...]``，函数返回 ``(ok: bool, detail: str)``
3. 在 ``checks/__init__.py`` 的 ``ALL_CHECKS`` 里登记

跑全部：``python3 regression/run_all.py``
跑单个：``python3 regression/run_all.py 静态审计``（名称支持模糊匹配）

## 目录约定

插件根目录 = 这个文件的上两级（regression/ 就放在插件根下）。
可以用环境变量 ``KIRA_PLUGIN_DIR`` 覆盖，方便对别的副本跑同一套检查。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ─── 路径 ────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent          # regression/
DEFAULT_PLUGIN_DIR = HERE.parent                # 插件根目录
PLUGIN_DIR = Path(os.environ.get("KIRA_PLUGIN_DIR") or DEFAULT_PLUGIN_DIR).resolve()

STUBS_DIR = HERE / "stubs"
JS_DIR = HERE / "js"

EXT_DIR = PLUGIN_DIR / "browser-bridge"          # 浏览器扩展（在插件体内）
BACKENDS_DIR = PLUGIN_DIR / "backends"


def src(rel: str) -> str:
    """读插件里的一个文件（相对插件根）。"""
    return (PLUGIN_DIR / rel).read_text(encoding="utf-8")


def exists(rel: str) -> bool:
    return (PLUGIN_DIR / rel).is_file()


def load_json(rel: str):
    return json.loads(src(rel))


# 常用文件的快捷读取（脚本里重复率很高）
def main_src() -> str:
    return src("main.py")


def headless_src() -> str:
    return src("backends/headless_backend.py")


def extension_src() -> str:
    return src("backends/extension_backend.py")


def bridge_src() -> str:
    return src("bridge.py")


def schema() -> dict:
    return load_json("schema.json")


def manifest() -> dict:
    return load_json("manifest.json")


def ext_manifest() -> dict:
    return load_json("browser-bridge/manifest.json")


def ext_file(name: str) -> str:
    return src(f"browser-bridge/{name}")


# ─── 桩注入 ──────────────────────────────────────────────────────────

_stubs_installed = False


def install_stubs() -> None:
    """把 stubs/ 放进 sys.path，让插件能被 import（不需要真的 KiraAI）。

    幂等：重复调用没副作用。
    """
    global _stubs_installed
    if _stubs_installed:
        return
    p = str(STUBS_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
    # 插件包自身也要在 path 上（脚本会用 importlib 直接加载它的模块）
    if str(PLUGIN_DIR.parent) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR.parent))
    _stubs_installed = True


def load_module(dotted: str, path: Path, pkg: str, pkg_path: Path):
    """把插件里的一个模块按 ``pkg.dotted`` 的名字加载（处理相对导入）。

    用法::

        mod = load_module("protocol", PLUGIN_DIR / "protocol.py",
                          "kirabrowser", PLUGIN_DIR)
    """
    import importlib.util
    import types

    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(pkg_path)]
        sys.modules[pkg] = m

    full = f"{pkg}.{dotted}"
    spec = importlib.util.spec_from_file_location(full, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


# ─── 结果收集 ────────────────────────────────────────────────────────

class Report:
    """收集一个检查脚本里的断言结果。"""

    def __init__(self, title: str = ""):
        self.title = title
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.warned: list[str] = []
        self.data: dict = {}
        self.notes: list[str] = []

    def ok(self, name: str, passed: bool, detail: str = "") -> bool:
        (self.passed if passed else self.failed).append(name)
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))
        return passed

    def warn(self, name: str, detail: str = "") -> None:
        self.warned.append(name)
        print(f"  [WARN] {name}" + (f" -- {detail}" if detail else ""))

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  {text}")

    def metric(self, key: str, value) -> None:
        self.data[key] = value

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    def summary(self) -> str:
        parts = [f"PASS {len(self.passed)} / FAIL {len(self.failed)}"]
        if self.warned:
            parts.append(f"/ WARN {len(self.warned)}")
        return " ".join(parts)


def section(title: str) -> None:
    print(f"\n{title}")


# ─── 常用解析工具 ────────────────────────────────────────────────────

def tool_names(text: str) -> set[str]:
    """从源码里抽出注册的工具名。"""
    return set(re.findall(r'name="(browser_[a-z_]+)"', text))


#: 不属于「后端能力接口」的辅助方法（两后端不必都有）
NON_CAPABILITY = {"new_filename", "start"}


def backend_methods(text: str, public_only: bool = True) -> set[str]:
    """抽出一个后端**能力接口**的方法名。

    只看 ``async def`` —— 同步方法（如 ``new_filename``）属于辅助函数，
    不是路由器要调的能力，两边不必都有。
    """
    names = set(re.findall(r'async def ([a-z_]+)\(', text))
    if public_only:
        names = {n for n in names if not n.startswith("_")}
    return names - NON_CAPABILITY


def called_backend_methods(main_text: str) -> set[str]:
    """插件通过 _call(...) 调用的后端方法。"""
    return set(re.findall(r'await self\._call\("([a-z_]+)"', main_text))


# ─── 假 Playwright ───────────────────────────────────────────────────

def fake_playwright():
    """返回 stubs/playwright 的 STATS 与模块（用于生命周期断言）。"""
    install_stubs()
    import playwright.async_api as pw
    return pw, pw.STATS


def reset_stats(stats: dict) -> None:
    for k in stats:
        stats[k] = 0
