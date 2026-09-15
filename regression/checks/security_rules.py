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
