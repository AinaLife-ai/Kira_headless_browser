"""核对"我们声称修好的东西"是否真的在代码里。

起因很具体：第七轮我在提交信息和 README 里都写了
「`buildWsUrl` 把裸 IPv6 的冒号当端口剥掉 —— 已修」，
但 **protocol.js 根本没进那次提交**，一行都没改。
之后的第八、九、十轮都没发现，因为**没有任何检查会去回验声称**。

这里把"每条声称"绑定到一个**可判定的事实**（文件存在某个模式、
或某段行为可跑通）。声明与实际不符时直接 FAIL。
"""

from __future__ import annotations

from ..harness import PLUGIN_DIR, section, src

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
    ("拼接 base64 前去掉各块 padding",
     "browser-bridge/capabilities.js", r"b64s\.map"),
    ("无头下载带 cookie 时不自动跟随重定向",
     "backends/headless_backend.py", r"allow_redirects=False"),
    ("下载列表不返回绝对路径",
     "browser-bridge/commands.js", r"filename\.split"),
    ("evaluate 把 __error 转成抛出",
     "browser-bridge/capabilities.js", r"__error"),
    ("面板用 textContent 渲染不受控字段",
     "web/index.html", r"_renderDomains"),
    ("cookie_get 在「只读但敏感」确认集里",
     "browser-bridge/shared.js", r"CONFIRM_ONLY_COMMANDS"),
    ("上传上限不超过单条消息能扛的范围",
     "backends/extension_backend.py", r"MAX_UPLOAD_BYTES = 32 \* 1024 \* 1024"),
]


def run(r):
    section("H. 声称 ↔ 实际（防止「写了但没改」）")
    import re

    bad = []
    for claim, rel, pat in CLAIMS:
        p = PLUGIN_DIR / rel
        if not p.is_file():
            bad.append(f"{claim}（文件不存在：{rel}）")
            continue
        body = src(rel)
        if not re.search(pat, body):
            bad.append(f"{claim}（{rel} 里找不到 {pat}）")

    r.ok("H1 每一条写在提交信息/README 里的修复都真的在代码里",
         not bad,
         f"未落实={bad or '无'}（共核对 {len(CLAIMS)} 条声称）")
