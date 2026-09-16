"""工具合并：31 个旧工具 → 12 个动作式工具，逐条核对**能力零丢失**。

不数工具个数，数能力：每个旧工具都要能在新接口里找到出口。
"""

from __future__ import annotations

import re

import json
import os as _os
import shutil as _shutil
import subprocess as _subprocess

from ..harness import JS_DIR, PLUGIN_DIR, section, src, tool_names

TITLE = "工具合并零丢失"

#: 旧工具 → 新出口。``None`` 表示**有意合并**（能力由其它工具覆盖）
LEGACY = {
    "browser_navigate": ("browser_navigate", None),
    "browser_screenshot": ("browser_screenshot", None),
    "browser_click": ("browser_interact", "click"),
    "browser_fill": ("browser_interact", "fill"),
    "browser_type": ("browser_interact", "fill/type"),
    "browser_upload_file": ("browser_interact", "upload"),
    "browser_get_text": ("browser_page", "text/selector"),
    "browser_get_info": ("browser_page", "info"),
    "browser_scroll": ("browser_interact", "scroll"),
    "browser_go_back": ("browser_interact", "go_back"),
    "browser_refresh": ("browser_interact", "refresh"),
    "browser_execute_js": ("browser_script", None),
    "browser_download": ("browser_file", "download"),
    "browser_wait": ("browser_wait", "seconds"),
    "browser_list_files": ("browser_file", "list"),
    "browser_test_visible": ("browser_test_visible", None),
    "browser_debug": ("browser_debug", None),
    # send_file 是「把本地已有文件发给用户」，两个用途分别由
    # screenshot(send) 与 file(download) 覆盖 —— 有意合并，非遗漏
    "browser_send_file": None,
    "browser_keyboard_type": ("browser_interact", "key_type"),
    "browser_keyboard_press": ("browser_interact", "key_press"),
    "browser_keyboard_down_up": ("browser_interact", "key_down/key_up"),
    "browser_mouse_move": ("browser_interact", "mouse_move"),
    "browser_mouse_click": ("browser_interact", "mouse_click"),
    "browser_mouse_down_up": ("browser_interact", "mouse_down/mouse_up"),
    "browser_mouse_wheel": ("browser_interact", "mouse_wheel"),
    "browser_mouse_drag": ("browser_interact", "mouse_drag"),
    "browser_hover": ("browser_interact", "hover"),
    # 桥接插件原有
    "browser_list_tabs": ("browser_tabs", None),
    "browser_get_page": ("browser_page", "text/outline/html"),
    "browser_extract": ("browser_page", "extract"),
    "browser_wait_for": ("browser_wait", "selector/text"),
    # 补充工具
    "browser_extension_help": ("browser_extension_help", None),
    "browser_cookie": ("browser_cookie", "export/import"),
}

def _tool_enum(src: str, tool: str) -> set[str]:
    """取出某个工具 **params 里 action/mode 的 enum**。

    ⚠️ 只取 schema 段（`@register.tool` 装饰器内部），**不含函数体** ——
    函数体里会出现同样的字面量（比如 `if a == "hover"`），
    带上它就会让"enum 里删了 action"检测不出来。
    """
    i = src.find(f'name="{tool}"')
    if i < 0:
        return set()
    start = src.rfind("@register.tool", 0, i)
    if start < 0:
        start = i
    # 装饰器参数到右括号结束（这里用"到 async def"作为上界更稳）
    fn = src.find("async def ", i)
    seg = src[start:fn if fn > 0 else i + 3000]
    # ⚠️ 只取 **action / mode 属性自己的** enum。
    #    原先把这个工具段里**所有** enum 并起来，于是某个被删掉的 action
    #    只要还留在别的参数 enum 里，C1 就检测不出这次删除。
    out = set()
    for prop in ("action", "mode"):
        mm = re.search(rf'"{prop}":\s*\{{[^}}]*?"enum":\s*\[([^\]]*)\]', seg)
        if mm:
            out |= set(re.findall(r'"([a-z_]+)"', mm.group(1)))
    return out


