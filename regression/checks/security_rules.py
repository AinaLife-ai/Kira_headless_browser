"""安全规则：域名匹配 / 本机地址识别。

这两个是**外部可达**的入口（浏览器会把 URL 交给我们判断），
所以用真实用例逐条钉住行为。
"""

from __future__ import annotations

from ..harness import PLUGIN_DIR, load_module, section

TITLE = "安全规则（域名 / 本机地址）"

#: (host, 规则, 期望是否命中)
MATCH_CASES = [
    # 字面量域名：自己 + 子域，绝不裸子串
    ("github.com", "github.com", True),
    ("gist.github.com", "github.com", True),
    ("evil-github.com", "github.com", False),
    ("github.com.evil.com", "github.com", False),
    # 子域模式：不跨域名边界
    ("example.com", "*.example.com", True),
    ("good.example.com", "*.example.com", True),
    ("example.com.evil.com", "*.example.com", False),
    # 关键词（整串）—— 用户明确要求关键词拦截
    ("mybank.cn", "*bank*", True),
    ("repayment.xyz", "*bank*", False),
    ("pay.example.com", "*pay*", True),
    # 域名主体关键词 —— 不允许跨到别的域名
    ("bankofamerica.com", "*.bank*", True),
    ("bank.com.cn", "*.bank*", True),
    ("bankofamerica.com.evil.com", "*.bank*", False),
    ("example.com.evil.test", "*.example*", False),
    ("good.example.com", "*.example*", True),
    ("example.com", "*.example*", True),
    ("paypal.com", "*.pay*", True),
    ("paypal.com.evil.io", "*.pay*", False),
]

#: 浏览器会当成本机、但 ipaddress 认不出的写法
LOCAL_CASES = [
    ("127.0.0.1", True), ("localhost", True), ("localhost.", True),
    ("127.1", True), ("0x7f000001", True), ("0177.0.0.1", True),
    ("0x7f.0.0.1", True), ("2130706433", True), ("0.0.0.0", True),
    ("::1", True), ("[::1]", True),
    ("example.com", False), ("8.8.8.8", False), ("1.1.1.1", False),
]


def run(r) -> None:
    sec = load_module("security", PLUGIN_DIR / "security.py",
                      "kirabrowser_sec", PLUGIN_DIR)

    section("A. 域名规则匹配")
    bad = []
    for host, pat, expect in MATCH_CASES:
        got = sec._matches(host, sec._normalize_pattern(pat))
        if got != expect:
            bad.append(f"{host} vs {pat}: got={got} exp={expect}")
    r.ok("A1 域名规则全部符合预期", not bad,
         f"{len(MATCH_CASES)} 个用例；不符={bad or '无'}")

    section("B. 本机地址识别（SSRF 防护）")
    bad2 = []
    for host, expect in LOCAL_CASES:
        got = sec.is_local_host(host)
        if got != expect:
            bad2.append(f"{host}: got={got} exp={expect}")
    r.ok("B1 本机等价写法全部被识别", not bad2,
         f"{len(LOCAL_CASES)} 个用例；不符={bad2 or '无'}")

    section("C. check_url 整体行为")
    # 黑名单优先
    ok, _ = sec.check_url("https://bankofamerica.com/x", blocked=["*.bank*"])
    r.ok("C1 黑名单命中即拒（读也拦）", not ok)
    # 白名单非空时，写操作必须命中
    ok, _ = sec.check_url("https://example.com/x", allowed=["*.github.com"],
                          for_write=True)
    r.ok("C2 白名单非空时，域外写操作被拒", not ok)
    # 读操作在白名单外仍放行
    ok, _ = sec.check_url("https://example.com/x", allowed=["*.github.com"])
    r.ok("C3 读操作不受白名单限制", ok)
    # 特殊 scheme
    ok, _ = sec.check_url("file:///etc/passwd")
    r.ok("C4 file:// 被拒", not ok)
    ok, _ = sec.check_url("javascript:alert(1)")
    r.ok("C5 javascript: 被拒", not ok)
    # 本机地址
    ok, _ = sec.check_url("http://127.1:5267/")
    r.ok("C6 本机地址（简写形式）被拒", not ok)
