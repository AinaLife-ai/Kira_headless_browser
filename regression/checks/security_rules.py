"""安全规则：域名匹配 / 本机地址识别。

这两个是**外部可达**的入口（浏览器会把 URL 交给我们判断），
所以用真实用例逐条钉住行为。
"""

from __future__ import annotations

import re


#: buildWsUrl 行为探针（B2.7）：__URI__ 会被换成 protocol.js 的 file:// URI
_PROBE_JS = r'''
import { buildWsUrl } from "__URI__";
const cases = [
  ["::1",              "ws:",  "[::1]:5267"],
  // new URL() 会把 IPv6 规范化：展开写法 -> 压缩写法，
  // v4-mapped 会转成十六进制形式。这里按**规范形式**断言。
  ["0:0:0:0:0:0:0:1",  "ws:",  "[::1]:5267"],
  ["::ffff:127.0.0.1", "ws:",  "[::ffff:7f00:1]:5267"],
  ["[::1]",            "ws:",  "[::1]:5267"],
  ["127.0.0.1",        "ws:",  "127.0.0.1:5267"],
  ["http://127.0.0.1:5267", "ws:", "127.0.0.1:5267"],
  ["localhost:5267",   "ws:",  "localhost:5267"],
  ["example.com",      "wss:", "example.com:5267"],
  ["https://example.com", "wss:", "example.com:5267"],
];
let bad = [];
for (const [h, wantScheme, wantHost] of cases) {
  let u;
  try { u = new URL(buildWsUrl(h, 5267, "T")); }
  catch (e) { bad.push(h + " -> THREW " + e.message); continue; }
  // 精确比较：协议 + host（host 含端口），不用 startsWith
  if (u.protocol !== wantScheme || u.host !== wantHost) {
    bad.push(h + " -> " + u.protocol + "//" + u.host + " (expected "
             + wantScheme + "//" + wantHost + ")");
  }
}
if (bad.length) { console.log(bad.join(" ; ")); process.exit(1); }
console.log("OK");
'''


import os as _os
import shutil as _sh
import subprocess as _sp
import tempfile as _tf
from pathlib import Path

from ..harness import PLUGIN_DIR, load_module, section, src

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
    # 字面量开头 + 通配后缀：fnmatch 的 * 跨点号会造成越界
    ("github.com", "github.*", True),
    ("sub.github.com", "github.*", True),
    ("github.io", "github.*", True),
    ("github.com.evil.test", "github.*", False),
]

