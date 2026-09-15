"""静态一致性 + README 一致 + 旧问题回归 + 运行时坑 + 打包完整性。

这一组是「不跑起来也能查」的部分：五方对齐（工具/配置/协议/后端/权限）、
文档与代码是否一致、历史问题有没有复发、以及常见的运行时坑。
"""

from __future__ import annotations

import json
import re

from ..harness import (
    EXT_DIR, PLUGIN_DIR, backend_methods, called_backend_methods,
    ext_file, ext_manifest, exists, headless_src, extension_src,
    bridge_src, main_src, manifest, schema, section, src, tool_names,
)

TITLE = "静态一致性审计"

#: 三个反向 CPU 参数 —— 任何情况都不该出现在启动参数里
FORBIDDEN_ARGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]


def run(r) -> None:
    main = main_src()
    hb = headless_src()
    eb = extension_src()
    br = bridge_src()
    sch = schema()
    man = manifest()
    exm = ext_manifest()
    proto_py = src("protocol.py")
    proto_js = ext_file("protocol.js")
    ext_names = tool_names(main)

    # ══════════════════════════════════════════════════════════════
    section("A. 五方对齐（工具 / 配置 / 协议 / 后端 / 权限）")
    # ══════════════════════════════════════════════════════════════

    # A1 每个 schema 配置项都必须被代码真的读进属性
    #    （历史坑：schema 有、代码不读 → 用户改了没反应 / AttributeError）
    read_keys = set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', main))
    read_keys |= set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', hb))
    never_read = sorted(set(sch) - read_keys)
    r.ok("A1 schema 每个配置项都被代码读取",
         not never_read, f"未被读取={never_read or '无'}（共 {len(sch)} 项）")

    # A2 传给无头后端的配置键必须都在 schema 里
    block = re.search(r'self\._headless_cfg = \{([^}]+)\}', main, re.S)
    inner = re.findall(r'"([a-z_]+)":', block.group(1)) if block else []
    missing = sorted(set(inner) - set(sch))
    r.ok("A2 传给无头后端的配置键都在 schema 里",
         not missing, f"缺={missing or '无'}")

    # A3/A4/A5 后端接口
    called = called_backend_methods(main)
    hb_m = backend_methods(hb)
    eb_m = backend_methods(eb)
    local = {"get_text"}
    r.ok("A3 无头后端实现全部被调用的方法",
         not (called - hb_m - local), f"缺={sorted(called - hb_m - local) or '无'}")
    r.ok("A4 扩展后端实现全部被调用的方法",
         not (called - eb_m - local), f"缺={sorted(called - eb_m - local) or '无'}")
    r.ok("A5 两个后端公开能力接口完全对称",
         not (hb_m - eb_m) and not (eb_m - hb_m),
         f"仅无头={sorted(hb_m - eb_m) or '无'}；"
         f"仅扩展={sorted(eb_m - hb_m) or '无'}")

    # A6/A7 协议命令两端一致
    py_cmds = set(re.findall(r'^CMD_[A-Z_]+ = "([a-z_]+)"', proto_py, re.M))
    cmd_section = (proto_js.split("export const CMD = {")[1].split("};")[0]
                   if "export const CMD = {" in proto_js else "")
    js_cmds = set(re.findall(r'^\s+[A-Z_]+: "([a-z_]+)",', cmd_section, re.M))
    r.ok("A6 协议命令 Python ⊆ JS", py_cmds <= js_cmds,
         f"Python 独有={sorted(py_cmds - js_cmds) or '无'}")
    r.ok("A7 协议命令 JS ⊆ Python", js_cmds <= py_cmds,
         f"JS 独有={sorted(js_cmds - py_cmds) or '无'}")

    # A8 background.js 分派覆盖全部命令
    bg = ext_file("background.js")
    dispatched = set(re.findall(r'case CMD\.([A-Z_]+):', bg))
    special = {"DOWNLOAD"} if "CMD.DOWNLOAD" in bg else set()
    all_consts = set(re.findall(r'^\s+([A-Z_]+): "[a-z_]+",', cmd_section, re.M))
    uncovered = sorted(all_consts - dispatched - special)
    r.ok("A8 background.js 分派覆盖全部协议命令",
         not uncovered, f"{len(all_consts)} 个命令，漏={uncovered or '无'}")

    # A9/A10 扩展权限
    need = {"tabs", "scripting", "storage", "alarms", "notifications",
            "webNavigation", "activeTab", "cookies", "userScripts"}
    have = set(exm["permissions"])
    r.ok("A9 扩展权限齐全（含新能力所需）", need <= have,
         f"缺={sorted(need - have) or '无'}")
    r.ok("A10 host_permissions 覆盖本地 + 全站",
         any("127.0.0.1" in h for h in exm["host_permissions"])
         and "<all_urls>" in exm["host_permissions"])

    # A11 工具名不重复
    names = re.findall(r'name="(browser_[a-z_]+)"', main)
    dupes = sorted({n for n in names if names.count(n) > 1})
    r.ok("A11 工具名无重复", not dupes, f"{len(names)} 个工具，重复={dupes or '无'}")

    # A12 只读模式摘除的写工具名都存在
    wt = re.search(r'WRITE_TOOL_NAMES = \(([^)]+)\)', main, re.S)
    wt_names = re.findall(r'"([a-z_]+)"', wt.group(1)) if wt else []
    r.ok("A12 WRITE_TOOL_NAMES 里的名字都是真实工具",
         all(f'"{n}"' in main for n in wt_names) and bool(wt_names),
         f"{len(wt_names)} 个")

    # A13 **JS 模块符号可解析**
    #     固化「ES 模块之间不共享作用域」这类事故：漏了就是运行时 ReferenceError
    js_files = ["background.js", "shared.js", "capabilities.js",
                "commands.js", "protocol.js", "content.js"]
    exports = {}
    for f in js_files:
        s = ext_file(f) if exists(f"browser-bridge/{f}") else ""
        ex = set(re.findall(r'export\s+(?:async\s+)?function\s+(\w+)', s))
        ex |= set(re.findall(r'export\s+const\s+(\w+)', s))
        for m in re.finditer(r'export\s*\{([^}]*)\}', s, re.S):
            ex |= {n.strip() for n in m.group(1).replace("\n", " ").split(",") if n.strip()}
        exports[f] = ex
    bad = []
    for f in js_files:
        if not exists(f"browser-bridge/{f}"):
            continue
        s = ext_file(f)
        for m in re.finditer(r'import\s*\{([^}]*)\}\s*from\s*"\./([\w.]+)"', s, re.S):
            names_ = [n.strip() for n in m.group(1).replace("\n", " ").split(",") if n.strip()]
            tgt = m.group(2)
            miss = [n for n in names_ if n not in exports.get(tgt, set())]
            if miss:
                bad.append(f"{f}←{tgt}:{miss}")
    r.ok("A13 JS 模块的 import 符号都在目标模块导出",
         not bad, f"缺={bad or '无'}")

    # ══════════════════════════════════════════════════════════════
    section("B. README 与代码一致性")
    # ══════════════════════════════════════════════════════════════
    if not exists("README.md"):
        r.warn("README.md 不存在")
    else:
        readme = src("README.md")
        mentioned = set(re.findall(r'browser_[a-z_]+', readme))
        nonexistent = sorted(mentioned - ext_names - set(sch) - {"browser_send_file"})
        r.ok("B1 README 提到的工具都存在（或已标注为废弃）",
             not nonexistent, f"不存在的={nonexistent or '无'}")

        cfg_section = (readme.split("## 配置说明")[1].split("\n## ")[0]
                       if "## 配置说明" in readme else "")
        table_keys = set()
        for line in cfg_section.splitlines():
            m = re.match(r'^\|\s*`([a-z_]{3,})`\s*\|', line)
            if m:
                table_keys.add(m.group(1))
        bad_cfg = sorted(k for k in table_keys if k not in sch)
        r.ok("B2 README 配置表里的项都存在",
             not bad_cfg, f"表里 {len(table_keys)} 项；不存在的={bad_cfg or '无'}")

        removed = ["use_real_browser_profile", "use_persistent_profile",
                   "auto_send_screenshot", "vlm_model", "upload_allow_any_path",
                   "notify_setup_via_chat"]
        stale = [x for x in removed if x in readme]
        r.ok("B3 README 不含已删除的配置项", not stale, f"残留={stale or '无'}")
        r.ok("B4 README 版本号与 manifest 一致",
             man["version"] in readme, f"manifest={man['version']}")

    # ══════════════════════════════════════════════════════════════
    section("C. 历史问题回归（防止修过的又回来）")
    # ══════════════════════════════════════════════════════════════
    args_blocks = re.findall(
        r'_(?:COMMON|HEADLESS_ONLY|HEADFUL_EXTRA)_ARGS = \[([^\]]+)\]', hb, re.S)
    in_args = [a for a in FORBIDDEN_ARGS for blk in args_blocks if f'"{a}"' in blk]
    r.ok("C1 三个反向节流参数不在任何 args 列表里", not in_args,
         f"残留={in_args or '无'}")
    r.ok("C2 有头模式含窗口可见性参数",
         all(k in hb for k in ("--start-maximized", "--window-size",
                               "--window-position")))
    r.ok("C3 默认等待是 domcontentloaded",
         sch["default_wait_until"]["default"] == "domcontentloaded"
         and '"domcontentloaded"' in hb)
    r.ok("C4 元素截图纳入清理",
         "element_*.png" in hb and "screenshot_*.png" in hb)
    r.ok("C5 弹窗回收已注册", 'self._context.on("page"' in hb)
    r.ok("C6 页面自愈存在",
         all(k in hb for k in ("_page_alive", "_ensure_page", "_recover")))
    r.ok("C7 空闲回收存在",
         "_idle_watchdog" in hb and sch["idle_close_seconds"]["default"] == 300)
    r.ok("C8 下载流式落盘（不占内存）",
         "iter_chunked" in hb and "await r.read()" not in hb
         and "await response.read()" not in hb)
    r.ok("C9 下载只允许 http/https",
         'startswith(("http://", "https://"))' in hb)
    r.ok("C10 桥接 _pending 用 finally 兜底（CancelledError 不再漏）",
         "finally:" in br and "CancelledError" in br)
    r.ok("C11 桥接下载分块走 sink（不整份攒内存）",
         "_sinks" in br and "_abort_sink" in br and "_chunks" not in br)
    r.ok("C12 扩展点击不再重复调 el.click()",
         "不能再调" in ext_file("content.js")
         or "只派发一次 click" in ext_file("content.js"))
    r.ok("C13 扩展桥不直接占用真实目录",
         "_real_user_data_dir" in hb and "_inherited_profile_dir" in hb
         and "use_real_browser_profile" not in hb)
    r.ok("C14 inherit 副本会清锁文件",
         all(k in hb for k in ("SingletonLock", "SingletonCookie")))
    # 只看真正的代码调用，注释里提到「不要用 FileReader」是正常的
    _cap = ext_file("capabilities.js")
    _cap_code = "\n".join(ln for ln in _cap.splitlines()
                          if not ln.strip().startswith(("//", "*", "/*")))
    r.ok("C15 MV3 里不用 DOM API（FileReader）",
         "FileReader" not in _cap_code,
         "Service Worker 里没有 FileReader，用了就是 ReferenceError")
    r.ok("C16 二次确认集中在 runCommand（高危命令不绕过）",
         "PRIVILEGED_COMMANDS" in bg and "PRIVILEGED_COMMANDS.has(name)" in bg)
    r.ok("C17 截图前校验标签是否在前台",
         "tab.active" in bg and "不能截" not in bg)

    # ══════════════════════════════════════════════════════════════
    section("D. 运行时坑")
    # ══════════════════════════════════════════════════════════════
    for name in ("main.py", "backends/headless_backend.py",
                 "backends/extension_backend.py", "bridge.py", "setup_guide.py"):
        body = src(name)
        if "logger." in body:
            r.ok(f"D1 {name} 的 logger 有定义", "logger = get_logger" in body)

    for name in ("main.py", "backends/headless_backend.py",
                 "backends/extension_backend.py"):
        body = src(name)
        if re.search(r'\basyncio\.', body):
            r.ok(f"D2 {name} 用到 asyncio 且已导入",
                 bool(re.search(r'^import asyncio', body, re.M)))

    import py_compile
    targets = ["main.py", "protocol.py", "bridge.py", "security.py",
               "tokens.py", "setup_guide.py", "backends/__init__.py",
               "backends/base.py", "backends/router.py",
               "backends/headless_backend.py", "backends/extension_backend.py"]
    bad_compile = []
    for f in targets:
        if not exists(f):
            continue
        try:
            py_compile.compile(str(PLUGIN_DIR / f), doraise=True)
        except Exception as e:
            bad_compile.append(f"{f}: {e}")
    r.ok("D3 所有 Python 文件可编译", not bad_compile,
         f"失败={bad_compile or '无'}")

    r.ok("D4 plugin_id 与上游一致（避免升级后出现两个插件）",
         man["plugin_id"] == "headless_browser",
         f"plugin_id={man['plugin_id']}")
    r.ok("D5 manifest 作者含 nyx / znq19 / 萧洋",
         all(x in man["author"] for x in ("nyx", "znq19", "萧洋")),
         f"author={man['author']}")
    r.ok("D6 manifest 引用的图标存在", exists(man["icon"]))
    r.ok("D7 扩展图标都在",
         all(exists(f"browser-bridge/{v}") for v in exm["icons"].values()))

    rel = set(re.findall(r'^from \.(\w+) import', main, re.M))
    rel |= set(re.findall(r'^from \.(\w+) import', hb, re.M))

    def mod_exists(name):
        for base in (PLUGIN_DIR, PLUGIN_DIR / "backends"):
            if (base / f"{name}.py").exists() or (base / name / "__init__.py").exists():
                return True
        return False

    miss_mod = [m for m in rel if not mod_exists(m)]
    r.ok("D8 相对导入的模块都存在", not miss_mod, f"缺={miss_mod or '无'}")

    declared = set()
    for v in exm.get("content_scripts", []):
        declared |= set(v.get("js", []))
    imported = set(re.findall(r'from "\./([\w.]+)"', bg))
    popup_html = ext_file("popup.html")
    popup_js = set(re.findall(r'src="([\w.]+)"', popup_html))
    reachable = declared | imported | popup_js | {exm["background"]["service_worker"]}
    present = {f.name for f in EXT_DIR.iterdir() if f.suffix == ".js"}
    orphan = sorted(present - reachable - {"protocol.js"})
    r.ok("D9 扩展里没有游离的 JS 文件", not orphan, f"未引用={orphan or '无'}")

    bg_actions = set(re.findall(r'callContent\(tab, "([a-z_]+)"', bg))
    content = ext_file("content.js")
    handlers = set(re.findall(r'^\s+(?:async\s+)?([a-z_]+)\(payload\) \{', content, re.M))
    miss_h = sorted(bg_actions - handlers - {"ping", "get_selection"})
    r.ok("D10 content.js 实现 background 调用的所有 action",
         not miss_h, f"缺={miss_h or '无'}")

    # ══════════════════════════════════════════════════════════════
    section("E. 打包完整性")
    # ══════════════════════════════════════════════════════════════
    required = [
        "main.py", "manifest.json", "schema.json", "__init__.py", "README.md",
        "requirements.txt", "icon.png", ".gitignore",
        "protocol.py", "bridge.py", "security.py", "tokens.py", "setup_guide.py",
        "backends/__init__.py", "backends/base.py", "backends/router.py",
        "backends/headless_backend.py", "backends/extension_backend.py",
        "browser-bridge/manifest.json", "browser-bridge/background.js",
        "browser-bridge/shared.js", "browser-bridge/capabilities.js",
        "browser-bridge/commands.js", "browser-bridge/content.js",
        "browser-bridge/protocol.js", "browser-bridge/popup.html",
        "browser-bridge/popup.js", "browser-bridge/icons/icon16.png",
        "browser-bridge/icons/icon48.png", "browser-bridge/icons/icon128.png",
        "web/index.html",
    ]
    miss_f = [f for f in required if not exists(f)]
    r.ok("E1 必需文件一个不少", not miss_f, f"缺={miss_f or '无'}")
    r.ok("E2 图标是有效 PNG",
         (PLUGIN_DIR / "icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")

    kira_manifests = []
    for f in PLUGIN_DIR.rglob("manifest.json"):
        if "__pycache__" in str(f) or "regression" in str(f):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "plugin_id" in data:
            kira_manifests.append((str(f.relative_to(PLUGIN_DIR)), data["plugin_id"]))
    r.ok("E3 整个包里只有一个 KiraAI 插件（不是两个）",
         len(kira_manifests) == 1, f"KiraAI 插件清单={kira_manifests}")
    r.ok("E4 browser-bridge 是浏览器扩展而非插件",
         exm.get("manifest_version") == 3 and "plugin_id" not in exm,
         f"manifest_version={exm.get('manifest_version')}")
