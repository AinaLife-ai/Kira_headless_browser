"""「结果不确定」链路的完整守卫（页面操作超时 → 禁止换后端重试）。

## 为什么单独一组

页面操作超时（慢点击 / 慢输入）意味着**操作可能已经在浏览器里生效了**。
这时如果当成普通失败，上层会换（无头）后端**重试** ——
同一个点击/输入被执行两次，而这是不可撤销的。

这条链路横跨 4 个文件、3 种语言（JS 扩展 → WS 协议 → Python 桥 → 后端），
任何一环断掉都会**静默退化成"重复执行"**：

    shared.js          Promise.race 超时不再等，但**不会取消**已发出的操作
      ↓ 抛带 code=ERR_TIMEOUT 的 Error
    background.js      catch → sendResult(id, false, null, msg, e.code)
      ↓ WS 消息带 error_code 字段
    protocol.py        BridgeResult.from_wire 解出 error_code
      ↓
    bridge.py          包成 BridgeError 时把 err_code 带上去
      ↓
    extension_backend  err_code == ERR_TIMEOUT → OpResult.indeterminate_result
      ↓
    main.py            看到 indeterminate → 明说"可能已生效"，**不换后端**

⚠️ 最容易断的两环：
  * `err_code` 中途被吞掉 → 退化成靠 `"超时" in msg` **文案匹配**，
    改个提示文字/换语言就静默失效；
  * main.py 忘了看 `indeterminate` → 前面全对，最后还是重试。
"""
from __future__ import annotations

import re

from ..harness import section, src, src_safe

TITLE = "「结果不确定」链路（超时不得重复执行）"

#: 「后端识别超时」的判据。
#  ⚠️ 要同时接受**字面量**和**共享常量**两种写法 ——
#    插件侧已经改用 `protocol.ERR_TIMEOUT`（避免两边各写一个字面量），
#    判据只认 "timeout" 的话，一改常量就误报"识别逻辑不见了"。
#  ⚠️ `.*==` 中间还有东西（`getattr(e, "err_code", None) == self._P.ERR_TIMEOUT`），
#  所以判据是"出现 err_code…… 然后某个 == 比较的对象是超时标识"。
#  末尾用 `\b` 收口，避免 `ERR_TIMEOUT_X` 这类同前缀名字误命中。
_ERRCODE_PAT = re.compile(
    r'err_code[\s\S]{0,80}?==[\s\S]{0,40}?(?:"timeout"|ERR_TIMEOUT)\b')



