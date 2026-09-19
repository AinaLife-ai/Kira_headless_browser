"""核对"我们声称修好的东西"是否真的在代码里。

起因很具体：第七轮我在提交信息和 README 里都写了
「`buildWsUrl` 把裸 IPv6 的冒号当端口剥掉 —— 已修」，
但 **protocol.js 根本没进那次提交**，一行都没改。
之后的第八、九、十轮都没发现，因为**没有任何检查会去回验声称**。

这里把"每条声称"绑定到一个**可判定的事实**（文件存在某个模式、
或某段行为可跑通）。声明与实际不符时直接 FAIL。
"""

from __future__ import annotations

from ..harness import PLUGIN_DIR, section, src_safe

TITLE = "声称 ↔ 实际（写了但没改）"

#: (声称, 相对路径, 必须匹配的正则)
#: ⚠️ 只放在这里**能被机器判定**的断言；"语义正确"交给各自的专项检查。
CLAIMS = [
    ("裸 IPv6 不会被当端口剥掉（含 URL 加方括号）",
     "browser-bridge/protocol.js", r"wireHost"),
    ("回环判定覆盖 v4-mapped / 展开写法",
     "browser-bridge/protocol.js", r"_expandV6"),
    ("上传分块大小是 3 的倍数",
     "backends/extension_backend.py", r"255 \* 1024"),
    ("上传分块流式：累积分块在页面侧（content.js），不在 SW",
     "browser-bridge/content.js", r"_upSessions"),
    ("SW 侧上传只转发、不累积文件内容",
     "browser-bridge/capabilities.js", r"_uploadTabs"),
    ("插件侧逐块发送上传内容",
     "backends/extension_backend.py", r"CMD_UPLOAD_CHUNK"),
    ("无头下载带 cookie 时不自动跟随重定向",
     "backends/headless_backend.py", r"allow_redirects=False"),
    ("下载列表不返回绝对路径",
     "browser-bridge/commands.js", r"filename\.split"),
    ("evaluate 把 __error 转成抛出",
     "browser-bridge/capabilities.js", r"__error"),
    ("面板用 textContent 渲染不受控字段",
     "web/app.js", r"_renderDomains"),
    # ⚠️ 不能只验集合名存在，也不能要求 cookie_get 必须是**第一个元素** ——
    #    解析集合字面量的内容，只要它真的在里面就算过（位置无关）。
    #    匹配 `CONFIRM_ONLY_COMMANDS = new Set([ ... ])` 且括号内出现 cookie_get。
    ("cookie_get 真的在「只读但敏感」确认集里（位置无关）",
     "browser-bridge/shared.js",
     r'CONFIRM_ONLY_COMMANDS\s*=\s*new Set\(\s*\[[^\]]*"cookie_get"'),
    ("插件侧的只读敏感确认集真的含 cookie_get（位置无关）",
     "backends/extension_backend.py",
     r'CONFIRM_ONLY_CMDS\s*=\s*\{[^}]*"cookie_get"'),
    ("上传上限由硬顶钳制",
     "backends/extension_backend.py", r"MAX_UPLOAD_BYTES = 256 \* 1024 \* 1024"),
    ("上传超时/页面超时不会被换后端重试（indeterminate）",
     "backends/base.py", r"indeterminate"),
    ("buildWsUrl 接受 http/https 写法",
     "browser-bridge/protocol.js", r"https\?"),
    ("连接超时会关掉 socket 并重排重连",
     "browser-bridge/background.js", r"scheduleReconnect"),
    ("面板 refresh 捕获 sendMessage 失败",
     "browser-bridge/popup.js", r"catch"),
]


def _check_upload_defaults(r):
    """单独一条：upload 上限的**每一处默认值**都必须一致且不超过硬顶。

    ⚠️ 这条是补上一次"只改了一半"的教训：我把
    ExtensionBackend.MAX_UPLOAD_BYTES 降到 32MB，却没改 schema / main.py /
    README 的默认值 —— 那些默认值（200MB）会被传下去，硬顶形同虚设。
    """
    import json
    import re
    bad = []
    ceiling = 256 * 1024 * 1024

    # ⚠️ 前置读取用 safe 版：缺文件时各段自己报错，不中断整组。
    try:
        sch = json.loads(src_safe("schema.json"))
    except Exception:
        sch = {}
    d = (sch.get("upload_max_bytes") or {}).get("default")
    if d != ceiling:
        bad.append(f"schema.json default={d}（期望 {ceiling}）")

    main = src_safe("main.py")
    m = re.search(r'cfg\.get\("upload_max_bytes",\s*([^)]+)', main)
    if not m:
        bad.append("main.py 里找不到 upload_max_bytes 的兜底值")
    else:
        expr = m.group(1).strip()
        try:
            val = int(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
        except Exception:
            val = None
        if val != ceiling:
            bad.append(f"main.py 兜底={expr}（期望 {ceiling}）")

    rm = src_safe("README.md")
    if str(ceiling) not in rm:
        bad.append(f"README.md 里没写 {ceiling}")

    eb = src_safe("backends/extension_backend.py")
    mc = re.search(r"MAX_UPLOAD_BYTES = ([0-9]+ \* 1024 \* 1024)", eb)
    if not mc:
        bad.append("extension_backend.py 找不到 MAX_UPLOAD_BYTES")
    else:
        got = int(eval(mc.group(1), {"__builtins__": {}}, {}))  # noqa: S307
        if got != ceiling:
            bad.append(f"MAX_UPLOAD_BYTES={got}（期望 {ceiling}）")

    # main.py 必须**真的钳制**到扩展后端的上限。
    # ⚠️ 不能只判 "MAX_UPLOAD_BYTES 这个词出现过" —— import、注释、
    #    甚至是另一处无关引用都会让检查通过，而配置值并没有被夹住。
    #    判据：必须存在一段同时包含「MAX_UPLOAD_BYTES」与
    #    「对 _umb/upload 值做 min/比较后赋值」的代码。
    _clamp = re.search(
        r'MAX_UPLOAD_BYTES[\s\S]{0,400}?'
        r'(?:if[^\n]*_umb\s*>\s*_ceil|_umb\s*=\s*_ceil|'
        r'min\([^)]*_umb[^)]*_ceil)', main)
    _decl = re.search(r'_umb\s*=\s*int\(cfg\.get\("upload_max_bytes"', main)
    if not (_clamp and _decl):
        bad.append("main.py 没有把配置值**真正**夹到 MAX_UPLOAD_BYTES 之内"
                   f"（clamp={bool(_clamp)}, decl={bool(_decl)}）")

    r.ok("H2 upload 上限的四处默认值一致，且配置值被钳制在硬顶内",
         not bad, f"不一致={bad or '无'}")


def run(r):
    section("H. 声称 ↔ 实际（防止「写了但没改」）")
    import re

    bad = []
    for claim, rel, pat in CLAIMS:
        p = PLUGIN_DIR / rel
        if not p.is_file():
            bad.append(f"{claim}（文件不存在：{rel}）")
            continue
        body = src_safe(rel)
        if not re.search(pat, body):
            bad.append(f"{claim}（{rel} 里找不到 {pat}）")

    r.ok("H1 每一条写在提交信息/README 里的修复都真的在代码里",
         not bad,
         f"未落实={bad or '无'}（共核对 {len(CLAIMS)} 条声称）")

    _check_upload_defaults(r)
