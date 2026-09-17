"""按 KiraAI 插件规范核对**交付形态**（不依赖框架源码，可离线跑）。

这里验证的是"交给用户的东西对不对"：
  · 包结构（框架靠 __init__.py 旁边的 manifest.json 定位 plugin_id）
  · manifest 字段与 identity（必须是原来的 plugin_id，不能变成第二个插件）
  · 入口类是 BasePlugin 子类、实现了两个 async 抽象方法
  · 注册装饰器的用法（tool / ws / api / page）
  · 路由路径与扩展侧一致
  · schema.json / web / icon 这些资源文件实在

⚠️ 为什么不直接 import 框架来验：框架源码不在插件仓库里，
   回归套件必须能在**只有插件**的环境里跑。需要框架才能验的部分
   （钩子三参调用、build_fields 解析）写进了 SECURITY_DESIGN/README 的
   核对记录里，并在发布前手工对最新框架跑过一次。
"""
from __future__ import annotations

import ast as _ast2
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from ..harness import PLUGIN_DIR, section, src_safe

TITLE = "KiraAI 规范符合性（交付形态）"

#: manifest 必需字段（对照官方内置插件的 manifest）
REQUIRED_MANIFEST = ("plugin_id", "display_name", "version", "author", "description")

#: 本插件**必须**沿用的 plugin_id —— 改了就等于变成另一个插件，
#: 用户的既有配置/数据目录会全部对不上。
EXPECTED_PLUGIN_ID = "headless_browser"


#: 真跑 Playwright 桩的 cookie 语义（往返 / 副本 / 同域子域过滤）
#  ⚠️ 桩坏了通常**不报错** —— 它静默返回一个偏宽松的结果，依赖它的检查
#    会"全绿但什么都没验证"。所以这里不看源码、直接跑行为。
_STUB_COOKIE_PROBE = r'''
import asyncio, json, os, sys
PLUGIN = os.environ["KIRA_PLUGIN_DIR"]
sys.path.insert(0, PLUGIN)
sys.path.insert(0, os.path.join(PLUGIN, "regression", "stubs"))
from playwright.async_api import FakeContext

async def main():
    out = {}
    ctx = FakeContext()
    await ctx.add_cookies([
        {"name": "v6",  "value": "1", "domain": "::1",          "path": "/"},
        {"name": "v4",  "value": "1", "domain": "127.0.0.1",    "path": "/"},
        {"name": "sub", "value": "1", "domain": ".example.com", "path": "/"},
    ])
    out["no_url"] = sorted(c["name"] for c in await ctx.cookies()) == ["sub", "v4", "v6"]
    out["roundtrip"] = sorted(
        c["name"] for c in await ctx.cookies("http://127.0.0.1:8080/")) == ["v4"]
    out["ipv6"] = sorted(
        c["name"] for c in await ctx.cookies("http://[::1]:8080/")) == ["v6"]
    out["subdomain"] = sorted(
        c["name"] for c in await ctx.cookies("https://a.example.com/x")) == ["sub"]
    out["no_match"] = sorted(
        c["name"] for c in await ctx.cookies("https://nope.cn/")) == []
    # 副本：改返回值不能影响内部状态
    got = await ctx.cookies()
    got[0]["value"] = "MUTATED"
    out["copy"] = all(c["value"] != "MUTATED" for c in await ctx.cookies())
    print("RESULT:" + json.dumps(out))

asyncio.run(main())
'''


