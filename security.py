"""域名白名单 / 黑名单校验。

规则：
    - 黑名单优先级最高，命中即拒绝（读和写都拒）
    - 白名单为空 → 读操作放行、写操作放行（但受黑名单约束）
    - 白名单非空 → 只有命中白名单的域名允许**写**操作，读操作仍然放行
    - 支持 ``*`` 通配符，如 ``*.example.com``、``*.bank*``
    - 特殊 scheme（``chrome://`` / ``file://`` / ``about:`` 等）默认拒绝

匹配对象是 host（不含端口），从 URL 中解析。扩展侧也会做一次同样的校验，
这里是服务端的权威判定 —— 不能只依赖扩展。
"""

from __future__ import annotations

import fnmatch
import ipaddress
import re
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse

#: 明确不支持的 scheme（扩展也拿不到权限）
BLOCKED_SCHEMES = frozenset({
    "chrome", "chrome-extension", "edge", "about", "devtools",
    "view-source", "data", "javascript", "file", "blob",
})

#: 本机地址的等价写法。
#: ``127.0.0.1`` / ``localhost`` 靠默认黑名单就能挡住，但 ``[::1]``（IPv6
#: 回环）、``2130706433``（十进制 IP）、``0.0.0.0``、``localhost.``（尾点）
#: 指向的是同一个地方 —— 也就是 KiraAI 自己的 WebUI。少挡一个就等于
#: 黑名单里那两条形同虚设。
_LOCAL_HOSTS = frozenset({
    "localhost", "localhost.localdomain", "127.0.0.1", "0.0.0.0", "::1", "::",
})


def _normalize_ipv4_literal(h: str) -> Optional[str]:
    """把浏览器能识别、但 ``ipaddress`` 认不出的 IPv4 写法归一成点分十进制。

    为什么要做：Chromium 会把 ``127.1`` / ``0x7f000001`` / ``0177.0.0.1``
    都当作 ``127.0.0.1``。如果我们只认标准写法，这些就能绕过本机拦截，
    直接访问 KiraAI 自己的 WebUI 端口。

    支持的形式（与 WHATWG URL 规范一致）：
      * 十进制整体：``2130706433``
      * 十六进制整体：``0x7f000001``
      * 八进制整体：``017700000001``
      * 分段简写：``127.1``（补零）、``127.0.1``
      * 每段可为 0x 十六进制 / 0 开头八进制：``0x7f.0.0.1``
    """
    raw = (h or "").strip().rstrip(".")
    if not raw:
        return None

    parts = raw.split(".")
    # 多于 4 段不是合法 IPv4
    if len(parts) > 4:
        return None

    numbers = []
    for p in parts:
        if p == "":
            return None
        try:
            if p.lower().startswith("0x"):
                numbers.append(int(p, 16))
            elif len(p) > 1 and p.startswith("0"):
                numbers.append(int(p, 8))
            else:
                numbers.append(int(p, 10))
        except ValueError:
            return None

    if any(n < 0 for n in numbers):
        return None

    # 最后一段以外的段必须在 0-255；最后一段可以承载剩余位数
    if len(numbers) > 1:
        if any(n > 255 for n in numbers[:-1]):
            return None
        if numbers[-1] > 0xFFFFFF:
            return None

    # 按 WHATWG 规则拼成 32 位
    if len(numbers) == 1:
        if numbers[0] > 0xFFFFFFFF:
            return None
        value = numbers[0]
    else:
        value = numbers[-1]
        for i, n in enumerate(numbers[:-1]):
            value += n << (8 * (3 - i))
        if value > 0xFFFFFFFF:
            return None

    return "%d.%d.%d.%d" % (
        (value >> 24) & 255, (value >> 16) & 255,
        (value >> 8) & 255, value & 255,
    )


def is_local_host(host: str) -> bool:
    """判断 host 是否指向本机（含各种等价写法）。"""
    h = (host or "").strip().strip("[]").rstrip(".").lower()
    if not h:
        return False
    if h in _LOCAL_HOSTS or h.endswith(".localhost"):
        return True

    # 先按浏览器规则归一化，再判断 —— 否则 127.1 / 0x7f000001 / 0177.0.0.1
    # 这类写法会绕过本机拦截
    normalized = _normalize_ipv4_literal(h)
    if normalized:
        h = normalized

    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        return False


