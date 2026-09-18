"""回归测试共享的夹具：路径解析、桩注入、计数与假 Playwright。

这个文件是给**所有检查脚本**用的，改一处即可让全部检查适配新结构。

## 怎么加一个新检查

1. 在 ``regression/checks/`` 下新建 ``xxx.py``
2. 模块里写两个东西::

       TITLE = "我的检查"          # 显示名（也是 run_all 的筛选关键字）
       def run(r) -> None:         # r 是 Report 实例
           r.ok("某项成立", condition, "细节")
           r.warn("某项跳过", "原因")     # 环境缺依赖之类，不算失败
           r.metric("指标名", 数值)        # 可选，会汇总进报告

3. 在 ``checks/__init__.py`` 的 ``ALL_CHECKS`` 里登记一行

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
# ⚠️ JS_DIR 必须跟着 PLUGIN_DIR 走，否则 KIRA_PLUGIN_DIR 指向别的副本时：
#    检查脚本（upload_stream.mjs）从**默认目录**读，而被测的 content.js
#    从**指定副本**读 —— 两棵树混着用，反向验证全部假绿。
#    （HERE 永远是本套件所在目录，不受 KIRA_PLUGIN_DIR 影响。）
JS_DIR = PLUGIN_DIR / "regression" / "js"
if not JS_DIR.is_dir():
    JS_DIR = HERE / "js"

EXT_DIR = PLUGIN_DIR / "browser-bridge"          # 浏览器扩展（在插件体内）
BACKENDS_DIR = PLUGIN_DIR / "backends"


def src(rel: str) -> str:
    """读插件里的一个文件（相对插件根）。"""
    return (PLUGIN_DIR / rel).read_text(encoding="utf-8")


def src_safe(rel: str) -> str:
    """同 :func:`src`，但文件缺失时返回空串而不是抛异常。

    ⚠️ 用途：某些检查是"先读文件、再按内容断言"。
    直接 `src()` 在文件被删/改名时会抛 FileNotFoundError，
    **整个检查组就此中断** —— 后面所有用例都不执行，
    报告上只留一条笼统失败，看不出真正缺了什么。

    用这个变体可以让检查**继续跑完**，并把它自己的 FAIL 记清楚
    （例如"__init__.py 缺失"这种本就要报出来的状态）。

    ⚠️ 只吞 **FileNotFoundError**（以及目录缺失导致的 NotADirectoryError）——
    这两种是"文件不在"的确定性状态，检查自己会报出来。
    **权限错误 / 编码错误等要抛出去**：把那些也吞掉的话，
    检查会拿着空串去断言，把"读不了文件"伪装成"文件内容不对"，
    排查时会往完全错误的方向走。
    """
    try:
        return (PLUGIN_DIR / rel).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return ""


def _copy_interp_body(text: str, i: int, out: list) -> int:
    """把模板插值体（`${` 之后到配对 `}`）原样抄进 *out*，返回闭合 `}` 之后的下标。

    ⚠️ 插值体里是**真实 JS**，所以 `}` 不一定是插值的结束 —— 它可能出现在：

      · **字符串字面量**里：`` `${"}"; socket.readyState}` ``
      · 转义之后：`` `${"\\"}` ``
      · **注释**里：`` `${/* } */ x}` ``
      · **更深的嵌套模板**里：`` `${`${a}`}` ``

    只按 `{`/`}` 计数的话，`` `${"}"; socket.readyState}` `` 会在字符串里
    那个 `}` 处**提前收尾**，剩下的 `"; socket.readyState}` 被当成模板文本
    丢掉 —— 于是 `socket.readyState` 这个（真会抛 `ReferenceError` 的）
    裸引用**漏检**。这正是"检查器自己数错"的类型：不报错，只是悄悄少看一段。

    已知边界：**正则字面量**靠"前一个有效字符"的经典启发式区分
    （``/`` 紧跟 `(,=:[!&|?{};+-*%~^<>` 或位于表达式开头 → 正则），
    不解析 `in` / `of` / `return` 之类的关键字上下文 ——
    对"扫裸引用"这个用途足够，但不宣称是完整 JS 词法分析器。
    """
    depth = 1
    n = len(text)
    #: 前一个**有效字符**（跳过空白），用来判断 `/` 是正则还是除号。
    #: 插值体开头视为"表达式起点" → `/` 按正则处理。
    last_sig = ""
    while i < n and depth > 0:
        c = text[i]

        # ① 字符串 / 模板字面量：整段照抄，里面的括号不参与计数
        if c in "\"'`":
            out.append(c)
            i += 1
            while i < n:
                c2 = text[i]
                if c2 == "\\":                  # 转义：连下一个字符一起抄
                    out.append(c2)
                    i += 1
                    if i < n:
                        out.append(text[i])
                        i += 1
                    continue
                if c == "`" and c2 == "$" and i + 1 < n and text[i + 1] == "{":
                    # 嵌套模板里的插值：递归处理（多层嵌套也不会数错）
                    out.append("${")
                    i = _copy_interp_body(text, i + 2, out)
                    continue
                out.append(c2)
                i += 1
                if c2 == c:
                    break
            last_sig = c
            continue

        # ② 注释：里面的括号同样不算
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(text[i])
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            out.append("/*")
            i += 2
            while i < n:
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    out.append("*/")
                    i += 2
                    break
                out.append(text[i])
                i += 1
            continue

        # ③ 正则字面量：`/}/` 里的 `}` 也不是插值结束
        if c == "/" and (last_sig == "" or last_sig in "(,=:[!&|?{};+-*%~^<>"):
            out.append(c)
            i += 1
            in_class = False
            while i < n:
                c2 = text[i]
                if c2 == "\\":
                    out.append(c2)
                    i += 1
                    if i < n:
                        out.append(text[i])
                        i += 1
                    continue
                if c2 == "[":
                    in_class = True
                elif c2 == "]":
                    in_class = False
                elif c2 == "/" and not in_class:
                    out.append(c2)
                    i += 1
                    break
                elif c2 == "\n":                # 未闭合 → 当作除号，回退
                    break
                out.append(c2)
                i += 1
            last_sig = "/"
            continue

        # ④ 括号计数
        if c == "{":
            depth += 1
            out.append(c)
            i += 1
            last_sig = "{"
            continue
        if c == "}":
            depth -= 1
            out.append(c)
            i += 1
            if depth == 0:
                return i
            last_sig = "}"
            continue

        out.append(c)
        if not c.isspace():
            last_sig = c
        i += 1

    return i


def strip_js_noise(src_text: str) -> str:
    """剥掉 JS 的注释，保留字符串与模板串中 `${...}` 的真实代码。

    ⚠️ **不能用"先剥行注释再剥字符串"的正则顺序** —— `//` 出现在
    字符串里极其常见（`"https://..."`、`"ws://127.0.0.1:5267/ws"`），
    那种顺序会把**从 `//` 起的整行**都当注释吃掉：
    真实代码被吞掉，后续的"裸标识符"检测就失真了（可能漏报）。

    块注释 `/* */` 同理：字符串里的 `/*` 也会被误当注释起点。

    所以改成**一次扫描的状态机**：按字符走，维护"当前在什么里"，
    注释只在**代码态**才是注释。这也正是 CR 建议的 state-aware scanner。

    保留语义：模板串里的 `${...}` 内容原样留下（那是真实求值的代码），
    其余字符串内容清空（运行时才存在的文本，不是变量引用）。
    """
    out = []
    i = 0
    n = len(src_text)
    # 状态：code / line_comment / block_comment / str(' or ") / tmpl(`)
    #  ⚠️ 模板串里的 `${...}` **不在这里处理** —— 插值体是真实 JS，
    #     要按词法逐个跳过字符串/注释/嵌套模板（见 `_copy_interp_body`）。
    state = "code"
    quote = ""

    while i < n:
        ch = src_text[i]
        nxt = src_text[i + 1] if i + 1 < n else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch in ("'", '"'):
                state = "str"
                quote = ch
                i += 1
                continue
            if ch == "`":
                state = "tmpl"
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        if state == "line_comment":
            if ch == "\n":
                state = "code"
                out.append(ch)          # 换行保留，避免把两行粘一起
            i += 1
            continue

        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                # 用空格顶替，避免把前后 token 粘成一个
                out.append(" ")
                continue
            if ch == "\n":
                out.append(ch)
            i += 1
            continue

        if state == "str":
            if ch == "\\":              # 转义：跳过下一个字符
                i += 2
                continue
            if ch == quote:
                state = "code"
                out.append(quote)       # 保留空引号对，占位
            i += 1
            continue

        if state == "tmpl":
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                state = "code"
                out.append("`")
                i += 1
                continue
            if ch == "$" and nxt == "{":
                # `${...}` 内部是**真实代码** —— 原样保留并嵌套计数
                out.append("${")
                i = _copy_interp_body(src_text, i + 2, out)
                continue
            i += 1
            continue

    return "".join(out)


def strip_comments_only(src_text: str) -> str:
    """只剥注释、**保留字符串字面量原样**。

    与 harness 的 `strip_js_noise` 不同：那个会把字符串内容清空
    （用于"找标识符引用"），而这里要**读配置字符串的值**。
    """
    out = []
    i = 0
    n = len(src_text)
    state = "code"
    delim = ""
    while i < n:
        c = src_text[i]
        nxt = src_text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line_comment"; i += 2; continue
            if c == "/" and nxt == "*":
                state = "block_comment"; i += 2; continue
            if c in "\"'`":
                state = "str"; delim = c
                out.append(c); i += 1; continue
            out.append(c); i += 1
        elif state == "line_comment":
            if c == "\n":
                state = "code"; out.append(c)
            i += 1
        elif state == "block_comment":
            if c == "*" and nxt == "/":
                state = "code"; i += 2; continue
            i += 1
        else:  # str
            out.append(c)
            if c == "\\" and nxt:
                out.append(nxt); i += 2; continue
            if c == delim:
                state = "code"
            i += 1
    return "".join(out)


def exists(rel: str) -> bool:
    return (PLUGIN_DIR / rel).is_file()


def load_json_safe(rel: str):
    """读 JSON；文件缺失或不是合法 JSON 时返回 **{}** 而不是抛异常。

    ⚠️ 这些是**前置读取**（检查开始前先把几个文件读进来）。
    一个文件缺失时直接抛异常的话，**整个检查组就此中断** ——
    报告上只剩一条笼统失败，后面所有段落都不执行，
    而"到底哪个文件缺了"反而看不出来。
    返回空值让各段各自报各自的失败，信息更全。
    """
    # ⚠️ 只吞**解析错误**。写成 `except Exception` 的话，权限错误 /
    #    编码错误（`src_safe` 是**有意**让这两种往外抛的）会被悄悄转成
    #    `{}`，然后报告上写"JSON 无效" —— 原因被换成了另一个原因，
    #    排查时会往完全错误的方向找。
    #    文件缺失由 `src_safe` 返回空串 → `json.loads("")` 抛的就是
    #    JSONDecodeError，照样走这里。
    try:
        return json.loads(src_safe(rel))
    except json.JSONDecodeError:
        return {}


# 常用文件的快捷读取（脚本里重复率很高）
# ⚠️ 一律用 **safe** 版本：前置读取抛异常会中断整组检查。
#    （值可能是空串/空 dict，各段自己判空并报自己的失败。）
def main_src() -> str:
    return src_safe("main.py")


def headless_src() -> str:
    return src_safe("backends/headless_backend.py")


def extension_src() -> str:
    return src_safe("backends/extension_backend.py")


def bridge_src() -> str:
    return src_safe("bridge.py")


def schema() -> dict:
    return load_json_safe("schema.json")


def manifest() -> dict:
    return load_json_safe("manifest.json")


def ext_manifest() -> dict:
    return load_json_safe("browser-bridge/manifest.json")


def ext_file(name: str) -> str:
    return src_safe(f"browser-bridge/{name}")


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
