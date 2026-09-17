"""扩展「执行 JS」能力的两道必守关口。

## 为什么单独一组

`browser_script`（执行任意 JS）走的是 `chrome.userScripts` API。
这条路有**两个**很容易漏的依赖，任何一个断掉都表现为"就是执行不了 JS"，
而且**其它命令全都正常**（扩展能装、能连、能读页面），很难联想到是扩展侧的问题：

### ① USER_SCRIPT world 的 CSP 默认禁止动态执行

MDN 对 `userScripts.WorldProperties` 的说明是：
"Defaults to the default CSP for content scripts, **which prohibits
dynamic code execution, such as eval and new Function**"。

而我们的 `execJs` 包装器**正是用 eval / new Function 跑用户脚本** ——
不调 `userScripts.configureWorld()` 放行，就会抛：

    EvalError: Refused to evaluate a string as JavaScript because
    'unsafe-eval' is not an allowed source of script in the following
    Content Security Policy directive…

### ② 端口值域

`buildWsUrl` 不校验端口的话，`-1` / `70000` / `abc` 会拼出
`ws://127.0.0.1:-1/...` 这种**非法 URL**，`new WebSocket()` 抛 "Invalid URL" ——
用户完全看不出是端口填错了。

（端口部分的行为断言在 `security_rules.py` 的 B2.7 里有真实 JS 探针；
这里补静态侧的"校验逻辑还在不在"，防止被整段删掉。）
"""
from __future__ import annotations

import re

from ..harness import section, src_safe, strip_comments_only, strip_js_noise

TITLE = "扩展执行 JS 的两道关口（CSP / 端口）"

def _judge_configure_world(cap_text: str) -> bool:
    """A1/A3 的判据：**真的调了** `chrome.userScripts.configureWorld(`，
    且这个调用**早于**首次 `execute`。

    ⚠️ 抽成函数是为了让 C 段的反向自检能**复用同一条判据** ——
    把变形文本喂进来，看它是否真的变红。否则 C 段只能写"对同一个字符串
    做替换"那种**重言式**断言（必然成立 = 等于没测）。

    ⚠️ 判据必须盯**调用本身**（`configureWorld(`），不能只看"有个叫
    ensureUserScriptWorld 的函数"：调用被删掉、只留一个空壳函数名时，
    只看函数名的判据照样通过 —— 而那时 CSP 根本没被放开，
    执行 JS 仍然静默不可用。（C1 反向自检正是因此才发现这条的。）
    """
    # ⚠️ 先**剥掉注释与字符串**再判 —— 否则注释里写一句
    #    "官方文档：userScripts.configureWorld() 可以配置 CSP"
    #    就能满足判据（那不是**调用**，是说明文字）。
    #    同理 `'unsafe-eval'` 出现在注释里也不该算数。
    #    strip_js_noise 是状态机（见 harness），能正确区分
    #    "代码态"与"注释/字符串态"，也不会被字符串里的 `//` 骗到。
    code = strip_js_noise(cap_text)
    if not re.search(r'configureWorld\s*\(', code):
        return False
    if "userScripts.execute" not in code:
        return False
    return code.index("configureWorld(") < \
        code.rindex("userScripts.execute")


def _judge_unsafe_eval(cap_text: str) -> bool:
    """A2 的判据：CSP 里放行**独立的** `'unsafe-eval'`。

    只带引号的独立关键字才算 —— `'wasm-unsafe-eval'` 里也含 `unsafe-eval`
    这一串，用 `"unsafe-eval" in text` 会被它兜住（假通过）。

    ⚠️ 只看 **CSP 字符串字面量**（正则抠 `csp: "..."` 的引号内内容），
    不是全文找关键字 —— 注释里写一句 "不含 unsafe-eval" 就能满足
    全文匹配（那是说明，不是配置）。
    """
    # ⚠️ 顺序：**先剥注释**（否则注释里那句 `csp: "...unsafe-eval..."`
    #    会被正则抠出来当配置 —— 那就等于被说明文字骗了），
    #    再从剩下的**代码**里抠 `csp:` 的字符串字面量。
    #    注意这时候字符串本身还在（strip_js_noise 只清模板串内容、
    #    清普通字符串**内容**为空串）—— 所以不能直接对它抠引号内容。
    #    做法：在**剥注释后的原文**上抠（保留字符串原样）。
    code_no_comments = strip_comments_only(cap_text)
    m = re.search(r'csp:\s*"([^"]*)"', code_no_comments) or \
        re.search(r"csp:\s*'([^']*)'", code_no_comments)
    if not m:
        return False
    return "'unsafe-eval'" in m.group(1)


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
