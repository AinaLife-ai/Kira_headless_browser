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

import json
import re

from ..harness import PLUGIN_DIR, section, src_safe

TITLE = "KiraAI 规范符合性（交付形态）"

#: manifest 必需字段（对照官方内置插件的 manifest）
REQUIRED_MANIFEST = ("plugin_id", "display_name", "version", "author", "description")

#: 本插件**必须**沿用的 plugin_id —— 改了就等于变成另一个插件，
#: 用户的既有配置/数据目录会全部对不上。
EXPECTED_PLUGIN_ID = "headless_browser"


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
