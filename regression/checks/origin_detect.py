"""页面来源识别：扩展能不能**自己问出** KiraAI 在哪个端口。

背景的自动发现只能在一小撮常见端口上探测（硬扫 65535 个端口不现实），
所以"KiraAI 装在别的端口上"会一直找不到。而用户**总要打开 KiraAI 的页面**
才能用 bot —— 那个页面本身就带着真实的主机名和端口。

这里用最小的 DOM 桩加载**真的 content.js**，验证它：
  · 在本机/私网页面上，会问一句"你是不是 KiraAI"（同源请求），
    是的话把 {host, port, token} 递给后台（走 page_pair 那条现成的路）；
  · 在**公网站点**上，一个请求都不发（隐私边界，必须守住）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from ..harness import PLUGIN_DIR, section, src_safe

TITLE = "页面来源识别（扩展自己问出端口）"

HERE = Path(__file__).resolve().parent
JS = PLUGIN_DIR / "regression" / "js" / "origin_detect.mjs"


async def _run(node: str, **env_extra) -> dict:
    env = dict(os.environ)
    env.update(env_extra)
    env["OD_PLUGIN_DIR"] = str(PLUGIN_DIR)
    proc = await asyncio.create_subprocess_exec(
        node, str(JS), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    text = (out or b"").decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    return {"fatal": (err or b"").decode("utf-8", "replace")[-300:] or text[-300:]}


def run(r) -> None:
    section("扩展自己问出 KiraAI 的端口")
    import shutil
    node = shutil.which("node")
    if not node:
        r.warn("O 组（页面来源识别）", "环境里没有 node")
        return
    if not JS.is_file():
        r.ok("O0 客户端脚本存在", False, f"缺 {JS}")
        return

    async def all_cases():
        return {
            # ① 本机 + 非常见端口 + 确实是 KiraAI
            "local_newport": await _run(node, OD_HOST="127.0.0.1", OD_PORT="9977",
                                        OD_PROTO="http", OD_PAIR_OK="1"),
            # ② 局域网地址 —— 用户从别的机器访问时就是这个
            "lan": await _run(node, OD_HOST="192.168.1.5", OD_PORT="5268",
                              OD_PROTO="http", OD_PAIR_OK="1"),
            # ③ 公网站点 —— 一个请求都不许发
            "public": await _run(node, OD_HOST="example.com", OD_PORT="",
                                 OD_PROTO="https", OD_PAIR_OK="0"),
            # ④ 本机但不是 KiraAI（404）—— 不许乱配对
            "local_not_kira": await _run(node, OD_HOST="127.0.0.1", OD_PORT="3000",
                                         OD_PROTO="http", OD_PAIR_OK="0"),
        }

    cases = asyncio.run(all_cases())

    def pair_of(case):
        for m in case.get("sent") or []:
            if m.get("action") == "page_pair":
                return m.get("payload") or {}
        return None

    c = cases["local_newport"]
    if c.get("fatal"):
        r.ok("O1 本机页面上会问出端口并配对", False, c["fatal"][:200])
    else:
        p = pair_of(c)
        r.ok("O1 本机非常见端口：从页面自己问出 host/port/token",
             bool(p) and p.get("host") == "127.0.0.1" and int(p.get("port") or 0) == 9977
             and p.get("token") == "TOK-9977",
             f"payload={p} fetches={c.get('fetches')}")

    c = cases["lan"]
    if c.get("fatal"):
        r.ok("O2 局域网地址也能问出来", False, c["fatal"][:200])
    else:
        p = pair_of(c)
        r.ok("O2 局域网地址（192.168.x.x）也会问出端口 —— 从别的机器访问时靠它",
             bool(p) and p.get("host") == "192.168.1.5"
             and int(p.get("port") or 0) == 5268,
             f"payload={p}")

    c = cases["public"]
    if c.get("fatal"):
        r.ok("O3 公网站点一个请求都不发", False, c["fatal"][:200])
    else:
        r.ok("O3 **公网站点一个请求都不发**（隐私边界，必须守住）",
             int(c.get("fetches") or 0) == 0 and not (c.get("sent") or []),
             f"fetches={c.get('fetches')} sent={c.get('sent')}")

    c = cases["local_not_kira"]
    if c.get("fatal"):
        r.ok("O4 本机的非 KiraAI 服务不会被误配", False, c["fatal"][:200])
    else:
        r.ok("O4 本机上不是 KiraAI 的服务（404）不会被误配对",
             pair_of(c) is None,
             f"却发出了 {c.get('sent')}")

    # O5：后台确实认得这条件消息（不是发出去没人接）
    bg = src_safe("browser-bridge/background.js")
    r.ok("O5 后台确实处理 page_pair（发出去有人接）",
         'case "page_pair"' in bg,
         "content script 把端口递回去，后台要有对应的处理分支")

    # ── O6：**未连接时必须告诉用户"打开一次这个页面"** ─────────────────
    #    ⚠️ 这一步不是可选提示，而是**主接入路径**：扩展是靠页面自己的
    #       地址（host + port）认出 KiraAI 在哪儿的。用户打开面板那一刻，
    #       接入信息就推给扩展了 —— 不用手填、不用管端口常不常见。
    #       面板上不写这句，用户对着"未连接"只能干瞪眼。
    _panel = src_safe("web/app.js") + src_safe("web/index.html")
    _bad6 = []
    if 'id="connHint"' not in _panel:
        _bad6.append("面板没有放提示的位置")
    if "打开一次这个页面" not in _panel:
        _bad6.append("没告诉用户'打开一次这个页面'")
    if "认出 KiraAI 在哪个端口" not in _panel:
        _bad6.append("没说清为什么要打开（扩展要认端口）")
    r.ok("O6 面板未连接时明确告诉用户'打开一次这个页面'", not _bad6,
         f"问题={_bad6 or '无'}")