def run(r) -> None:
    section("A. 链路各环节都在")

    shared = src_safe("browser-bridge/shared.js")
    proto_js = src_safe("browser-bridge/protocol.js")
    bg = src_safe("browser-bridge/background.js")
    proto_py = src_safe("protocol.py")
    bridge = src_safe("bridge.py")
    ext = src_safe("backends/extension_backend.py")
    main = src_safe("main.py")

    # ── JS 侧：超时错误带显式类别 ─────────────────────────────────────
    r.ok("A1 协议里有超时错误类别常量",
         "ERR_TIMEOUT" in proto_js and 'ERR_TIMEOUT = "timeout"' in proto_js,
         "别靠错误文案判断语义")
    r.ok("A2 callContent 的超时抛出带 code 的错误",
         "ERR_TIMEOUT" in shared and re.search(
             r"err\.code\s*=\s*ERR_TIMEOUT", shared) is not None,
         "Promise.race 只停止等待，**取消不了**已发出的操作")
    r.ok("A3 sendResult 会带上 error_code 字段",
         "error_code" in shared and "errorCode" in shared)
    r.ok("A4 background 把 e.code 传进 sendResult",
         re.search(r"sendResult\(\s*id\s*,\s*false[^;]*?e\s*&&\s*e\.code\s*\)",
                   bg, re.S) is not None,
         "不传的话类别在入口就丢了")

    # ── Python 侧：字段一路透传 ───────────────────────────────────────
    r.ok("A5 BridgeResult 解析 error_code",
         "error_code" in proto_py and "raw.get(\"error_code\")" in proto_py)
    r.ok("A6 BridgeError 携带 err_code 且被赋值",
         "err_code" in bridge and re.search(r"_err\.err_code\s*=", bridge)
         is not None)
    r.ok("A7 后端**优先**看显式类别（文案匹配只作兜底）",
         _ERRCODE_PAT.search(ext) is not None)

    # ── 主插件：最终必须禁止重试 ─────────────────────────────────────
    r.ok("A8 主插件识别 indeterminate 并明说「可能已生效」",
         "indeterminate" in main and "可能已经在浏览器里生效" in main,
         "前面全对、这里漏了 = 仍然重复执行")
    r.ok("A9 命中 indeterminate 时在重试之前就返回",
         re.search(r"indeterminate[\s\S]{0,400}?return", main) is not None)

    section("B. 反向：链路断掉要能被发现")

    # 这几条是"结构还在但语义被改坏"的情形 —— 用真实片段验证判据本身有效
    def detects(pat: str, text: str) -> bool:
        return re.search(pat, text) is not None

    # ① 文案匹配兜底还在（兼容旧扩展）—— 不能把兜底删了却没加显式判断
    has_code_check = detects(_ERRCODE_PAT, ext)
    has_text_check = detects(r'"超时" in msg', ext)
    r.ok("B1 显式类别与文案兜底**至少有一个**", has_code_check or has_text_check)
    r.ok("B2 显式类别判断确实在（不只是兜底）", has_code_check,
         "只有文案匹配的话，改提示语就会静默失效")

    # ② indeterminate 语义在 OpResult 里确实存在且被构造
    base = src_safe("backends/base.py")
    r.ok("B3 OpResult 有 indeterminate 字段与构造器",
         "indeterminate: bool" in base and "indeterminate_result" in base)
    r.ok("B4 后端在超时路径上真的用了它",
         re.search(_ERRCODE_PAT.pattern + r'[\s\S]{0,200}?'
                   r"indeterminate_result", ext) is not None,
         "识别出超时却还返回普通 fail = 白识别")

    # ③ declined 与 indeterminate 是**两种**终止性结果，不能混用。
    # ⚠️ 不能用 `main.index("a") != main.index("b")` 那种判据 ——
    #    两个不同的字符串下标**必然**不等，那条断言永远为真（形同虚设）。
    #    这里用 AST 找 `_call` 里真正的两个分支，并确认：
    #      · 各自判断的是**不同**的字段名；
    #      · 每个分支都**直接 return**（不能只是打条日志继续往下走 ——
    #        那样还会去换后端重试）。
    import ast
    _call = None
    try:
        _tree = ast.parse(main)
        _call = next((n for n in ast.walk(_tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.name == "_call"), None)
    except SyntaxError:
        _call = None

    def _branches(fn, field):
        """返回 fn 里判断 getattr(<x>, 'field', ...) 的分支列表。"""
        out = []
        if fn is None:
            return out
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            src_txt = ast.dump(node.test)
            if f"'{field}'" not in src_txt and f'"{field}"' not in src_txt:
                continue
            # 分支体里是否**直接 return**
            has_ret = any(isinstance(s, ast.Return) for s in node.body)
            out.append((node.lineno, has_ret))
        return out

    _ind = _branches(_call, "indeterminate")
    _dec = _branches(_call, "declined")
    r.ok("B5 _call 里 indeterminate 与 declined 是**两个独立分支**",
         bool(_ind) and bool(_dec)
         and {ln for ln, _ in _ind}.isdisjoint({ln for ln, _ in _dec}),
         f"indeterminate 分支={[l for l, _ in _ind]}；"
         f"declined 分支={[l for l, _ in _dec]}")
    r.ok("B6 两个分支都**直接返回**（不会继续换后端重试）",
         _ind and _dec and all(h for _, h in _ind) and all(h for _, h in _dec),
         f"indeterminate 有 return={[h for _, h in _ind]}；"
         f"declined 有 return={[h for _, h in _dec]}")
