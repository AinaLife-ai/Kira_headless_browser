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
    state = "code"
    quote = ""
    depth = 0          # 模板串里 `${...}` 的嵌套深度

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
                i += 2
                depth = 1
                while i < n and depth > 0:
                    c2 = src_text[i]
                    if c2 == "{":
                        depth += 1
                    elif c2 == "}":
                        depth -= 1
                        if depth == 0:
                            out.append("}")
                            i += 1
                            break
                    out.append(c2)
                    i += 1
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


def _judge_port_guard(proto_text: str) -> bool:
    """B1 的判据：buildWsUrl 里有明确的端口值域校验 **代码**。

    ⚠️ 判据要盯**校验表达式本身**（范围比较 / 数字正则），不能盯
    "端口无效" 这种**错误消息文本** —— 消息在字符串字面量里，
    一旦按"只认代码态"剥离就找不到了（那是**改过头**的自我误报）。
    反过来，只盯消息文本也不行：注释里写一句"端口无效"就能满足。
    正确做法是：剥掉注释（排除说明文字）后，在**代码**里找那个
    范围判断。
    """
    code = strip_js_noise(proto_text)
    # 形态：`Number(p) < 1 || Number(p) > 65535`（可能带括号/空白差异）
    has_lo = re.search(r'<\s*1\b', code) is not None
    has_hi = re.search(r'>\s*65535\b', code) is not None
    # 还要确认这两个比较出现在**同一行/邻近**（防止别处恰好有 <1）
    nearby = re.search(r'<\s*1\b[^\n]{0,80}>\s*65535\b', code) is not None
    return has_lo and has_hi and nearby


