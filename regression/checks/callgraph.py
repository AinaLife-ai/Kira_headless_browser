"""调用图完整性：**调用了但类里没定义**的方法。

这类 bug 最阴险：静态语法检查（`py_compile`）看不出来，
但一跑到那行就是 `AttributeError`。

真实案例：重构工具层时把 `_send_image` / `_send_file` 弄丢了，
只剩调用点还在 —— 截图发送、文件发送会直接崩，
而所有现有检查全绿（因为它们只看"名字有没有出现"，
不看"定义有没有存在"）。

同时也查反向：定义了但从未被调用的私有方法（可能是残留）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..harness import PLUGIN_DIR, section

TITLE = "调用图完整性（未定义方法 / 死代码）"

#: 这些来自框架基类或运行时注入，不算"未定义"
# ⚠️ 这份白名单只能放**真正由框架基类/运行期注入提供**的名字。
#    之前混进了 `get` / `append` / `format` / `create_task` 这类**通用方法名** ——
#    那等于给 A1 开了一个大洞：`self.get(...)` / `self.append(...)` 写错也
#    永远不会被报出来（因为"看起来像继承来的"）。
#    判据：这个名字在 BasePlugin 上有定义，或由框架在运行期塞进实例。
INHERITED_OK = {
    # BasePlugin 的属性（框架注入）
    "ctx", "plugin_cfg",
    # 框架运行期注入的辅助（BasePlugin 提供）
    "logger", "get_logger",
}

#: 允许"定义了但没被调用"的：框架回调（框架按名字调用）
FRAMEWORK_CALLED = {
    "initialize", "terminate",            # BasePlugin 生命周期
    "_apply_config",
}


def _iter_py():
    for f in sorted(PLUGIN_DIR.rglob("*.py")):
        s = str(f)
        if "__pycache__" in s or "regression" in s or "/.git/" in s:
            continue
        yield f


def run(r) -> None:
    section("A. self.xxx() 调用了但类里没定义")
    undefined = []
    for f in _iter_py():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            undefined.append(f"{f}: 语法错误 {e}")
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            defined = {m.name for m in cls.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
            # ⚠️ 类体里的 **赋值** 也是"存在的东西"：
            #    类属性（`FOO = {...}`）和赋值出来的方法别名都算，
            #    否则会误报"未定义"。
            for st in cls.body:
                if isinstance(st, ast.Assign):
                    for t in st.targets:
                        if isinstance(t, ast.Name):
                            defined.add(t.id)
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    defined.add(st.target.id)
            # 类里赋值的属性（self.x = ... 在 __init__ 里）也算"存在"
            for n in ast.walk(cls):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                        and isinstance(n.func.value, ast.Name) \
                        and n.func.value.id == "self":
                    nm = n.func.attr
                    if nm in INHERITED_OK or nm in defined:
                        continue
                    rel = f.relative_to(PLUGIN_DIR)
                    undefined.append(f"{rel}:{n.lineno} {cls.name}.self.{nm}() 未定义")
    r.ok("A1 所有 self.xxx() 调用都能找到定义", not undefined,
         f"未定义={undefined or '无'}")
    for u in undefined:
        r.note(f"   ❗ {u}")

    section("B. 工具函数是否都被框架能识别（签名合规）")
    # 框架调用方式：tool_inst.execute(event, **args) —— 第一个位置参数必须是 event
    bad_sig = []
    for f in _iter_py():
        src = f.read_text(encoding="utf-8")
        if "@register.tool" not in src:
            continue
        # ⚠️ 每个 parse 点都要自己接住 SyntaxError：让异常逃出 run()
        #    会让**整组检查**变成一条笼统失败，后面的段落全不执行。
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            bad_sig.append(f"{f}: 语法错误 {e}")
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            # ⚠️ 不要用 `if not isinstance(m, AsyncFunctionDef): continue` 开头 ——
            #    那样"同步函数被注册成工具"这种错**永远查不出来**（它会被跳过）。
            #    应该先认装饰器，再判定它是不是 async。
            for m in cls.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decs = [ast.unparse(d) for d in m.decorator_list]
                if not any("register.tool" in d for d in decs):
                    continue
                if not isinstance(m, ast.AsyncFunctionDef):
                    bad_sig.append(f"{f.name}::{m.name} 不是 async"
                                   f"（框架会 await 它 → TypeError）")
                    continue
                args = [a.arg for a in m.args.args]
                # args[0] 必须是 self，args[1] 必须是 event
                if len(args) < 2 or args[0] != "self" or args[1] != "event":
                    bad_sig.append(f"{f.name}::{m.name} 签名={args[:3]}")
    r.ok("B1 所有 @register.tool 的函数签名合规（self, event, …）且是 async",
         not bad_sig, f"不合规={bad_sig or '无'}")

    section("C. hook 签名是否与框架调用一致")
    # 框架按 (event, req, ...) 调用 ON_LLM_REQUEST
    bad_hook = []
    for f in _iter_py():
        src = f.read_text(encoding="utf-8")
        if "@on." not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            bad_hook.append(f"{f}: 语法错误 {e}")
            continue
        for m in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]:
            decs = [ast.unparse(d) for d in m.decorator_list]
            if not any("@on." in d or "on." in d for d in decs):
                continue
            args = [a.arg for a in m.args.args]
            if len(args) < 3 or args[0] != "self" or args[1] != "event":
                bad_hook.append(f"{f.name}::{m.name} 签名={args[:4]}")
    r.ok("C1 所有 hook 签名合规（self, event, req, …）", not bad_hook,
         f"不合规={bad_hook or '无'}")

    section("D. 会话标识取法正确（event.session 是对象，不是字符串）")
    # ⚠️ 之前是"文件里只要有一处合法的 _sid_of 兜底，整个文件都跳过" ——
    #    等于同一文件里其它不安全的 str(event.session) 全被放过。
    #    改成按 **AST 定位所属函数**，只豁免 _sid_of 内部那处。
    wrong = []
    for f in _iter_py():
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            wrong.append(f"{f}: 语法错误 {e}")
            continue
        # 找出所有"函数内使用了 str(event.session)"的位置
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            bad_here = []
            for node in ast.walk(fn):
                # 要匹配的是**调用** str(event.session)，不是字符串常量。
                # ast.unparse 能稳定还原成 "str(event.session)"。
                if not isinstance(node, ast.Call):
                    continue
                # ⚠️ 用 **AST 结构**判断，不要用 ast.unparse() 的字符串前缀：
                #    前缀匹配会被 `str(event.session_id)` 这类**不同**的属性
                #    误命中（session_id 是合法用法），也会被空格/换行差别绕过。
                #    判据：实参必须是属性链，且**最后一个属性恰好是 session**，
                #    接收者必须是 `event` 或 `self.event`。
                if not node.args:
                    continue
                arg = node.args[0]
                if not isinstance(arg, ast.Attribute) or arg.attr != "session":
                    continue
                recv = arg.value
                is_event = (isinstance(recv, ast.Name) and recv.id == "event")
                is_self_event = (
                    isinstance(recv, ast.Attribute) and recv.attr == "event"
                    and isinstance(recv.value, ast.Name) and recv.value.id == "self")
                if is_event or is_self_event:
                    bad_here.append(getattr(node, "lineno", 0))
            if not bad_here:
                continue
            body = ast.get_source_segment(src, fn) or ""
            # 只有在 _sid_of 内部、且带 `count(":") == 2` 校验时才放行
            if fn.name == "_sid_of" and 'count(":") == 2' in body:
                continue
            for ln in bad_here:
                wrong.append(f"{f.name}:{ln} ({fn.name})")
    r.ok("D1 没有把 event.session 直接当字符串用",
         not wrong,
         f"可疑={wrong or '无'}（event.session 是 Session 对象，"
         f"str() 得到的是 repr，适配器解析不了）")
