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
  // ⚠️ 显式的 **wss://** 输入：用户会直接粘完整地址，这条路径
  //    与"裸主机名"不同（scheme 是**显式**给的，不靠回环判定推导），
  //    必须也走通并保留 wss。
  ["wss://example.com", "wss:", "example.com:5267"],
  ["wss://example.com:9443", "wss:", "example.com:9443"],
  ["ws://127.0.0.1:5267", "ws:", "127.0.0.1:5267"],
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

// ── 端口值域：非法端口必须**明确抛错**，不能拼出非法 URL ──────────
//    不校验的话会得到 `ws://127.0.0.1:-1/...` 这种地址，
//    `new WebSocket()` 抛 "Invalid URL"，用户根本看不出是端口填错了。
//    ⚠️ 0 / "" / 空白 会被 `port || DEFAULT_PORT` 当成"没填"而落到默认端口
//       —— 这是**有意的**（面板清空端口 = 用默认值），不算非法，
//       所以不列在这里；它们由下面"应落到默认端口"那一组覆盖。
const badPorts = [-1, 70000, "abc", "52a67", "1e4", "1.5"];
for (const p of badPorts) {
  let threw = false;
  try { buildWsUrl("127.0.0.1", p, "T"); }
  catch (e) { threw = /端口无效/.test(e.message); }
  if (!threw) bad.push("port=" + JSON.stringify(p) + " 竟然没被拒绝");
}
// 合法边界值必须**照常通过**，而且**端口不能被打折** ——
// 只验证"能解析成 URL"是不够的：端口被悄悄换成默认值（5267）
// 时 URL 依然合法，连接却会指向错误的端口，而检查照样绿。
//
// ⚠️ 不能直接断言 `u.port === String(p)`：URL 解析会把协议的**默认端口**
//    省略掉（ws 的默认端口是 80），所以 `:80` 解析出来 u.port 是空串。
//    要断言的是"落到那个端口的**实际值**与期望一致"，默认端口按空串折算。
const DEFAULT_FOR_SCHEME = { "ws:": "80", "wss:": "443" };
const goodPorts = [1, 80, 5267, 65535];
for (const p of goodPorts) {
  try {
    const u = new URL(buildWsUrl("127.0.0.1", p, "T"));
    const actual = u.port || DEFAULT_FOR_SCHEME[u.protocol] || "";
    if (actual !== String(p)) {
      bad.push("port=" + p + " 被改成了 " + (u.port || "(空)")
               + "（实际生效 " + actual + "）");
    }
  }
  catch (e) { bad.push("port=" + p + " 被误拒: " + e.message); }
}
// "没填"的形态应落到默认端口（不是报错）
for (const p of [0, "", "  "]) {
  try {
    const u = new URL(buildWsUrl("127.0.0.1", p, "T"));
    if (u.port !== "5267") bad.push("port=" + JSON.stringify(p)
      + " 应落到默认 5267，实际 " + u.port);
  } catch (e) {
    bad.push("port=" + JSON.stringify(p) + " 不该抛错: " + e.message);
  }
}

