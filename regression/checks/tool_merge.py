"""工具合并：31 个旧工具 → 12 个动作式工具，逐条核对**能力零丢失**。

不数工具个数，数能力：每个旧工具都要能在新接口里找到出口。
"""

from __future__ import annotations

import re

from ..harness import section, src, tool_names

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
    out = set()
    for mm in re.finditer(r'"enum":\s*\[([^\]]*)\]', seg):
        out |= set(re.findall(r'"([a-z_]+)"', mm.group(1)))
    return out


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
            for a in [x.strip() for x in action.split("/") if x.strip()]:
                # 目标工具的 enum 里必须有这个动作
                if enums and a not in enums:
                    ok = False
                    missing.append(f"{old}({newtool}.enum 里没有 {a})")
        if not ok and (newtool not in now):
            missing.append(old)
        r.note(f"{'✅' if ok else '❌'} {old:<28} → {newtool}"
               + (f"(action={action})" if action else ""))

    r.ok("C1 每个旧工具都有新出口（零能力丢失）", not missing,
         f"缺失={missing or '无'}；有意合并={len(intentional)} 个；"
         f"{len(LEGACY)} 个旧工具 → {len(now)} 个新工具名")

    # 交互动作齐全
    m = re.search(r'"action": \{"type": "string", "enum": \[([^\]]+)\]', main)
    actions = set(re.findall(r'"([a-z_]+)"', m.group(1))) if m else set()
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
    cmd_section = proto_js.split("export const CMD = {")[1].split("};")[0]
    js_cmds = set(re.findall(r'^\s+[A-Z_]+: "([a-z_]+)",', cmd_section, re.M))
    r.ok("C5a 协议命令两端一致", py_cmds == js_cmds,
         f"仅 Python={sorted(py_cmds - js_cmds) or '无'}；"
         f"仅 JS={sorted(js_cmds - py_cmds) or '无'}")

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

    r.ok("C6 扩展侧实现 exec_js（走 chrome.userScripts）",
         "async function execJs(" in cap and "chrome.userScripts.execute" in cap)
    r.ok("C7 扩展侧实现 upload（DataTransfer）",
         "async function upload(" in cap and "DataTransfer" in src("browser-bridge/content.js"))
    # 看**意图**而不是写死的字面量：现在凭据是按协议条件携带的
    # （HTTPS 才带，防止明文泄漏），所以断言"用用户会话"这一点。
    _dl = cap.split("async function downloadViaSession(")[-1][:1500]
    r.ok("C8 扩展侧实现 download（用户会话 + 分块）",
         "async function downloadViaSession(" in cap
         and "credentials" in _dl and '"include"' in _dl
         and "sendChunk" in cap)
    r.ok("C9 扩展侧实现 cookie 导出/写入",
         "async function cookieGet(" in cap and "async function cookieSet(" in cap)