def parse_host(url: str) -> Optional[str]:
    """从 URL 提取 host（小写，不含端口）。解析失败返回 None。"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    scheme = (parsed.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES:
        return None

    host = (parsed.hostname or "").lower()
    # 注意：这里**不**剥离 "www." 前缀。
    # 剥离会让 host 与用户规则失去对称性 —— 用户写规则 "www.example.com" 时
    # 却拿 "example.com" 去比，反而误判。www 与裸域的等价由 _matches 的
    # 子域匹配规则自然覆盖（www.example.com 是 example.com 的子域）。
    #
    # 也**不**去掉尾点（``localhost.``）：改写 host 会让它和用户写的规则对不上，
    # 尾点等价交给 _matches 处理。
    return host or None


def parse_scheme(url: str) -> str:
    try:
        return (urlparse(url).scheme or "").lower()
    except Exception:
        return ""


def _normalize_pattern(pattern: str) -> str:
    """把用户填的规则归一化成 host 形式的 glob。"""
    p = (pattern or "").strip().lower()
    if not p:
        return ""
    # 允许用户直接填完整 URL，这里只取 host 部分
    if "://" in p:
        p = urlparse(p).hostname or p
    # 去掉端口
    p = re.sub(r":\d+$", "", p)
    # 去掉路径残留
    p = p.split("/")[0]
    return p


def _matches(host: str, pat: str) -> bool:
    """单条规则的匹配判定。

    这是整个安全校验的核心，规则必须可预测。四条语义，按顺序判定：

    1. **纯字面量域名**（``github.com``）：匹配它自己，也匹配所有子域
       （``gist.github.com``）。按**标签边界**比较，绝不做裸子串 ——
       否则 ``evil-github.com`` 和 ``github.com.evil.com`` 都会命中白名单。

    2. **两端通配的关键词模式**（``*bank*``、``*pay*``）：在整串 host 上做
       子串匹配。用户这样写就是在明确要求「关键词拦截」，误伤面大是其固有代价。

    3. **子域模式**（``*.example.com``）：匹配 ``example.com`` 本身及所有子域，
       按标签边界对齐。特意不走 fnmatch —— fnmatch 的 ``*`` 跨 ``.``，
       会把 ``example.com.evil.com`` 也判成命中，白名单就形同虚设了。

    4. **字面量 + 通配**（``*.bank*``、``github.*``）：通配符左边的字面量必须在
       某个标签的**起始位置**对齐，右边剩余部分再按 fnmatch 比。
       所以 ``*.bank*`` 命中 ``bankofamerica.com``（标签以 bank 开头）、
       ``bank.com.cn``（标签就叫 bank）、``bankofamerica.com.evil.com`` 不命中。

       ⚠️ 这条**不做任意位置子串匹配**，是有意为之：默认黑名单里的
       ``*.bank*`` / ``*.pay*`` 若按子串理解，会顺带拦掉 ``repayment.xyz``、
       ``newspay.cn`` 这类与支付无关的站点，规则行为变得不可预测。
       代价是 ``my-bank-of-china.com``（关键词在标签中间）不再被 ``*.bank*``
       拦下 —— 要覆盖这种写法，请用 ``*bank*``。

    5. 大小写不敏感（host 已在 :func:`parse_host` 里小写化，模式在
       :func:`_normalize_pattern` 里小写化）；尾点写法（``localhost.``）
       与不带尾点等价。
    """
    if not pat:
        return False

    # 尾点只在比较时归一，不去改 parse_host 的返回值（那会让 host 与用户规则失配）
    h = (host or "").rstrip(".")
    if not h:
        return False

    labels = h.split(".")

    # 1) 纯字面量域名：自己 + 所有子域，按标签边界
    if not any(ch in pat for ch in "*?["):
        return h == pat or h.endswith("." + pat)

    # 2) 两端通配的关键词模式：在**单个标签内**做子串匹配。
    #
    #    这里曾经是整串子串匹配，会让白名单 *.example* 顺带放行
    #    example.com.evil.test（"example" 出现在某个标签里就算命中），
    #    等于把写权限授给了 evil.test。改成逐标签比较后，
    #    只有真正含该关键词的那个标签才算命中。
    if pat.startswith("*") and pat.endswith("*") and pat.count("*") == 2:
        # 这两种写法语义**不同**，不能混为一谈：
        #   ``*bank*``   —— 关键词，整串任意位置子串匹配（用户明确要关键词拦截）
        #   ``*.bank*``  —— **域名主体**里含关键词，不允许跨到别的域名
        # 区分依据就是那个点。丢了它，``*.bank*`` 就会被
        # bankofamerica.com.evil.com 这种借关键词过关。
        keyword_form = not pat.lstrip("*").startswith(".")
        core = pat.strip("*").lstrip(".")
        if not core:
            return False
        if keyword_form:
            return core in h
        # ⚠️ 判据是「**域名主体**里含关键词」，而不是「整串里含关键词」。
        #
        #    域名主体 = 去掉 www 之后的注册域，即最后两个标签
        #    （bankofamerica.com、bank.com.cn 这种多级后缀另算）。
        #    只看整串的话，example.com.evil.test 会因为第一个标签是
        #    "example" 而命中 *.example* —— 等于把权限授给了 evil.test。
        #
        #    多级公共后缀（com.cn / co.uk / com.br …）单独处理，
        #    否则 bank.com.cn 的主体会被算成 com.cn，规则反而漏掉。
        MULTI_TLD = {"com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
                     "co.uk", "org.uk", "me.uk", "co.jp", "co.kr",
                     "com.br", "com.tw", "com.hk", "com.sg", "co.in",
                     "com.au", "co.nz", "com.mx", "com.tr"}
        if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_TLD:
            body_labels = labels[-3:]
        elif len(labels) >= 2:
            body_labels = labels[-2:]
        else:
            body_labels = labels
        return any(core in label for label in body_labels)

    # 拆成「通配符左边的字面量」+「含通配符的剩余部分」。
    #
    # "*." 前缀要单独处理：直接按第一个通配符切，字面量会剩下一个孤零零的 "."
    # （"*.example.com" -> literal=".", tail="*"），既丢掉了真实后缀，
    # 又会让 "*.example.com" 匹配不上裸域 "example.com"。所以这里把 "*."
    # 当作「任意子域前缀」整个吃掉，剩下的完整模式留在 tail 里，
    # 由下面的标签边界循环逐段对齐。
    if pat.startswith("*."):
        literal = ""
        tail = pat[2:]
    else:
        wildcard_at = len(pat)
        for ch in "*?[":
            i = pat.find(ch)
            if i != -1:
                wildcard_at = min(wildcard_at, i)
        literal = pat[:wildcard_at]
        tail = pat[wildcard_at:]
        # 字面量里残留的点是分隔符，不属于标签内容（"example.*" -> "example"）
        literal = literal.rstrip(".")

    # 3) "*.example.com" / "*.bank*"：literal 为空，每个标签边界都试一次。
    #    tail 仍可能含通配符（"*.bank*" 的 "bank*"），交给 fnmatch；
    #    也可能不含（"*.example.com" 的 "example.com"），那就是精确后缀匹配，
    #    绝不会命中 "example.com.evil.com"。
    if not literal:
        # tail 里**不含**通配符时（"*.example.com"）→ 精确后缀匹配，
        # 天然不会命中 example.com.evil.com
        if not any(ch in tail for ch in "*?["):
            return h == tail or h.endswith("." + tail)
        # tail 含通配符时（"*.bank*" / "*.example*"）—— 注意 tail 里**没有点**，
        # 说明这是「一个标签」的模式（关键词类规则）。那就只比对域名主体
        # （最后两个标签，例如 bankofamerica.com），不能拿整串去比，
        # 否则 bankofamerica.com.evil.com 也会命中。
        if "." not in tail:
            body = ".".join(labels[-2:]) if len(labels) >= 2 else labels[-1]
            if _glob_match_segment(body, tail):
                return True
            # 也允许纯单标签域名（如内网名 "bank"）
            return _glob_match_segment(labels[-1], tail)
        # tail 里带点（"*.sub.example.com"）→ 逐标签起点试后缀匹配
        for i in range(len(labels)):
            rest = ".".join(labels[i:])
            if _glob_match_segment(rest, tail):
                return True
        return False

    # 4) "bank*" / "github.*"：字面量必须从某个标签起始处对齐。
    #
    #    ⚠️ 剩余部分**不能**直接用 fnmatch —— fnmatch 的 `*` 会吃掉点号，
    #       于是 `github.*` 会匹配 `github.com.evil.test`（把 `*` 当成
    #       "com.evil.test"）。这和之前 `*.example*` 的问题是同一个。
    #       所以：
    #         - tail 里含点 → 要求**标签数一致**再逐标签比（不跨域名）
    #         - tail 只是通配（`*`）→ 允许一个标签，但不允许跨到别的域名
    for i in range(len(labels)):
        rest = ".".join(labels[i:])
        if not rest.startswith(literal):
            continue
        remainder = rest[len(literal):]
        if not tail:
            if remainder == "":
                return True
            continue
        # 去掉前导点后按标签逐段比
        rem_labels = [x for x in remainder.split(".") if x]
        tail_labels = [x for x in tail.split(".") if x]
        if len(rem_labels) != len(tail_labels):
            continue
        if all(fnmatch.fnmatch(a, b) for a, b in zip(rem_labels, tail_labels)):
            return True

    return False


def _glob_match_segment(text: str, pattern: str) -> bool:
    """与 :func:`_glob_match` 相同，但要求通配符**不跨越标签分隔符**。

    用于 ``*.bank*`` 这类规则的收尾匹配：``example.com.evil.test`` 从
    ``example`` 起虽然以 ``example`` 开头，但后面还有 ``.com.evil.test``
    这些**其它域名**的标签，不应该算命中。
    """
    if not pattern:
        return text == ""
    t_labels = text.split(".")
    p_labels = pattern.split(".")
    if len(t_labels) != len(p_labels):
        return False
    return all(fnmatch.fnmatch(t, p) for t, p in zip(t_labels, p_labels))


def _glob_match(text: str, pattern: str) -> bool:
    """通配符匹配的收口入口。

    ``fnmatch`` 的 ``*`` 跨 ``.``，这正是子域规则容易出错的地方，所以只允许
    它在已经确定好对齐位置的**剩余部分**上使用 —— 对齐由调用方负责。
    """
    if not pattern:
        return text == ""
    return fnmatch.fnmatch(text, pattern)


def _match_any(host: str, patterns: Iterable[str]) -> Optional[str]:
    for raw in patterns or []:
        pat = _normalize_pattern(raw)
        if not pat:
            continue
        if _matches(host, pat):
            return raw
    return None


def check_url(
    url: str,
    allowed: Iterable[str] = (),
    blocked: Iterable[str] = (),
    for_write: bool = False,
) -> Tuple[bool, str]:
    """校验一个 URL 是否允许被访问。

    Args:
        url: 目标地址
        allowed: 白名单规则
        blocked: 黑名单规则
        for_write: 是否为写操作（白名单非空时，写操作必须命中白名单）

    Returns:
        ``(ok, reason)`` —— ``ok`` 为 False 时 ``reason`` 是给用户/日志看的说明。
    """
    if not url:
        return False, "URL 为空"

    scheme = parse_scheme(url)
    if scheme in BLOCKED_SCHEMES:
        return False, f"不支持的页面类型（{scheme}://），扩展无权访问"

    host = parse_host(url)
    if not host:
        return False, f"无法解析域名: {url}"

    # 本机地址优先拦，且不看黑名单配置 —— 默认黑名单只写了 127.0.0.1 和
    # localhost 两种写法，[::1] / 2130706433 / 0.0.0.0 / localhost. 都能绕过去，
    # 而它们指向的是 KiraAI 自己的 WebUI 端口。
    if is_local_host(host):
        return False, (
            f"域名 {host} 指向本机地址，已拒绝（避免 AI 操作 KiraAI 自身的服务）"
        )

    blocked_hit = _match_any(host, blocked)
    if blocked_hit:
        return False, f"域名 {host} 命中黑名单规则「{blocked_hit}」，已拒绝"

    allowed_list: List[str] = [a for a in (allowed or []) if str(a).strip()]

    if not allowed_list:
        # 白名单空：读写都放行（已过黑名单）
        return True, ""

    allowed_hit = _match_any(host, allowed_list)
    if allowed_hit:
        return True, ""

    if for_write:
        return False, (
            f"域名 {host} 不在白名单内，写操作被拒绝。"
            f"如需允许，请在插件配置的「域名白名单」中添加"
        )
    # 读操作在白名单非空时仍然放行
    return True, ""


def check_write_targets(urls: Iterable[str], allowed: Iterable[str],
                        blocked: Iterable[str]) -> Tuple[bool, str]:
    """批量校验写操作涉及的多个 URL，任一失败即整体拒绝。"""
    for url in urls:
        ok, reason = check_url(url, allowed, blocked, for_write=True)
        if not ok:
            return False, reason
    return True, ""