#: 浏览器会当成本机、但 ipaddress 认不出的写法
LOCAL_CASES = [
    ("127.0.0.1", True), ("localhost", True), ("localhost.", True),
    ("127.1", True), ("0x7f000001", True), ("0177.0.0.1", True),
    ("0x7f.0.0.1", True), ("2130706433", True), ("0.0.0.0", True),
    ("::1", True), ("[::1]", True),
    # IPv4-mapped IPv6 —— ipaddress 对它的 is_loopback 是 False，
    # 但 Chromium 会真的连到回环。不处理就是个后门。
    ("::ffff:127.0.0.1", True), ("::ffff:7f00:1", True),
    ("[::ffff:127.0.0.1]", True), ("::ffff:2130706433", True),
    ("::ffff:0x7f000001", True),
    ("::ffff:8.8.8.8", False),
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

    section("B2. 扩展的 WS 地址：非本机必须 wss")
    # 令牌放在 query string 里，明文 ws:// 到远程主机会把它暴露在网络上
    pjs = src("browser-bridge/protocol.js")
    r.ok("B2.1 有回环判定函数", "isLoopbackHost" in pjs)
    # 默认：本机 ws、其它主机 wss（远程能正常连，且令牌加密）
    r.ok("B2.2 非回环主机默认用 wss（令牌在 query 里必须加密）",
         'loopback ? "ws" : "wss"' in pjs,
         "远程主机能被正常连上，且默认走 wss")
    # 只在**显式**要求明文连非本机时才拒绝
    r.ok("B2.3 显式 ws:// 连非本机时拒绝（不静默发明文）",
         'scheme === "ws" && !loopback' in pjs and "拒绝以明文 ws://" in pjs)
    # 用户填 wss://xxx 时要能解析出 scheme（否则连不上远程）
    # ⚠️ 不能只匹配源码里的字面量 `^(wss?)://` —— 现在这个正则扩展成
    #    同时接受 http/https（面板上用户很自然会粘 http://host:port），
    #    写死字面量会导致一改实现就误报。改为**行为级**断言：真跑一遍，
    #    确认各种写法都能被正确归一。
    # 源码层面确认 scheme 解析接受 ws/wss/http/https（行为由 B2.7 真跑覆盖）
    r.ok("B2.4 支持用户在地址里写 ws:// / wss:// / http://",
         "wss?" in pjs and "https?" in pjs,
         "地址栏允许 ws/wss 以及误填的 http/https")
    # 扩展下载不允许跟随重定向（跨协议会带出 cookie）
    # ⚠️ 只在下**载处理器内部**找这个选项 —— 全文搜会命中注释或别处，
    #    下载里删掉了也照样通过。
    cap = src("browser-bridge/capabilities.js")
    _dl = cap.split("async function downloadViaSession(")[-1].split("// ───")[0] \
        if "async function downloadViaSession(" in cap else ""
    r.ok("B2.5 下载不跟随重定向（杜绝跨协议带 cookie）",
         'redirect: "error"' in _dl,
         "必须出现在 downloadViaSession 里")
    # [12] 不把本机绝对路径回传给服务。
    # ⚠️ 别用"子串在不在一起"这种脆弱判据 —— 格式化一下就能绕过。
    #    改成提取 listFiles 里的 map 对象字面量，检查它的**键**里有没有 path。
    cmds = src("browser-bridge/commands.js")
    fn = cmds.find("async function listFiles(")
    seg = cmds[fn:fn + 1600] if fn > 0 else ""
    # 取出 map((d) => ({ ... })) 里的返回字段。
    # ⚠️ 必须同时识别**简写属性** `{ name, path }` ——
    #    只匹配 `path:` 的话，简写形式的 path 会漏掉 →
    #    "结果里暴露了本地绝对路径"就检测不出来了。
    keys = set()
    for mm in re.finditer(r'([a-z_]+)\s*:', seg):        # 显式 `path: x`
        keys.add(mm.group(1))
    for mm in re.finditer(r'\{\s*([^{}]*?)\s*\}', seg):   # 对象字面量内部
        for part in mm.group(1).split(","):
            part = part.strip()
            if not part or ":" in part:
                continue
            if re.fullmatch(r'[A-Za-z_$][\w$]*', part):
                keys.add(part)                            # 简写 `{ name, path }`
    r.ok("B2.6 下载列表不返回绝对路径（键里没有 path）",
         "path" not in keys and "name" in keys,
         f"listFiles 返回的字段={sorted(keys)}")

    # ── buildWsUrl 的 IPv6 / 端口解析（真实跑 JS）────────
    #  ⚠️ 必须**真的执行** protocol.js：
    #  第七轮我曾把"修好了"写进提交信息和 README，
    #  但 protocol.js **根本没进那次提交**，一行都没改。
    #  当时没有任何检查发现得了 —— 因为全是文本匹配。
    if _sh.which("node"):
        _probe = _PROBE_JS.replace("__URI__",
                                   (PLUGIN_DIR / "browser-bridge" / "protocol.js").as_uri())
        _tf2 = Path(_tf.gettempdir()) / (
            f"_kira_ws_{_os.getpid()}_{next(_tf._get_candidate_names())}.mjs")
        try:
            _tf2.write_text(_probe, encoding="utf-8")
            _rr = _sp.run(["node", str(_tf2)], capture_output=True,
                          text=True, timeout=30)
            _out = (_rr.stdout or "").strip()
            r.ok("B2.7 buildWsUrl \u6b63\u786e\u89e3\u6790\u88f8 IPv6\uff08\u771f\u8dd1 JS\uff09",
                 _rr.returncode == 0 and _out.endswith("OK"),
                 _out[:160] or (_rr.stderr or "")[:160])
        except Exception as e:
            r.ok("B2.7 buildWsUrl \u6b63\u786e\u89e3\u6790\u88f8 IPv6\uff08\u771f\u8dd1 JS\uff09",
                 False, str(e)[:120])
        finally:
            try:
                _tf2.unlink()
            except Exception:
                pass
    else:
        r.warn("没有 node，跳过 buildWsUrl 行为检查",
               "安装 Node.js 后可启用")

    # ── 多级公共后缀（PSL）────────────────────────────────────────────
    #  ⚠️ 这里过去靠一张**硬编码**的 MULTI_TLD 表（com.cn / co.uk / …）。
    #     那种写法必然补不全：co.za / com.ar / co.il 都不在表里，
    #     于是 `bank.co.za` 的主体被算成 `co.za`，
    #     `*.bank*` 这种规则**匹配不上真正的银行域** —— 是安全漏洞。
    #     改用 Public Suffix List 之后各国后缀都能正确识别。
    _psl_cases = [
        ("bank.co.za", True), ("www.bank.co.za", True),   # 南非
        ("bank.com.ar", True), ("bank.co.il", True),      # 阿根廷 / 以色列
        ("bank.com.cn", True), ("bank.co.uk", True),      # 原本就在表里的
        ("bankofamerica.com", True),
        ("evil.com", False), ("example.org", False),      # 不得误报
    ]
    _psl_bad = []
    for _h, _want in _psl_cases:
        _got = sec._matches(_h, "*.bank*")
        if _got != _want:
            _psl_bad.append(f"{_h}: {_got}（期望 {_want}）")
    r.ok("B2.8 域名主体按 Public Suffix List 解析（co.za 等多级后缀不漏）",
         not _psl_bad, f"不符={_psl_bad or '无'}")

    # ── 端口只从 host:port / [IPv6]:port 剥 ───────────────────────────
    #  ⚠️ 裸 IPv6 里本来就有冒号，用 `re.sub(r":\d+$")` 会把尾组当端口削掉
    #     （`2001:db8::1` → `2001:db8:`，`::1` → `:`），规则直接失效。
    _port_cases = [
        ("2001:db8::1", "2001:db8::1"),        # 裸 IPv6 必须原样保留
        ("::1", "::1"),
        ("[2001:db8::1]:8080", "2001:db8::1"),
        ("example.com:8080", "example.com"),
        ("*.bank.com", "*.bank.com"),
    ]
    _port_bad = []
    for _in, _want in _port_cases:
        _got = sec._normalize_pattern(_in)
        if _got != _want:
            _port_bad.append(f"{_in} → {_got!r}（期望 {_want!r}）")
    r.ok("B2.9 归一化规则时不会把裸 IPv6 的尾组当端口削掉",
         not _port_bad, f"不符={_port_bad or '无'}")

    # ── SSRF：内网地址必须拦（不只是回环）────────────────────────────
    #  ⚠️ 过去只判 is_loopback / is_unspecified，于是下面这些都放行：
    #      10.0.0.5 / 192.168.1.1 / 172.16.0.1（内网）
    #      169.254.169.254（**云厂商实例元数据端点**，拿到就能读走临时凭据）
    #      fe80::/10（链路本地）
    #    这是典型的 SSRF 通道，而且是"看起来不像本机"的那一类。
    _internal = [
        "127.0.0.1", "0.0.0.0", "::1",
        "10.0.0.5", "10.255.255.254",
        "192.168.1.1", "172.16.0.1", "172.31.255.254",
        "169.254.169.254",          # AWS/GCP/Azure 元数据端点
        "fe80::1",                  # 链路本地
        "::ffff:10.0.0.1",          # v4-mapped 内网
    ]
    _not_internal = [
        "8.8.8.8", "1.1.1.1", "93.184.216.34", "example.com",
        # ⚠️ 198.18.0.0/15 是 RFC 2544，Python 算它 private，
        #    但 **Clash / mihomo 默认拿它做 fake-IP**。
        #    拦掉它 = 代理环境下所有站点全废。
        "198.18.1.1",
    ]
    _bad_int = [h for h in _internal if not sec.is_local_host(h)]
    _bad_ext = [h for h in _not_internal if sec.is_local_host(h)]
    r.ok("B2.10 内网/元数据地址被判定为内部（SSRF）",
         not _bad_int, f"漏判={_bad_int or '无'}")
    r.ok("B2.11 公网地址（含代理 fake-IP 段）不误判为内部",
         not _bad_ext, f"误判={_bad_ext or '无'}")

    # ── DNS 解析：主机名解析到内网也要拦 ──────────────────────────────
    #  `internal.corp` / `db.local` 这种名字本身"不像本机"，
    #  但解析出来可能就是 10.x —— 只看字符串等于把内网敞开。
    _resolved_ok = True
    _resolved_detail = ""
    try:
        import socket as _sock
        # 用 localhost 做一次真实解析（一定存在且一定指向回环）
        _hit, _ip = sec.resolved_url_is_internal("http://localhost/")
        _resolved_ok = bool(_hit)
        _resolved_detail = f"localhost -> 内部={_hit} ip={_ip}"
        # 一个必然解析不到的名字：不该因此报错，也不该判成内部
        _miss, _ = sec.resolved_url_is_internal("http://nonexistent.invalid/")
        _resolved_ok = _resolved_ok and (_miss is False)
    except Exception as e:  # noqa: BLE001
        _resolved_ok = False
        _resolved_detail = f"{type(e).__name__}: {e}"
    r.ok("B2.12 主机名解析到内网会被拦（不只比较字符串）",
         _resolved_ok, _resolved_detail)

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