#: 非 action 式工具的关键参数（丢了就是能力丢失，单靠 enum 查不出来）
LEGACY_PARAMS = {
    "browser_wait": ("selector", "text", "seconds", "timeout"),
    # ⚠️ text 必须算进来：这个工具的主要用法就是"等某段文字出现"，
    #    漏掉它的话，参数被删掉也查不出来（能力静默丢失）。
    "browser_wait_for": ("selector", "text", "timeout"),
}

NEED_ACTIONS = {
    "click", "fill", "type", "hover", "scroll", "upload",
    "go_back", "refresh",
    "key_press", "key_down", "key_up", "key_type",
    "mouse_click", "mouse_move", "mouse_down", "mouse_up",
    "mouse_wheel", "mouse_drag",
}


def run(r) -> None:
    main = src("main.py")
    if not main:
        r.ok("读取 main.py", False, "文件为空或不存在")
        return
    hb = src("backends/headless_backend.py")
    eb = src("backends/extension_backend.py")
    now = tool_names(main)

    section("旧工具 → 新出口")
    missing, intentional = [], []
    for old, dest in LEGACY.items():
        if dest is None:
            intentional.append(old)
            r.note(f"🔀 {old:<28} → 有意合并（能力由其它工具覆盖）")
            continue
        newtool, action = dest
        # ⚠️ 只验"工具名存在"是不够的 —— 工具存在但 action 不存在的话，
        #    模型照文档传 `action=xxx` 会直接被拒绝，功能等于丢了。
        #    所以这里把 action 也逐条验一遍。
        ok = newtool in now
        if ok and action:
            # ⚠️ 必须在**目标工具自己的 schema 段**里找 action，
            #    不能全文搜 —— 全文搜会命中别处的同名动作，
            #    导致"目标工具删了这个 action"也照样 PASS。
            enums = _tool_enum(main, newtool)
            # 有些目标工具本来就**不是** action 式的（browser_wait / browser_tabs
            # 等），没有 enum 是正常的，不该要求 action 存在。
            # 判据：它的参数里声明了 action/mode 吗？声明了才要求 enum。
            _seg = main[main.find(f'name="{newtool}"'):][:1600]
            _has_sel = bool(re.search(r'"(action|mode)":\s*\{"type"', _seg))
            if _has_sel and not enums:
                ok = False
                missing.append(f"{old}({newtool} 声明了 action/mode 但没有可解析的 enum)")
            elif enums:
                for a in [x.strip() for x in action.split("/") if x.strip()]:
                    if a not in enums:
                        ok = False
                        missing.append(f"{old}({newtool}.enum 里没有 {a})")
            else:
                # ⚠️ 目标工具**不是** action 式（如 browser_wait）时，
                #    上面整段都会被跳过 —— 于是"browser_wait 丢了
                #    seconds/selector 参数"这种能力丢失检测不出来。
                #    这里按**具名参数**再校验一遍：LEGACY 里登记的必要
                #    参数名必须仍出现在该工具的 params 里。
                _need = LEGACY_PARAMS.get(old, ())
                for _pn in _need:
                    if f'"{_pn}"' not in _seg:
                        ok = False
                        missing.append(f"{old}({newtool} 缺参数 {_pn})")
        if not ok and (newtool not in now):
            missing.append(old)
        r.note(f"{'✅' if ok else '❌'} {old:<28} → {newtool}"
               + (f"(action={action})" if action else ""))

    r.ok("C1 每个旧工具都有新出口（零能力丢失）", not missing,
         f"缺失={missing or '无'}；有意合并={len(intentional)} 个；"
         f"{len(LEGACY)} 个旧工具 → {len(now)} 个新工具名")

    # 交互动作齐全
    # ⚠️ 用 _tool_enum() 而不是全仓正则：后者会命中**别的工具**里的 enum，
    #    于是 browser_interact 自己少了个动作也照样通过。
    actions = _tool_enum(main, "browser_interact")
    r.ok("C2 browser_interact 的 action 覆盖全部交互动作",
         NEED_ACTIONS <= actions,
         f"{len(actions)} 个动作；缺={sorted(NEED_ACTIONS - actions) or '无'}")

    # 插件调用的后端方法，两个后端都要实现
    called = set(re.findall(r'await self\._call\("([a-z_]+)"', main))
    hb_m = set(re.findall(r'async def ([a-z_]+)\(', hb))
    eb_m = set(re.findall(r'async def ([a-z_]+)\(', eb))
    local = {"get_text"}
    r.ok("C3 无头后端实现所有被调用的方法",
         not (called - hb_m - local), f"缺={sorted(called - hb_m - local) or '无'}")
    r.ok("C4 扩展后端实现所有被调用的方法",
         not (called - eb_m - local), f"缺={sorted(called - eb_m - local) or '无'}")

    # 协议两端 + 扩展实现
    proto_py = src("protocol.py")
    proto_js = src("browser-bridge/protocol.js")
    py_cmds = set(re.findall(r'^CMD_[A-Z_]+ = "([a-z_]+)"', proto_py, re.M))
    # ⚠️ 先确认标记存在再 split：否则 IndexError 会逃出 run()，
    #    整组变成一条笼统失败、后面所有检查都不执行。
    _MARK = "export const CMD = {"
    if _MARK not in proto_js:
        # ⚠️ 记失败但**不要 return**：一 return，后面的 C5b~C9 全都不执行了，
        #    报告上看不出"后面那些检查其实没跑"。用空集合继续走完。
        r.ok("C5a 协议命令两端一致", False,
             f"protocol.js 里找不到 `{_MARK}`（被改名或删了？）")
        js_cmds = set()
    else:
        cmd_section = proto_js.split(_MARK)[1].split("};")[0]
        js_cmds = set(re.findall(r'^\s+[A-Z_]+: "([a-z_]+)",', cmd_section, re.M))
        r.ok("C5a 协议命令两端一致", py_cmds == js_cmds,
             f"仅 Python={sorted(py_cmds - js_cmds) or '无'}；"
             f"仅 JS={sorted(j for j in js_cmds - py_cmds) or '无'}")

    bg = src("browser-bridge/background.js")
    impl = set(re.findall(r'async function (\w+)\(', bg))
    cap = src("browser-bridge/capabilities.js")
    cmds = src("browser-bridge/commands.js")
    impl |= set(re.findall(r'async function (\w+)\(', cap + cmds))
    r.ok("C5b 补齐的命令都有实现",
         all(f"async function {f}(" in (cap + cmds) for f in
             ("getInfo", "goBack", "refresh", "hover", "keyPress", "keyDownUp",
              "mouseMove", "mouseClick", "mouseDownUp", "mouseWheel",
              "mouseDrag", "listFiles", "debugInfo")),
         "13 个补齐命令")

    # ⚠️ 必须限定在 **execJs 函数体内部**：只查"cap 里出现过
    #    chrome.userScripts.execute"是不够的 —— 别处（比如 ensureUserScripts）
    #    也可能提到它，execJs 自己改成别的方式实现照样能通过。
    _ej = cap.split("async function execJs(")[-1].split("async function upload(")[0] \
        if "async function execJs(" in cap else ""
    r.ok("C6 扩展侧实现 exec_js（execJs 函数体内走 chrome.userScripts.execute）",
         bool(_ej) and "chrome.userScripts.execute" in _ej,
         "必须在 execJs 自己的实现里出现")
    r.ok("C7 扩展侧实现 upload（DataTransfer）",
         "async function upload(" in cap and "DataTransfer" in src("browser-bridge/content.js"))
    # 看**意图**而不是写死的字面量：现在凭据是按协议条件携带的
    # （HTTPS 才带，防止明文泄漏），所以断言"用用户会话"这一点。
    # ⚠️ 只看"有三个子串"是不够的：即使 credentials 被改成无条件
    #    `"include"`（HTTP 也带会话 Cookie → 明文泄漏），只要这三个词
    #    还在，断言照样通过。
    #    必须**限定在 downloadViaSession 内**，并且要求凭据表达式
    #    **带 HTTPS 条件 + 有 omit 分支**。
    _dl = cap.split("async function downloadViaSession(")[-1][:2000] \
        if "async function downloadViaSession(" in cap else ""
    _creds = re.search(r'credentials\s*:\s*([^,\n]+)', _dl)
    _expr = (_creds.group(1) if _creds else "")
    _cond_ok = (":" in _expr) and ('"include"' in _expr) and ('"omit"' in _expr)
    r.ok("C8 扩展侧实现 download（用户会话 + 分块）",
         bool(_dl) and _cond_ok and "sendChunk" in _dl,
         "凭据必须是按协议条件的表达式（HTTPS->include, 否则 omit），"
         f"且 sendChunk 在 downloadViaSession 内；实际 credentials={_expr!r}")
    # ── 凭据模式的**行为**验证（HTTP/HTTPS 各跑一次）──────────────
    # 语法检查只能证明"表达式里有 include 和 omit"；把条件写反
    # （HTTPS→omit、HTTP→include）或者条件恒真，语法检查照样通过，
    # 但**会话 Cookie 会在明文 HTTP 上被发出去**。
    _cred_js = JS_DIR / "cred_mode.mjs"
    # ⚠️ 不能"文件不在就静默跳过" —— 那样回归套件会**通过但没执行 C8b**，
    #    看起来一切正常，实际凭据模式没有任何行为验证。
    #    探测脚本是仓库文件，缺了就是回归不完整 → 记 FAIL。
    #    node 不在是环境问题 → 记 warning（不算失败）。
    _node = _shutil.which("node")
    if not _cred_js.is_file():
        r.ok("C8b 凭据探测脚本存在", False,
             f"缺少 {_cred_js} —— 凭据模式将没有任何行为验证")
    elif not _node:
        r.warn("没有 node，跳过 C8b 凭据模式行为验证",
               "安装 Node.js 后可启用")
    else:
        try:
            _env = {
                "PATH": _os.environ.get("PATH", "") + ":/usr/bin:/bin:/usr/local/bin",
                "KIRA_PLUGIN_DIR": str(PLUGIN_DIR),
            }
            # ⚠️ 用 which 解析出的路径跑，不用裸 "node" ——
            #    这里给子进程换了 env，裸名字会在**子进程里**重新查 PATH。
            _cp = _subprocess.run([_node, str(_cred_js)], cwd=str(JS_DIR),
                                  capture_output=True, text=True, env=_env,
                                  timeout=60)
            # ⚠️ 必须判退出码 + 结果完整性：脚本崩了/少打一项时，
            #    逐项断言会"少报"（少报 = 漏检），甚至一项都不报。
            if _cp.returncode != 0:
                r.ok("C8b 凭据探测脚本正常退出", False,
                     f"exit={_cp.returncode}；{(_cp.stderr or '')[:150]}")
            else:
                _data = json.loads((_cp.stdout or "[]").strip().splitlines()[-1])
                # 至少要覆盖：1 条 isHttps 来源 + HTTPS/HTTP 两种协议
                # （现在还会多测大小写不敏感与本机 HTTP）
                _names = [str(x.get("name", "")) for x in _data]
                _need = ("凭据表达式引用了由 URL 推导出的 isHttps",
                         "HTTPS 下载带凭据", "HTTP 下载不带凭据")
                _miss = [n for n in _need if n not in _names]
                if _miss:
                    r.ok("C8b 凭据探测覆盖必要场景", False,
                         f"缺少={_miss}；实际={_names}")
                else:
                    for _it in _data:
                        r.ok(f"C8b {_it['name']}", bool(_it.get("ok")),
                             _it.get("detail", ""))
        except Exception as e:
            r.ok("C8b 凭据模式行为验证", False, f"{type(e).__name__}: {e}"[:140])

    r.ok("C9 扩展侧实现 cookie 导出/写入",
         "async function cookieGet(" in cap and "async function cookieSet(" in cap)