if (bad.length) { console.log(bad.join(" ; ")); process.exit(1); }
console.log("OK");
'''


import os as _os
import uuid as _uuid
import shutil as _sh
import subprocess as _sp
import tempfile as _tf
from pathlib import Path

from ..harness import (PLUGIN_DIR, load_module, section, src,
                       strip_comments_only)

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
    # 扩展下载：跨协议不能带出 cookie。
    # ⚠️ 只在下**载处理器内部**找选项 —— 全文搜会命中注释或别处，
    #    下载里删掉了也照样通过。
    # ⚠️ 判据是"**会不会跨协议泄漏凭据**"，不是"用不用某个具体选项"：
    #    · 最初用 `redirect:"error"`（完全不跟随）—— 最保守，但 CDN/短链
    #      的正常重定向会让**下载直接失败**；
    #    · 试过 `redirect:"manual"` 自己跟 —— **在浏览器里走不通**：
    #      manual 返回 opaqueredirect（status=0、响应头全空），
    #      **读不到 Location**，等于换个写法失败；
    #    · 现在用 `redirect:"follow"` 让浏览器跟（它每一跳会自己重算
    #      Cookie：Secure cookie 绝不上 HTTP，域不匹配的不发），
    #      **事后检查最终 URL**：起始是 HTTPS 却落在 HTTP 上就中止。
    #    所以这里守两条：凭据按协议条件 + 有"最终落在 HTTP 就中止"的检查。
    cap = src("browser-bridge/capabilities.js")
    _dl = cap.split("async function downloadViaSession(")[-1].split("// ───")[0] \
        if "async function downloadViaSession(" in cap else ""
    # 只剥注释（保留字符串，判据要看选项字面量）
    _dl_code = strip_comments_only(_dl)
    _no_manual = 'redirect: "manual"' not in _dl_code
    _guards_final = ("resp.url" in _dl_code
                     and "finalIsHttps" in _dl_code)
    r.ok("B2.5 下载不会跨协议泄漏凭据（不用读不到 Location 的 manual）",
         _no_manual and _guards_final,
         f"manual 未用={_no_manual}；有最终 URL 协议检查={_guards_final}"
         f"（manual 返回 opaqueredirect：status=0、响应头全空）")
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
            # ⚠️ 与 static_audit 保持一致：用公开的 uuid4().hex，
            #    不碰 tempfile 的私有 _get_candidate_names()。
            f"_kira_ws_{_os.getpid()}_{_uuid.uuid4().hex[:12]}.mjs")
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

    # ── 开关必须一路传到底（不能有"漏一段"的地方）────────────────────
    #  ⚠️ `check_url` 的 `local_access` 默认是 True（本机默认放行）。
    #     任何**封装了 check_url** 的函数如果不显式接收并转发这个参数，
    #     就会静默地用默认值 —— 用户把配置关掉了，那条路径照样放行。
    #     这正是"加了开关但开关没生效"的经典形态。
    _sec_src = src("security.py")
    # ⚠️ 要同时认 `def` 与 `async def` —— 只写 `def` 的话，
    #    封装函数改成异步之后就**扫不到**了，这条"开关必须一路传到底"
    #    的检查会静默失效（变成空集合 → 永远通过，假绿）。
    # ⚠️ 用 **AST** 逐个函数检查，不要用"从函数开头切 2000 字符"那种宽段 ——
    #    切太宽时，**后面那个函数**里的 `local_access=local_access`
    #    会给前面这个漏转发的封装"背书"（假通过）。
    #    这里用 AST 拿到每个函数体里**它自己**的 check_url 调用，只看那些调用的关键字。
    import ast as _ast
    _pass_bad = []
    _checked_names = []
    try:
        _tree = _ast.parse(_sec_src)
    except SyntaxError as e:
        _tree = None
        r.ok("C6g security.py 可解析", False, f"SyntaxError: {e}")

    def _visits(node):
        """遍历函数体，但**不进入嵌套函数**（它们的调用不算这个函数的）。"""
        for child in _ast.iter_child_nodes(node):
            if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                  _ast.ClassDef)):
                continue
            yield child
            yield from _visits(child)

    if _tree is not None:
        for fn in _ast.walk(_tree):
            if not isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            if fn.name == "check_url":
                continue
            _calls = [c for c in _visits(fn)
                      if isinstance(c, _ast.Call)
                      and getattr(c.func, "id", None) == "check_url"]
            if not _calls:
                continue          # 不封装 check_url，管不着
            _checked_names.append(fn.name)
            _has_param = any(a.arg == "local_access" for a in fn.args.args)
            # 每个 check_url 调用都必须显式传 local_access=local_access
            _fwd = all(any(kw.arg == "local_access"
                           and getattr(kw.value, "id", None) == "local_access"
                           for kw in c.keywords)
                       for c in _calls)
            if not (_has_param and _fwd):
                _pass_bad.append(fn.name)
    r.ok("C6g 所有封装 check_url 的函数都显式转发 local_access",
         not _pass_bad,
         f"未转发={_pass_bad or '无'}（会让开关在那条路径上失效）；"
         f"共检查 {len(_checked_names)} 个封装：{_checked_names}")

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
    #  ⚠️ 不要依赖"真实解析 localhost"：解析结果取决于运行环境
    #     （有的沙箱没有 DNS，有的把一切解析成代理 fake-IP），
    #     用例会随环境飘。这里**换掉解析器**，用可控映射来验。
    _real_gai = None
    try:
        import socket as _sock

        def _fake_gai(host, port=None, *a, **kw):
            if host == "internal.example":
                return [(2, 1, 6, "", ("10.0.0.5", 0))]
            if host == "public.example":
                return [(2, 1, 6, "", ("93.184.216.34", 0))]
            raise _sock.gaierror(f"fake: 不解析 {host}")

        _real_gai = _sock.getaddrinfo
        _sock.getaddrinfo = _fake_gai

        # ① 名字"不像本机"但解析到内网 → 必须判为内部
        _hit, _ip = sec.resolved_url_is_internal("http://internal.example/")
        # ② 解析到公网 → 不得误判
        _pub, _ = sec.resolved_url_is_internal("http://public.example/")
        # ③ 解析不到 → 不报错、也不算内部
        _miss, _ = sec.resolved_url_is_internal("http://nonexistent.invalid/")
        _resolved_ok = bool(_hit) and (_pub is False) and (_miss is False)
        _resolved_detail = (f"internal.example→内部={_hit}({_ip})；"
                            f"public.example→内部={_pub}；"
                            f"解析失败→内部={_miss}")
    except Exception as e:  # noqa: BLE001
        _resolved_ok = False
        _resolved_detail = f"{type(e).__name__}: {e}"
    finally:
        if _real_gai is not None:
            import socket as _s2
            _s2.getaddrinfo = _real_gai
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
    # ── 本机 / 内网：默认放行，可配置收紧 ──────────────────────────
    #
    # ⚠️ 这里过去断言"本机一律被拒"。现在**默认是允许的** ——
    #    插件定位就是让 AI 帮用户操作浏览器，localhost:3000 这类本地开发
    #    服务器和 KiraAI 自己的面板都是正常目标，默认拦掉会让"帮我看看
    #    我本地的页面"直接不可用。需要收紧的人在配置里关掉 local_access。
    #    所以这条用例改成**两个方向都验**：
    #      · 默认（local_access=True）→ 本机与内网必须放行；
    #      · 收紧（local_access=False）→ 本机/内网/元数据必须拒绝。
    _local_urls = ["http://127.0.0.1:5267/overview", "http://localhost:3000/",
                   "http://127.1:5267/", "http://[::1]:9000/",
                   "http://192.168.1.1/", "http://10.0.0.5/"]
    _open_bad = [u for u in _local_urls
                 if not sec.check_url(u, local_access=True)[0]]
    r.ok("C6 默认放行本机/内网地址（插件定位就是给 AI 操作浏览器）",
         not _open_bad, f"被误拦={_open_bad or '无'}")

    _strict_bad = [u for u in _local_urls
                   if sec.check_url(u, local_access=False)[0]]
    r.ok("C6b 关闭「允许访问本机/内网」后，本机/内网一律拒绝",
         not _strict_bad, f"漏放={_strict_bad or '无'}")

    # 收紧模式下云元数据端点也必须拒（SSRF 的关键目标）
    _meta_ok, _ = sec.check_url("http://169.254.169.254/latest/meta-data/",
                                local_access=False)
    r.ok("C6c 收紧模式下云元数据端点被拒", not _meta_ok)

    # 收紧后公网站点不受影响
    _pub_ok, _ = sec.check_url("https://example.com/", local_access=False)
    r.ok("C6d 收紧模式不影响公网站点", _pub_ok)

    # ⚠️ 默认黑名单**不能**再包含 127.0.0.1 / localhost ——
    #    黑名单优先级最高，留着它们会让「允许访问本机」这个开关形同虚设：
    #    用户明明开着开关，本机地址还是被黑名单拦掉。
    #    本机是否允许，统一由 local_access 决定。
    import json as _json
    _sch = _json.loads(src("schema.json"))
    _dflt_blocked = (_sch.get("blocked_domains") or {}).get("default") or []
    _conflict = [x for x in _dflt_blocked
                 if x.strip().lower() in ("127.0.0.1", "localhost", "::1",
                                          "0.0.0.0")]
    r.ok("C6e 默认黑名单不含本机条目（否则「允许访问本机」会失效）",
         not _conflict, f"冲突={_conflict or '无'}")

    # 用**真实默认配置**跑一遍端到端：开关开着就必须真的能访问
    _end_to_end = []
    for _u in ("http://127.0.0.1:5267/overview", "http://localhost:3000/"):
        if not sec.check_url(_u, blocked=_dflt_blocked, local_access=True)[0]:
            _end_to_end.append(_u)
    r.ok("C6f 用默认配置端到端：本机地址确实可访问",
         not _end_to_end, f"仍被拦={_end_to_end or '无'}")