def _run_probe(script_src: str, timeout: int = 120) -> dict:
    """在子进程里跑一段探针脚本，返回它 RESULT: 后面的 JSON。"""
    fd, path = tempfile.mkstemp(suffix="_probe.py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script_src)
        env = dict(os.environ)
        env["KIRA_PLUGIN_DIR"] = str(PLUGIN_DIR)
        env["KIRA_FW_DIR"] = os.environ.get("KIRA_FW_DIR", "/tmp/kiraai_latest")
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           env=env, timeout=timeout)
        line = next((ln for ln in (p.stdout or "").splitlines()
                     if ln.startswith("RESULT:")), "")
        if not line:
            return {"__error__": (p.stderr or p.stdout or "")[-300:]}
        return json.loads(line[len("RESULT:"):])
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run(r) -> None:
    section("A. 包结构（框架据此刻画 plugin_id）")

    r.ok("A1 __init__.py 存在（插件入口）",
         (PLUGIN_DIR / "__init__.py").is_file())
    r.ok("A2 manifest.json 与 __init__.py **同级**",
         (PLUGIN_DIR / "manifest.json").is_file(),
         "框架用 `module.__file__.parent / manifest.json` 定位 plugin_id，"
         "放错层级会导致 identity 认不出来")

    init = src_safe("__init__.py")
    r.ok("A3 __init__.py 导出入口类 BrowserPlugin",
         "BrowserPlugin" in init,
         "__init__.py 必须能让框架 `from <pkg> import <Class>` 拿到入口类")

    # 目录名：真装时框架会落到 plugins_dir/<plugin_id>/
    # （开发副本可能叫别的，这里只做提示，不算失败）
    if PLUGIN_DIR.name != EXPECTED_PLUGIN_ID:
        r.note(f"   当前目录名 {PLUGIN_DIR.name!r}；正式安装时会落到 "
               f"plugins_dir/{EXPECTED_PLUGIN_ID}/（本项不作失败）")

    section("B. manifest 字段与身份")

    try:
        mf = json.loads(src_safe("manifest.json"))
        if not isinstance(mf, dict):
            # 合法 JSON 但不是对象（比如是个数组/字符串）→ 当作无效处理，
            # 否则下面所有 mf.get(...) 都会抛 AttributeError。
            raise ValueError(f"manifest 顶层不是对象（{type(mf).__name__}）")
    except Exception as e:
        r.ok("B0 manifest.json 是合法 JSON", False, f"{type(e).__name__}: {e}")
        # ⚠️ **不要 return** —— 一个坏掉的 manifest 不该把
        #    C~G 段（入口点、路由、资源、文档一致性）全部屏蔽掉：
        #    那些检查与 manifest 是否合法**互相独立**，
        #    早退会让它们的结果完全看不见（报告上只有一条 B0 失败）。
        #    赋空对象继续走，让后面每一段各自报自己的结果。
        mf = {}

    miss = [k for k in REQUIRED_MANIFEST if not mf.get(k)]
    r.ok("B1 manifest 必备字段齐全", not miss, f"缺={miss or '无'}")

    r.ok("B2 plugin_id 沿用原值（不会变成第二个插件）",
         mf.get("plugin_id") == EXPECTED_PLUGIN_ID,
         f"实际={mf.get('plugin_id')!r}；改掉的话用户既有配置/数据全部对不上")

    icon = mf.get("icon")
    r.ok("B3 manifest.icon 指向的文件实在",
         bool(icon) and (PLUGIN_DIR / str(icon)).is_file(),
         f"icon={icon!r}")

    # locales 可选，但给了就必须是 dict
    if mf.get("locales") is not None:
        r.ok("B4 locales 是对象且含 zh",
             isinstance(mf["locales"], dict) and "zh" in mf["locales"])
    else:
        r.note("   manifest 未提供 locales（可选）")

    section("C. 入口类与基类契约")

    main = src_safe("main.py")
    r.ok("C1 继承 BasePlugin",
         re.search(r"class\s+\w+\s*\(\s*BasePlugin\s*\)", main) is not None,
         "框架要求入口类继承 BasePlugin")
    r.ok("C2 实现 async initialize()",
         re.search(r"async\s+def\s+initialize\s*\(\s*self\s*\)", main) is not None,
         "BasePlugin 的抽象方法，框架加载时 await 它")
    r.ok("C3 实现 async terminate()",
         re.search(r"async\s+def\s+terminate\s*\(\s*self\s*\)", main) is not None,
         "BasePlugin 的抽象方法，插件停用时 await 它")

    section("D. 注册装饰器（对照框架 RegisterDeco 的签名）")

    # 框架签名：
    #   tool(name, description, params)
    #   ws(path, auth=True)
    #   api(method, path, auth=True, **kwargs)
    #   page(route, auth=True, menu=None)
    tools = re.findall(r"@register\.tool\(", main)
    r.ok("D1 用 @register.tool 暴露 LLM 能力", len(tools) >= 10,
         f"共 {len(tools)} 个工具")

    ok_ws = re.search(r'@register\.ws\(\s*"/[^"]*"\s*,\s*auth\s*=', main) is not None
    r.ok("D2 @register.ws 用关键字传 auth（框架签名: ws(path, auth=True)）",
         ok_ws)

    ok_api = re.search(
        r'@register\.api\(\s*"(GET|POST|PUT|DELETE|PATCH)"\s*,\s*"/[^"]*"',
        main) is not None
    r.ok("D3 @register.api 首位是 HTTP 方法（框架签名: api(method, path, ...)）",
         ok_api)

    ok_page = re.search(r'@register\.page\(\s*"/[^"]*"', main) is not None
    r.ok("D4 @register.page 首位是路由（框架签名: page(route, ...)）", ok_page)
    r.ok("D5 面板返回 PluginPage",
         "PluginPage.from_folder" in main or "PluginPage.from_url" in main
         or "PluginPage.from_html" in main,
         "框架要求页面端点返回 PluginPage 对象")

    section("E. 路由路径一致性")

    pid = mf.get("plugin_id") or EXPECTED_PLUGIN_ID
    # 扩展侧写死的 WS 路径
    proto = src_safe("browser-bridge/protocol.js")
    m = re.search(r'WS_PATH\s*=\s*["\']([^"\']+)', proto)
    ext_path = m.group(1) if m else ""
    want = f"/ws/plugin/{pid}/bridge"
    r.ok("E1 扩展连接的 WS 路径 == 框架注册的路径",
         ext_path == want, f"扩展={ext_path!r} 期望={want!r}")
    r.ok("E2 插件汇报的 ws_path 与之一致",
         f"/ws/plugin/{{PLUGIN_ID}}/bridge" in main or want in main)  # noqa: F541  （要的就是字面量 {PLUGIN_ID}）

    section("F. 随包资源")

    r.ok("F1 schema.json 存在且是合法 JSON",
         (PLUGIN_DIR / "schema.json").is_file())
    try:
        sch = json.loads(src_safe("schema.json"))
        r.ok("F2 schema 是对象且非空", isinstance(sch, dict) and bool(sch),
             f"{len(sch)} 个字段")
    except Exception as e:
        r.ok("F2 schema 是对象且非空", False, f"{type(e).__name__}: {e}")

    r.ok("F3 面板页面文件存在（@register.page 指向的目录）",
         (PLUGIN_DIR / "web" / "index.html").is_file(),
         "PluginPage.from_folder('./web') 需要 web/index.html")

    sec = PLUGIN_DIR / "SECURITY_DESIGN.md"
    r.ok("F4 有独立的安全设计说明（供自动审阅读，避免误报）", sec.is_file())

    section("G. 回归套件自身的文档与登记表一致")

    # ⚠️ 这条是补一次"文档漂移"的教训：`regression/README.md` 里的
    #    checks/ 清单漏了 6 个已登记模块（claims / spec_compliance /
    #    vlm_describe / lost_features / timeout_semantics / execjs_gates），
    #    而**没有任何检查盯着它** —— 于是漂了很久才被外部审查发现。
    #    登记表（checks/__init__.py 的 ALL_CHECKS）是唯一事实来源。
    try:
        import importlib
        _pkg = importlib.import_module("regression.checks")
        _registered = {m.__name__.rsplit(".", 1)[-1] for m in _pkg.ALL_CHECKS}
    except Exception as e:
        _registered = set()
        r.ok("G1 能读到检查登记表", False, f"{type(e).__name__}: {e}")

    if _registered:
        _readme = src_safe("regression/README.md")
        _missing = sorted(n for n in _registered
                          if f"{n}.py" not in _readme)
        r.ok("G1 regression/README.md 的 checks 清单覆盖所有已登记模块",
             not _missing,
             f"README 里缺={_missing or '无'}（已登记 {len(_registered)} 个）")

    # ── G2 harness 只放"共享夹具"，不放检查 ──────────────────────
    #  ⚠️ 补一次自己犯的错：把函数从某个 check 模块提取到 harness 时，
    #    误把**该模块的检查代码**（`_judge_*` / `run()`）也带了过去 ——
    #    harness 里于是多了一份"永远不会被调用"的重复实现。
    #    它不会报错（没人 import 它），但会让人以为检查在那里跑。
    #    ⚠️ 判据必须排除**注释/文档字符串** —— harness 的说明里就写着
    #       "def run(r) -> None:   # r 是 Report 实例" 这样的示例，
    #       裸文本匹配会把它当成真的检查实现（自我误报）。
    #       用 AST 只看**真实的函数定义**。
    _bad = []
    try:
        _tree = _ast2.parse(src_safe("regression/harness.py") or "")
        for _n in _ast2.walk(_tree):
            if isinstance(_n, (_ast2.FunctionDef, _ast2.AsyncFunctionDef)):
                if _n.name == "run" or _n.name.startswith("_judge_"):
                    _bad.append(f"{_n.name} (line {_n.lineno})")
    except SyntaxError:
        _bad = ["<harness.py 语法错误，无法解析>"]
    r.ok("G2 harness.py 只放共享夹具（不含检查实现）",
         not _bad,
         f"发现检查代码={_bad or '无'}（提取函数时误带过来的？）")

    # ── G3 前置读取必须走 safe 变体（缺文件不中断整组）──────────────
    #  ⚠️ 补一类反复出现的问题：检查模块读文件时用裸 `src()`，
    #    文件缺失就抛异常 → **整个检查组中断**，报告上只剩一条笼统失败，
    #    看不出真正缺了什么、也看不到后面本该跑的检查。
    #    （CR 就 static_audit 点过一次；我用"逐个删文件跑全套"的矩阵
    #      自查时又抓出 7 处，横跨 5 个模块。）
    #  判据两条：
    #    ① harness 的读取便捷函数内部必须用 *_safe；
    #    ② 检查模块里不许出现**裸 src(**（要用 src_safe）。
    _hs2 = src_safe("regression/harness.py")
    _hs_bad = []
    try:
        _t3 = _ast2.parse(_hs2 or "")
        _READERS = {"main_src", "headless_src", "extension_src", "bridge_src",
                    "schema", "manifest", "ext_manifest", "ext_file",
                    "load_json_safe"}
        for _n in _ast2.walk(_t3):
            if isinstance(_n, (_ast2.FunctionDef, _ast2.AsyncFunctionDef)) \
                    and _n.name in _READERS:
                _seg = _ast2.get_source_segment(_hs2, _n) or ""
                # 允许 safe 名（src_safe / load_json_safe）——
                # 用负向断言避免把 src_safe 当成裸 src
                if re.search(r'(?<![_\w])src\(', _seg) or \
                        re.search(r'(?<![_\w])load_json\(', _seg):
                    _hs_bad.append(_n.name)
    except SyntaxError:
        _hs_bad = ["<解析失败>"]
    r.ok("G3a harness 的读取便捷函数都走 safe 变体",
         not _hs_bad,
         f"仍用裸读取={_hs_bad or '无'}（缺文件会中断整组检查）")

    # ② 检查模块里不许有裸 src(
    _mods_bad = {}
    for _f in sorted((PLUGIN_DIR / "regression" / "checks").glob("*.py")):
        # ⚠️ 跳过本文件自己：G3b 的**报错文案**里就写着 "src()"
        #    （"检查模块不用裸 src()"），不排除的话它会命中自己（自指）。
        if _f.name in ("__init__.py", "spec_compliance.py"):
            continue
        _txt = _f.read_text(encoding="utf-8")
        _hits = [i + 1 for i, _ln in enumerate(_txt.splitlines())
                 if re.search(r'(?<![_\w])src\(', _ln)
                 and not _ln.strip().startswith("#")]
        if _hits:
            _mods_bad[_f.name] = _hits
    r.ok("G3b 检查模块不用裸 src()（一律 src_safe）",
         not _mods_bad,
         f"裸 src() 位置={_mods_bad or '无'}（缺文件时会中断整组）")

    # ③ 也不许**裸 open()/read_text** 读仓库里的固定文件
    #  ⚠️ G3b 只堵了 `src()` 一条路。实际上"中断整组"的来源还有裸 `open(...)`
    #    —— 用"逐个删文件跑全套"矩阵时，这类又抓出 4 处（security.py 的
    #    load_module、playwright 桩的 import、content.js / manifest.json 的
    #    open）。与其一个个抓，不如把判据升级成"**读固定文件就走读取器**"。
    _bare_open = {}
    for _f in sorted((PLUGIN_DIR / "regression" / "checks").glob("*.py")):
        if _f.name in ("__init__.py", "spec_compliance.py"):
            continue
        _hits = []
        for _i, _ln in enumerate(_f.read_text(encoding="utf-8").splitlines(), 1):
            _code = _ln.split("#", 1)[0]
            # ⚠️ 只管"读**固定字面量**路径"这种形态 —— 必须跟一个引号。
            #    写成 `(PLUGIN_DIR / f).read_text()` 的是**遍历 glob 结果**
            #    （f 来自已存在的文件列表），文件被删就不会出现在列表里，
            #    本来就不会崩。不区分的话会误报（首跑就误报 3 处）。
            if re.search(r'\bopen\(\s*(PLUGIN_DIR|EXT_DIR)\s*/\s*["\']',
                         _code) or \
                    re.search(r'\((PLUGIN_DIR|EXT_DIR)\s*/\s*["\']'
                              r'[^)]*\)\.read_text\(', _code):
                _hits.append(_i)
        if _hits:
            _bare_open[_f.name] = _hits
    r.ok("G3c 检查模块读固定文件不用裸 open()/read_text（走 src_safe 等）",
         not _bare_open,
         f"裸读取位置={_bare_open or '无'}（缺文件时会中断整组，"
         f"后面的检查一条都不跑）")

    # ── H. 测试桩自身的语义（桩错了 = 检查在验一个假世界）────────────
    #  ⚠️ 桩坏了往往**不报错**：它是"配合方"，出错时静默返回一个宽松的
    #    结果，于是依赖它的检查全绿但什么都没验证。这里真跑一遍。
    section("H. Playwright 桩的 cookie 语义")
    _stub = src_safe("regression/stubs/playwright/async_api.py")

    # H1：`urlsplit` 必须**模块级**可解析 —— 写成函数内 `except Exception`
    #     兜底时，忘导入会伪装成"过滤失败→返回全部"，比不过滤更糟。
    _imports = re.findall(r'^from urllib\.parse import .*\burlsplit\b',
                          _stub, re.M)
    r.ok("H1 桩里 urlsplit 是模块级导入",
         bool(_imports),
         "函数内才导入 / 忘了导入 → 过滤抛 NameError → 被兜底吞掉 → 返回全部")

    # H2：过滤路径不许有裸 `except Exception` 兜底
    #  ⚠️ 判据用 **AST**，不用文本匹配 —— 解释"为什么不能用 except Exception"
    #    的注释里就写着这几个字，文本判据会被自己的说明命中（自指，第三次了）。
    _bare = []
    _has_fn = False
    try:
        # ⚠️ 必须同时认 **AsyncFunctionDef** —— `async def cookies()` 不是
        #    FunctionDef，只认后者的话 _has_fn 永远是 False，
        #    检查变成"找不到函数"却报成别的原因（同 page 里踩过一次）。
        for _fn in _ast2.walk(_ast2.parse(_stub)):
            if isinstance(_fn, (_ast2.FunctionDef, _ast2.AsyncFunctionDef)) \
                    and _fn.name == "cookies":
                _has_fn = True
                for _n in _ast2.walk(_fn):
                    if isinstance(_n, _ast2.ExceptHandler) \
                            and isinstance(_n.type, _ast2.Name) \
                            and _n.type.id in ("Exception", "BaseException"):
                        _bare.append(_n.lineno)
    except SyntaxError:
        _has_fn = False
    r.ok("H2 cookies() 里没有裸 except Exception",
         _has_fn and not _bare,
         f"位置={_bare or '无'}（会把'函数名写错'伪装成'过滤不了→返回全部'）")

    # H3：真跑一遍往返 / 副本 / 同域子域过滤（含 IPv6 方括号）
    _res = _run_probe(_STUB_COOKIE_PROBE)
    for _k, _desc in [
        ("roundtrip", "写入的 cookie 能读回来"),
        ("copy", "返回的是副本（改它不影响内部）"),
        ("ipv6", "IPv6 方括号主机能匹配（`[::1]` ↔ `::1`）"),
        ("subdomain", "子域命中父域 cookie"),
        ("no_match", "不相关域名不返回"),
        ("no_url", "不传 url 时返回全部"),
    ]:
        r.ok(f"H3.{_k} {_desc}",
             _res.get(_k) is True,
             f"探测结果={_res.get(_k)!r} {_res.get('__error__', '')}".strip())

    # ── I. 面板令牌请求的竞态守卫（真跑 web/index.html 里的 loadToken）──
    #  ⚠️ 面板每 10 分钟轮询一次 `/token`，同时用户可以点「重新生成」
    #     —— 两个请求**并发**，而 fetch 不保证先发先回。没有请求序号时，
    #     轮询那条拿到的**旧令牌**如果晚到，会把刚生成的新令牌**覆盖掉**，
    #     用户看到的是"重新生成没生效"，甚至粘到一枚已作废的令牌。
    section("I. 面板令牌请求的竞态守卫")
    _tr = PLUGIN_DIR / "regression" / "js" / "token_race.mjs"
    _node = shutil.which("node")
    if not _tr.is_file():
        r.ok("I1 竞态探测脚本存在（token_race.mjs）", False,
             f"缺少 {_tr} —— 令牌竞态将没有行为验证")
    elif not _node:
        r.warn("没有 node，跳过 I1 令牌竞态验证", "安装 Node.js 后可启用")
    else:
        try:
            _cd = subprocess.run(
                [_node, str(_tr)], cwd=str(_tr.parent), capture_output=True,
                text=True, timeout=60,
                env={"PATH": os.environ.get("PATH", "") + ":/usr/bin:/bin",
                     "KIRA_PLUGIN_DIR": str(PLUGIN_DIR)})
            _items = json.loads((_cd.stdout or "[]").strip().splitlines()[-1])
            # 覆盖度：必须真的测了"迟到覆盖"这一条，否则脚本改瘦了也全绿
            _names = [str(x.get("name", "")) for x in _items]
            _need = ("迟到的旧响应不得覆盖新令牌", "迟到的失败响应不得覆盖新令牌")
            _miss = [n for n in _need if n not in _names]
            if _miss:
                r.ok("I1 竞态探测覆盖关键场景", False, f"缺={_miss}；实际={_names}")
            else:
                for _it in _items:
                    r.ok(f"I1 {_it['name']}", bool(_it.get("ok")),
                         _it.get("detail", ""))
        except Exception as e:
            r.ok("I1 面板令牌竞态验证", False, f"{type(e).__name__}: {e}"[:140])