def run(r) -> None:
    section("A. USER_SCRIPT world 的 CSP 必须放行动态执行")

    cap = src_safe("browser-bridge/capabilities.js")
    proto = src_safe("browser-bridge/protocol.js")

    r.ok("A1 调了 userScripts.configureWorld",
         _judge_configure_world(cap) and re.search(r'configureWorld\s*\(', cap) is not None,
         "不调它 → eval/new Function 被默认 CSP 挡掉 → 执行 JS 静默不可用")
    # ⚠️ 判据必须**精确到独立关键字** ——
    #    `'wasm-unsafe-eval'` 里也含 `unsafe-eval` 这一串，
    #    用 `"unsafe-eval" in cap` 的话，真正的 `'unsafe-eval'` 被删掉
    #    也照样能过（被 wasm 那个关键字兜住了）。
    #    必须匹配**带引号的独立关键字** `'unsafe-eval'`。
    r.ok("A2 CSP 里放行了独立的 'unsafe-eval'（不是被 wasm 关键字兜住）",
         _judge_unsafe_eval(cap),
         "execJs 的包装器正是用 eval / new Function 跑的；"
         "只放行 wasm-unsafe-eval 是不够的")
    # ⚠️ 两个标记**都要先判存在**再 index/rindex ——
    #    只守 "ensureUserScriptWorld" 的话，`cap.rindex("userScripts.execute")`
    #    在标记被改名/重构掉后抛 ValueError，异常逃出 run() →
    #    run_all.py 记一条笼统失败，**A4~A6 与 B、C 段全不执行**。
    #    这正是本组检查自己在防的那类"整组中断"。
    _has_cfg = "ensureUserScriptWorld" in cap
    _has_exec = "userScripts.execute" in cap
    r.ok("A3 配置 world 发生在**执行之前**",
         _has_cfg and _has_exec
         and cap.index("ensureUserScriptWorld") < cap.rindex("userScripts.execute"),
         f"两个标记都存在={_has_cfg}/{_has_exec}；"
         "配置要早于首次执行，否则第一次调用必然失败")
    # ⚠️ 只看 **CSP 字符串字面量本身**，不看全文 ——
    #    注释里写着"不含 `unsafe-inline`"也会命中全文匹配（误报）。
    #    用正则把 `csp: "..."` 的引号内内容抠出来。
    import re as _re
    _m = _re.search(r'csp:\s*"([^"]*)"', cap) or \
         _re.search(r"csp:\s*'([^']*)'", cap)
    _csp = _m.group(1) if _m else ""
    r.ok("A4 CSP 里没有 unsafe-inline / 远端源（只放行动态执行）",
         bool(_csp)
         and "unsafe-inline" not in _csp
         and "http://" not in _csp and "https://" not in _csp,
         f"安全边界：只解决 eval 的问题，不扩大攻击面；CSP={_csp!r}")
    r.ok("A5 老版本没有 configureWorld 时不崩",
         "configureWorld" in cap and "return false" in cap,
         "探测性调用，缺 API 就走原路径让真实报错说话")
    r.ok("A6 被 CSP 挡下时给出可照做的提示",
         "unsafe-eval" in cap and "内容安全策略" in cap,
         "别把 EvalError 原文丢给用户")

    section("B. 端口值域校验")

    r.ok("B1 buildWsUrl 校验了端口范围",
         _judge_port_guard(proto),
         "否则 -1/70000/abc 会拼出非法 URL，报错只说 Invalid URL")
    # ⚠️ 同样两个标记都先判存在（与 A3 保持一致）——
    #    写成 `a < b if (x in s and y in s) else False` 虽然不抛错，
    #    但意图绕、容易在改动时改坏。显式短路更清楚。
    _hp = "端口无效" in proto
    _hr = "return `${scheme}" in proto
    r.ok("B2 校验发生在拼 URL **之前**",
         _hp and _hr
         and proto.index("端口无效") < proto.rindex("return `${scheme}"),
         f"两个标记都存在={_hp}/{_hr}")
    r.ok("B3 纯空白端口当作\"没填\"（回落默认值，而不是报错）",
         'String(port ?? "").trim()' in proto,
         '`"  "` 是真值，不 trim 就会被当成非法端口值')

    section("C. 反向：关口被拆掉要能被发现")

    # ① 把 configureWorld 整段删掉 —— 复用 A1/A3 的**同一条判据**看它变不变红。
    #    ⚠️ 不要写 `"configureWorld" not in stripped and "configureWorld" in cap`
    #       —— 那是对同一字符串做替换，前半必然成立（**重言式**），等于没测。
    stripped = cap.replace("configureWorld", "REMOVED_CONFIG_WORLD")
    r.ok("C1 删掉 configureWorld 后本检查能发现",
         _judge_configure_world(stripped) is False
         and _judge_configure_world(cap) is True,
         "在真源码上 True、在变形文本上 False → 判据有效")

    # ② CSP 里把 unsafe-eval 去掉 —— 判据（A2）必须能发现。
    #    ⚠️ 不能写成 `"unsafe-eval" not in no_eval and "unsafe-eval" in cap`
    #       —— 对同一个字符串做替换，前半必然成立；那是**重言式**，恒为真，
    #       等于没测。要真正把 A2 的判据跑在变形文本上。
    no_eval = cap.replace("'unsafe-eval'", "'self'")
    r.ok("C2 去掉 unsafe-eval 后本检查能发现",
         _judge_unsafe_eval(no_eval) is False
         and _judge_unsafe_eval(cap) is True,
         "在真源码上 True、在变形文本上 False → 判据有效")

    # ③ 端口校验删掉 —— 复用 B1 的**同一条判据**。
    #    ⚠️ 变形要改**判据盯的那个东西**（校验表达式），不是错误消息 ——
    #    改消息文本的话，判据看不到（它已不看消息），C3 会假红。
    no_port = proto.replace("Number(p) < 1 || Number(p) > 65535",
                            "false")
    r.ok("C3 删掉端口校验后本检查能发现",
         _judge_port_guard(no_port) is False
         and _judge_port_guard(proto) is True,
         "在真源码上 True、在变形文本上 False → 判据有效")


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
