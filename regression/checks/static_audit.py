"""静态一致性 + README 一致 + 旧问题回归 + 运行时坑 + 打包完整性。

这一组是「不跑起来也能查」的部分：五方对齐（工具/配置/协议/后端/权限）、
文档与代码是否一致、历史问题有没有复发、以及常见的运行时坑。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..harness import (
    EXT_DIR, PLUGIN_DIR, backend_methods, called_backend_methods,
    ext_file, ext_manifest, exists, headless_src, extension_src,
    bridge_src, main_src, manifest, schema, section, src, src_safe,
    tool_names,
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
    # ⚠️ 用 .get() 取值：`exm["permissions"]` 直接索引时，
    #    键缺失/被改名会抛 KeyError → **整组检查中断**（B~E 段全不执行），
    #    报告上只剩一条笼统失败，看不出真正缺的是哪个键。
    #    改成安全取值后，只有**这一条**断言失败，信息也明确。
    _perm = exm.get("permissions") or []
    have = set(_perm)
    r.ok("A9 扩展权限齐全（含新能力所需）", need <= have,
         f"缺={sorted(need - have) or '无'}；permissions 键={'有' if _perm else '缺失/为空'}")
    _hosts = exm.get("host_permissions") or []
    r.ok("A10 host_permissions 覆盖本地 + 全站",
         any("127.0.0.1" in h for h in _hosts)
         and "<all_urls>" in _hosts,
         f"host_permissions={'有' if _hosts else '缺失/为空'}")

    # A11 工具名不重复
    names = re.findall(r'name="(browser_[a-z_]+)"', main)
    dupes = sorted({n for n in names if names.count(n) > 1})
    r.ok("A11 工具名无重复", not dupes, f"{len(names)} 个工具，重复={dupes or '无'}")

    # A12 只读模式摘除的写工具名 —— 必须**双向**都对得上
    #
    # ⚠️ 这条以前只验"名单里的名字都真实存在"，那是**单向**的：
    #    名单里写着三个早已被合并掉的工具名（browser_click/type/scroll），
    #    而真正的写工具（browser_interact 等）**一个都没在名单里** ——
    #    于是只读模式下这些工具根本没被摘掉，用户开了只读照样能点击/输入/执行 JS。
    #    单向检查对此完全无感（它只确认旧名字"存在"，而它们压根不存在……）。
    #
    #    现在两个方向都验：
    #      ① 名单里的每个名字都必须是真实注册的工具（防拼错/防残留）；
    #      ② **会改状态的**工具必须**全部**在名单里（防漏）。
    wt = re.search(r'WRITE_TOOL_NAMES = \(([^)]+)\)', main, re.S)
    wt_names = set(re.findall(r'"([a-z_]+)"', wt.group(1))) if wt else set()
    registered = set(names)

    _ghost = sorted(wt_names - registered)
    r.ok("A12a WRITE_TOOL_NAMES 里的名字都是真实注册的工具",
         not _ghost and bool(wt_names),
         f"名单里不存在={_ghost or '无'}（共 {len(wt_names)} 个）")

    # 会改状态的工具必须都在名单里。
    # 这份"必须被摘掉"的清单是**独立维护的期望值** —— 不是从代码里读出来的，
    # 所以它能在"有人加了新的写工具却忘了加进名单"时报红。
    MUST_BE_WRITABLE = {
        "browser_interact",   # 点击/输入/滚动/键盘/鼠标
        "browser_navigate",   # 跳转
        "browser_script",     # 执行任意 JS
        "browser_cookie",     # 写 cookie
        "browser_file",       # 上传/下载
    }
    _missing = sorted((MUST_BE_WRITABLE & registered) - wt_names)
    r.ok("A12b 所有会改状态的工具都在 WRITE_TOOL_NAMES 里（只读模式才真的拦得住）",
         not _missing,
         f"漏={_missing or '无'}；只读模式下这些工具不会被摘掉")

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

    # A13b **用了但没 import** 的符号
    #      A13 只验了"import 的符号在目标模块里存在"这一个方向，
    #      反过来（代码里用了 shared.js 的函数却没 import）完全没查 ——
    #      而这正是 capabilities.js 出事的地方：运行时 ReferenceError，
    #      执行JS / 上传 / Cookie 全部不可用。
    SHARED_SYMBOLS = set()
    for f_ in ("shared.js", "protocol.js"):
        if not exists(f"browser-bridge/{f_}"):
            continue
        body = ext_file(f_)
        SHARED_SYMBOLS |= set(re.findall(r'export\s+const\s+(\w+)', body))
        SHARED_SYMBOLS |= set(re.findall(r'export\s+(?:async\s+)?function\s+(\w+)', body))
        for m_ in re.finditer(r'export\s*\{([^}]*)\}', body, re.S):
            SHARED_SYMBOLS |= {x.strip() for x in
                               m_.group(1).replace("\n", " ").split(",") if x.strip()}
    unresolved = []
    for f_ in js_files:
        if not exists(f"browser-bridge/{f_}") or f_ in ("shared.js", "protocol.js"):
            continue
        body = ext_file(f_)
        code = "\n".join(ln for ln in body.splitlines()
                         if not ln.strip().startswith(("//", "*", "/*")))
        have = set()
        for m_ in re.finditer(r'import\s*\{([^}]*)\}\s*from', code, re.S):
            have |= {x.strip().split(" as ")[-1].strip()
                     for x in m_.group(1).replace("\n", " ").split(",") if x.strip()}
        for sym in sorted(SHARED_SYMBOLS):
            if sym in have:
                continue
            # ⚠️ 不能只看 `sym(` —— 那样会漏掉：
            #      * 成员访问（`MSG.PING` / `CMD.DOWNLOAD`）
            #      * 当值传递（回调、数组元素）
            #    这些同样是"未声明的标识符"，运行时照样 ReferenceError，
            #    而 node --check 不会报（它不查未定义标识符）。
            if re.search(rf'(?<![\w.$]){re.escape(sym)}\s*[.(]', code) \
                    or re.search(rf'(?<![\w.$]){re.escape(sym)}\b(?!\s*:)', code):
                # 排除它自己被声明的情况（函数/变量声明、解构赋值）
                if re.search(rf'(?:function|const|let|var)\s+{re.escape(sym)}\b', code):
                    continue
                unresolved.append(f"{f_} 用了 {sym} 但没 import")
    r.ok("A13b 从 shared/protocol 用到的符号都 import 了",
         not unresolved, f"未 import={unresolved or '无'}")

    # A13c **ES 模块语法**（用 module 模式解析）
    #      ⚠️ `node --check xxx.js` 会把 .js 当 **CommonJS** 解析，
    #      "import 与本地声明重名"这类模块级语法错误**根本查不出来**。
    #      必须复制成 .mjs 或用 --input-type=module 才是真检查。
    #      真实事故：capabilities.js 既 import 了 sendChunk 又本地声明了一遍
    #      → 整个扩展模块**解析失败**，扩展完全加载不了。
    import shutil as _sh, subprocess as _sp, tempfile as _tf
    if _sh.which("node"):
        js_bad = []
        for f_ in js_files:
            p_ = PLUGIN_DIR / "browser-bridge" / f_
            if not p_.is_file():
                continue
            # ⚠️ 名字里必须带**每次唯一的**成分：写死的话两个并发跑的
            #    回归进程会互相覆盖、甚至在 finally 里删掉对方的文件。
            _uniq = f"{os.getpid()}_{next(_tf._get_candidate_names())}"
            tmp = (Path(_tf.gettempdir())
                   / f"_kira_chk_{_uniq}_{f_.replace('.', '_')}.mjs")
            try:
                tmp.write_text(p_.read_text(encoding="utf-8"), encoding="utf-8")
                r_ = _sp.run(["node", "--check", str(tmp)],
                             capture_output=True, text=True, timeout=30)
                if r_.returncode != 0:
                    first = (r_.stderr or "").strip().splitlines()
                    msg = next((x for x in first if "Error" in x), first[0] if first else "?")
                    js_bad.append(f"{f_}: {msg[:80]}")
            except Exception as e:
                js_bad.append(f"{f_}: 检查失败 {e}")
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        r.ok("A13c 扩展 JS 在 module 模式下语法正确", not js_bad,
             f"失败={js_bad or '无'}")
    else:
        r.warn("没有 node，跳过 ES 模块语法检查")

    # A14 硬编码的插件 id 必须与 manifest 一致
    #     （面板 API 路径 / WS 路径 / 扩展路径都依赖它，
    #      对不上就是 404 或连不上，而且"看起来都写对了"）
    pid = man.get("plugin_id") or ""
    hard = []
    if not pid:
        # manifest 缺 plugin_id → 只报这一条，不往下用空串去比对
        # （空串会让"该行必须含 plugin_id"永远不成立 → 满屏误报）
        r.ok("A14 硬编码的插件 id 与 manifest 一致", False,
             "manifest 缺 plugin_id，无法比对")
        pid = "headless_browser"
    for rel in ("web/index.html", "browser-bridge/protocol.js"):
        if not exists(rel):
            continue
        body = src(rel)
        for i, ln in enumerate(body.splitlines(), 1):
            if "/api/plugin/" in ln or "/ws/plugin/" in ln:
                # 该行必须含正确的 plugin_id，或者是动态拼接
                if f"/api/plugin/{pid}" in ln or f"/ws/plugin/{pid}" in ln:
                    continue
                if "plugin_id" in ln or "${" in ln or "ws_path" in ln:
                    continue          # 动态拼接，OK
                hard.append(f"{rel}:{i} {ln.strip()[:70]}")
    r.ok("A14 硬编码的插件 id 与 manifest 一致", not hard,
         f"不一致={hard or '无'}（plugin_id={pid}）")

    # ══════════════════════════════════════════════════════════════
    section("B. README 与代码一致性")
    # ══════════════════════════════════════════════════════════════
    if not exists("README.md"):
        r.warn("README.md 不存在")
    else:
        readme = src("README.md")
        # 只认**工具名**：browser_ 前缀且后面是工具命名风格。
        # 别把文件名（browser_bridge.py）或配置项（browser_channel）算进来 ——
        # 这里额外排除已知不是工具的标识。
        mentioned = set(re.findall(r'\bbrowser_[a-z_]+\b', readme))
        mentioned -= {"browser_bridge", "browser_channel"}
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

        # 只列**确实已从 schema 移除**的旧配置。
        # （upload_allow_any_path 在 v2.1.5 恢复了，不再算"已删除"）
        # ⚠️ `vlm_model` / `auto_describe_screenshot` 已从这份"已删除"名单里
        #    拿掉：它们在 v2.1.27 **恢复了**（v2.1.0 重写双后端架构时被整段
        #    丢掉，那是个失误 —— bot 因此看不到页面）。
        #    `auto_send_screenshot` 仍算已删除：当前用截图工具的 `send`
        #    参数表达同一件事，不再单列配置项。
        removed = ["use_real_browser_profile", "use_persistent_profile",
                   "auto_send_screenshot", "notify_setup_via_chat"]
        stale = [x for x in removed if x in readme]
        r.ok("B3 README 不含已删除的配置项", not stale, f"残留={stale or '无'}")
        _ver = man.get("version") or ""
        r.ok("B4 README 版本号与 manifest 一致",
             bool(_ver) and _ver in readme,
             f"manifest={_ver or '（缺失）'}")

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
    # 用 .get 而不是直接下标 —— schema 少一个键时不该让整组检查中断
    r.ok("C3 默认等待是 domcontentloaded",
         (sch.get("default_wait_until") or {}).get("default") == "domcontentloaded"
         and '"domcontentloaded"' in hb)
    r.ok("C4 元素截图纳入清理",
         "element_*.png" in hb and "screenshot_*.png" in hb)
    r.ok("C5 弹窗回收已注册", 'self._context.on("page"' in hb)
    r.ok("C6 页面自愈存在",
         all(k in hb for k in ("_page_alive", "_ensure_page", "_recover")))
    r.ok("C7 空闲回收存在",
         "_idle_watchdog" in hb
         and (sch.get("idle_close_seconds") or {}).get("default") == 300)
    r.ok("C8 下载流式落盘（不占内存）",
         "iter_chunked" in hb and "await r.read()" not in hb
         and "await response.read()" not in hb)
    r.ok("C9 下载只允许 http/https",
         'startswith(("http://", "https://"))' in hb)
    r.ok("C10 桥接 _pending 用 finally 兜底（CancelledError 不再漏）",
         "finally:" in br and "CancelledError" in br)
    r.ok("C11 桥接下载分块走 sink（不整份攒内存）",
         "_sinks" in br and "_abort_sink" in br and "_chunks" not in br)
    # ⚠️ 不能拿**注释文字**当判据 —— 注释还在、有人把 el.click() 加回来时，
    #    这条检查照样会过。必须看**真正的代码**。
    _cjs = ext_file("content.js")
    _cjs_code = "\n".join(ln for ln in _cjs.splitlines()
                          if not ln.strip().startswith(("//", "*", "/*")))
    # 找 click 处理器里有没有第二次触发（target.click() / el.click() 之类）
    _dup_click = []
    for _m in re.finditer(r'(\w+)\.click\(\)', _cjs_code):
        # 排除我们自己派发的 dispatchEvent(new MouseEvent("click"...))
        _line = _cjs_code[:_m.start()].rsplit("\n", 1)[-1]
        if "dispatchEvent" in _line or _m.group(1) in ("window",):
            continue
        _dup_click.append(_m.group(0))
    r.ok("C12 扩展点击只派发一次（没有第二次原生 click()）",
         not _dup_click,
         f"发现第二次点击调用={_dup_click or '无'}")
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
    # ⚠️ 收到 PING 后**只有 PONG 真的发出去了**才算链路通。
    #    收到 ping 只说明"能收"；sendRaw 返回 false 说明 socket 只能收不能发，
    #    链路其实不通。原来无论发没发成都调 state.probe() →
    #    弹窗显示"链路正常"，实际命令发不出去（假成功）。
    # 判定逻辑抽成函数，并配一个**变异夹具**：把"有守卫"的样本判为过、
    # 把"没守卫"的样本判为不过 —— 否则断言本身弱了也没人知道。
    def _ping_guarded(seg):
        """seg 里 state.probe() 是否**真的**被 sendRaw 的结果把关？

        ⚠️ 不能只检查"存在某个 = sendRaw(...) 的赋值，且出现了 state.probe" ——
            那两种情况它都会误判为通过：
              ① 赋值了但没用它做条件（`const s = sendRaw(...); if (true) probe()`）
              ② 把 probe 放在完全无关的分支里
            要求：赋值出的**那个标识符**必须出现在 state.probe() 之前的条件里。
        """
        m = re.search(
            r'(?:const|let|var)\s+(\w+)\s*=\s*sendRaw\s*\(', seg)
        if not m:
            return False
        ident = m.group(1)
        # ⚠️ 先剥掉注释：注释里也会出现 "state.probe()"（说明为什么这么写），
        #    直接 find("state.probe") 会切在注释里，把真正的守卫代码切没。
        code = re.sub(r'/\*[\s\S]*?\*/', '', seg)
        code = re.sub(r'(?m)//[^\n]*$', '', code)
        if "state.probe(" not in code:
            return False
        head = code[:code.find("state.probe(")]
        # 该标识符必须出现在 probe 之前的**条件**里
        return (re.search(rf'if\s*\([^)]*\b{re.escape(ident)}\b', head)
                is not None
                or re.search(rf'\b{re.escape(ident)}\b\s*&&', head) is not None
                or re.search(rf'\b{re.escape(ident)}\b\s*\?', head) is not None)

    _ping = re.search(r'case MSG\.PING:([\s\S]{0,600}?)break;', bg)
    _ping_seg = _ping.group(1) if _ping else ""

    # ── 变异夹具：证明这条断言**真的能红** ──────────────────────────
    _fx_good = ('const pongSent = sendRaw({ type: MSG.PONG });\n'
                'if (pongSent && typeof state.probe === "function") '
                '{ state.probe(); }')
    _fx_bad_assign_only = ('const pongSent = sendRaw({ type: MSG.PONG });\n'
                           'if (typeof state.probe === "function") '
                           '{ state.probe(); }')
    _fx_bad_none = 'state.probe();'
    r.ok("C16k 夹具：有守卫的写法必须判为通过", _ping_guarded(_fx_good) is True)
    r.ok("C16k 夹具：赋值了但没拿它做守卫 → 必须判为不通过",
         _ping_guarded(_fx_bad_assign_only) is False)
    r.ok("C16k 夹具：完全没有守卫 → 必须判为不通过",
         _ping_guarded(_fx_bad_none) is False)

    r.ok("C16k 仅有 PONG 发送成功时才报告链路正常",
         _ping_guarded(_ping_seg),
         "PING 分支必须用 sendRaw 的返回值**作为条件**来把关 state.probe()")

    # ⚠️ popup 里**每一个** chrome.runtime.sendMessage 都必须被 try/catch 兜住。
    #    MV3 的 service worker 被回收/未唤醒时它会 reject；不接住的话
    #    弹窗会卡在"连接中…/测试中…"不回来，而且每 3 秒的轮询会不断产生
    #    unhandled rejection。
    _pop = ext_file("popup.js")
    _lines = _pop.splitlines()
    _unguarded = []
    for _i, _ln in enumerate(_lines):
        if "chrome.runtime.sendMessage" not in _ln:
            continue
        # 往上找 6 行内有没有 try（调用点通常紧跟 try {）
        _win = "\n".join(_lines[max(0, _i - 6):_i + 1])
        if "try {" not in _win and "try{" not in _win:
            _unguarded.append(_i + 1)
    # ⚠️ 光有 try/catch 还不够：`sendMessage` 在 background 没返回内容时
    #    会 resolve 成 **undefined**（不 reject），此时读 `r.ok` 会抛
    #    TypeError，界面就停在"连接中…/正在测试…"不回来了。
    #    所以每个读取 sendMessage 结果的地方都必须先判空。
    # ⚠️ 先剥注释再扫：注释里也会提到 `r.ok`（说明为什么要判空），
    #    不剥掉的话会把"自己的说明文字"当成未保护的代码（假红）。
    _pop_code = re.sub(r'/\*[\s\S]*?\*/', '', _pop)
    _pop_code = re.sub(r'(?m)//[^\n]*$', '', _pop_code)
    _pop_lines = _pop_code.splitlines()
    _no_guard = []
    for _i, _ln in enumerate(_pop_lines):
        if not re.search(r'\br\.ok\b', _ln):
            continue
        # 往上找 25 行内有没有 `if (!r)` 之类的判空
        # （窗口要够大：sendMessage 的 try/catch 块本身就占好几行，
        #  窗口太小会把"其实判了空"的地方误报成没判）
        _win = "\n".join(_pop_lines[max(0, _i - 25):_i + 1])
        if not re.search(r'if\s*\(\s*!\s*r\s*\)', _win):
            _no_guard.append(_i + 1)
    r.ok("C16m 读取 sendMessage 结果前先判空（否则 undefined 会抛错卡住界面）",
         not _no_guard, f"未判空的行={_no_guard or '无'}")

    r.ok("C16j popup 里每个 sendMessage 都被 try/catch 兜住",
         not _unguarded,
         f"未兜住的行={_unguarded or '无'}（SW 回收时会卡住弹窗）")

    # ⚠️ 禁止"同一个校验被连续重复调用"的空转。
    #    真实发生过：cookie_get 里 `_check_tab_id(tab_id)` 连着写了三遍
    #    （删过一次，后来重写那段时又带回来了）。它不影响功能，
    #    但会让人以为"这里有额外的校验逻辑"，也白跑两遍。
    _hb_code2 = re.sub(r'#.*$', '', src("backends/headless_backend.py"), flags=re.M)
    # ⚠️ 按**函数体**统计，而不是匹配固定的空白形态 ——
    #    正则匹配缩进/换行很脆（我刚写的版本就没能在反向验证里报红）。
    #    判据：同一个方法里，同一句校验最多出现一次。
    _dup_fns = []
    _cur_fn, _cur_count = None, 0
    for _ln in _hb_code2.splitlines():
        _m = re.match(r'\s*(?:async\s+)?def\s+(\w+)', _ln)
        if _m:
            if _cur_fn and _cur_count > 1:
                _dup_fns.append(f"{_cur_fn}×{_cur_count}")
            _cur_fn, _cur_count = _m.group(1), 0
        if "_check_tab_id(tab_id)" in _ln:
            _cur_count += 1
    if _cur_fn and _cur_count > 1:
        _dup_fns.append(f"{_cur_fn}×{_cur_count}")
    r.ok("C16n 没有连续重复的同一校验调用（空转）",
         not _dup_fns,
         f"同一方法里重复={_dup_fns or '无'}（不影响功能，但白跑且误导读者）")

    # ⚠️ 禁止"no-op 循环"：传空 dict 给 update_cookies 再把异常吞掉 ——
    #    看起来在做 cookie 导入，其实什么都没做。
    #    这种代码会**一直躺在里面**：它不报错、不影响功能，只是白跑一轮，
    #    但会让人以为"这里处理了 cookie"（我删过一次，改别处时又带回来了）。
    _hb_cookie = src("backends/headless_backend.py")
    # ⚠️ 先剥注释：我在这段旁边加了说明，注释里也写着 update_cookies({}, ...)，
    #    不剥掉的话**自己的注释会让检查永远为红**（假红）。
    _hb_code = re.sub(r'#.*$', '', _hb_cookie, flags=re.M)
    _noop = re.findall(r'update_cookies\(\s*\{\s*\}\s*,', _hb_code)
    r.ok("C16l 没有 no-op 的 update_cookies({}, ...) 空转",
         not _noop,
         f"出现 {len(_noop)} 处：传空 dict 什么也不会写入，只会误导读者")

    # ⚠️ 单条 WebSocket 帧有**硬上限**：KiraAI 用 uvicorn，其 ws_max_size
    #    默认 16 MiB，且框架没有覆盖它。实测：整条帧超过 16 MiB 时对端回
    #    1009 并**关闭整个连接** —— 一次超大上传会把连接打断，
    #    连带后面所有命令一起崩，而不只是这一条失败。
    #    任何"把整份文件塞进一条消息"的做法都会踩这个坑。
    _eb_code = src("backends/extension_backend.py")
    r.ok("C16f 上传不把整份文件塞进单条 WS 消息（16MiB 帧上限）",
         "CMD_UPLOAD_CHUNK" in _eb_code
         and "chunks\": chunks" not in _eb_code
         and "\"chunks\": chunks" not in _eb_code,
         "必须走分块流式；一次性下发 chunks 会在文件 >~12MiB 时断开连接")

    # ⚠️ 架构不变量：**文件内容只能在页面侧累积，SW 只转发**。
    #    原因：MV3 的 Service Worker 常驻内存紧、最容易被系统回收，
    #    回收会让正在进行的上传直接断掉；页面上下文宽松得多。
    #    实测：200MB 若在 SW 里攒 base64 峰值 ≈267MB；改成页面侧累积后
    #    SW 峰值 ≈0.3MB（恒定），页面侧峰值 ≈1.0× 文件大小。
    _cap3 = ext_file("capabilities.js")
    _content3 = ext_file("content.js")
    _cap_up = _cap3.split("async function upload(")[-1].split("async function uploadAbort(")
    _cap_up = _cap_up[0] if len(_cap_up) > 1 else _cap3
    _accum = ("parts.push" in _cap_up or ".push(data)" in _cap_up
              or "chunks.push" in _cap_up or "join(" in _cap_up)
    r.ok("C16h SW 侧上传只转发、不累积文件内容（内存不随文件增长）",
         not _accum,
         "capabilities.js 的上传路径里出现累积/拼接 —— SW 内存会随文件线性增长，"
         "且有被 MV3 回收的风险")
    r.ok("C16i 分块累积实现在页面侧（content.js）",
         "_upSessions" in _content3 and "upload_chunk" in _content3,
         "content.js 必须自己累积分块并实现 upload_chunk/finish")

    # 扩展侧要有对应的分块接收器。
    # ⚠️ 必须查**函数定义**，不能只查名字是否出现 ——
    #    名字在 export 名单里也会出现，函数体被删了照样"存在"。
    _cap2 = ext_file("capabilities.js")
    _missing_fn = [k for k in ("uploadChunk", "uploadFinish", "uploadAbort")
                   if f"async function {k}(" not in _cap2]
    r.ok("C16g 扩展侧真的实现了分块接收/收尾/中止",
         not _missing_fn,
         f"缺函数定义={_missing_fn or '无'}")

    # ⚠️ 判定必须用「写操作 ∪ 只读敏感」的**并集**，而且 cookie_get
    #    必须在确认集合里 —— 否则导出登录态会被静默放行。
    shared = src("browser-bridge/shared.js")
    r.ok("C16 二次确认集中在 runCommand（高危命令不绕过）",
         "NEEDS_CONFIRM_COMMANDS" in bg
         and "NEEDS_CONFIRM_COMMANDS.has(name)" in bg
         and "PRIVILEGED_COMMANDS" in shared
         and "CONFIRM_ONLY_COMMANDS" in shared
         and re.search(r'CONFIRM_ONLY_COMMANDS\s*=\s*new Set\(\s*\[\s*"cookie_get"',
                       shared) is not None,
         "确认集需为并集，且 cookie_get 必须在只读敏感集里")
    # ⚠️ evaluate 必须把注入包装器吞掉的 `__error` **翻成异常**，
    #    否则脚本抛错会被当成"命令成功"上报给插件。
    r.ok("C16b evaluate 把 __error 转成抛出（脚本异常不算成功）",
         "__error" in _cap_code
         and re.search(r'"__error"\s+in\s+\w+', _cap_code) is not None
         and "throw" in _cap_code,
         "必须检测结果里的 __error 并 throw")

    # ⚠️ 面板渲染事件数据/域名时必须用 textContent，不能拼 innerHTML
    #    （这些字段来自持有 bridge token 的一方，是注入口 CWE-79）。
    web = src_safe("web/index.html")
    _sinks = re.findall(r'\$\(["\'](?:conflog|domains)["\']\)\.innerHTML\s*=\s*([^;]+)',
                        web)
    r.ok("C16c 面板不用 innerHTML 拼事件数据（XSS）",
         all(("" == x.strip()) or x.strip().startswith('"') and len(x.strip()) <= 4
             for x in _sinks),
         f"conflog/domains 的 innerHTML 赋值必须清空或纯静态：{_sinks}")
    r.ok("C16d 面板用 textContent 渲染不受控字段",
         web.count("textContent") >= 6 and "_renderDomains" in web,
         "事件记录与域名列表都要走 DOM 节点")

    # ⚠️ 面板脚本里"用了但从没声明"的局部变量。
    #    真实事故：上一轮删掉 `const ad = ...`（改走 _renderDomains）时
    #    漏了后面 `!ad.length` 仍在用它 → 可写模式下每次 refresh() 都抛
    #    ReferenceError，而且**只读模式因短路求值不触发**，非常难发现。
    #    这里做一个轻量"未声明标识符"扫描，专抓这类漏改。
    def _keep_interp_c16e(m):
        inner = re.findall(r'\$\{([^{}]*)\}', m.group(0))
        return " " + " ".join(inner) + " "

    def _scan_undeclared(code_in):
        """扫出"用了但从没声明"的标识符（同 G1 的判据）。"""
        _c = re.sub(r'/\*[\s\S]*?\*/', '', code_in)
        _c = re.sub(r'(?m)//[^\n]*$', '', _c)
        _c = re.sub(r'`(?:[^`\\]|\\.)*`', _keep_interp_c16e, _c)
        _c = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', _c)
        _c = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", _c)
        _decl = set()
        for _m in re.finditer(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)', _c):
            _decl.add(_m.group(1))
        for _m in re.finditer(r'\bfunction\s+([A-Za-z_$][\w$]*)', _c):
            _decl.add(_m.group(1))
        for _m in re.finditer(r'\bfunction\s*[\w$]*\s*\(([^)]*)\)', _c):
            for _a in _m.group(1).split(","):
                _a = _a.strip().split("=")[0].strip()
                if _a:
                    _decl.add(_a)
        for _m in re.finditer(r'\bcatch\s*\(\s*([A-Za-z_$][\w$]*)', _c):
            _decl.add(_m.group(1))
        for _m in re.finditer(r'\(([^)]*)\)\s*=>', _c):
            for _a in _m.group(1).split(","):
                _a = _a.strip().split("=")[0].strip()
                if _a and _a.isidentifier():
                    _decl.add(_a)
        for _m in re.finditer(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*=>', _c):
            _decl.add(_m.group(1))

        _bi = {
            "document", "window", "chrome", "console", "Math", "JSON", "Date",
            "Array", "Object", "String", "Number", "Boolean", "Promise",
            "Error", "fetch", "setTimeout", "setInterval", "clearTimeout",
            "clearInterval", "encodeURIComponent", "decodeURIComponent",
            "parseInt", "parseFloat", "isNaN", "undefined", "null", "true",
            "false", "this", "new", "typeof", "return", "if", "else", "for",
            "while", "do", "function", "const", "let", "var", "async", "await",
            "try", "catch", "finally", "class", "throw", "switch", "case",
            "break", "continue", "delete", "in", "of", "instanceof", "void",
            "yield", "static", "get", "set", "navigator", "location", "alert",
            "confirm", "prompt", "Event", "CustomEvent", "Map", "Set",
            "Symbol", "RegExp", "Infinity", "NaN", "arguments", "globalThis",
            "requestAnimationFrame", "btoa", "atob", "Blob", "File",
            "Uint8Array", "DataTransfer",
        }
        _used = {}
        for _m in re.finditer(
                r'(?<![\w.$])([a-z_$][\w$]*)(?=\s*(?:\.|\[))', _c):
            _n = _m.group(1)
            if _n not in _decl and _n not in _bi:
                _used.setdefault(_n, _c[:_m.start()].count("\n") + 1)
        for _m in re.finditer(
                r'(?<![\w.$])([a-z_$][\w$]*)(?=\s*[)\]}])', _c):
            _n = _m.group(1)
            if _n in _decl or _n in _bi or _n in _used:
                continue
            if _n in ("return", "typeof", "await", "new", "in", "of", "if",
                      "for", "while", "catch", "switch"):
                continue
            _used.setdefault(_n, _c[:_m.start()].count("\n") + 1)
        return _used

    # ── C16e 的自检夹具 ──────────────────────────────────────────────
    #  ⚠️ 专门验证"只在模板插值里引用未声明变量"这种情况能被抓到。
    #     之前模板串被整体替换成 `` ``, 里面的 `${foo}` 一并消失，
    #     这类 bug 就永远查不出来（而运行时照样 ReferenceError）。
    _fx_bad = "const s = `x=${notDeclaredAnywhere.y}`;"
    _fx_ok = "const notDeclaredAnywhere = { y: 1 };\nconst s = `x=${notDeclaredAnywhere.y}`;"
    r.ok("C16e 自检夹具：模板插值里的未声明变量必须被抓到",
         "notDeclaredAnywhere" in _scan_undeclared(_fx_bad),
         f"夹具扫描={sorted(_scan_undeclared(_fx_bad))}")
    r.ok("C16e 自检夹具：已声明的插值变量不得误报",
         "notDeclaredAnywhere" not in _scan_undeclared(_fx_ok),
         f"夹具扫描={sorted(_scan_undeclared(_fx_ok))}")

    _js = re.search(r'<script>([\s\S]*)</script>', web)
    _code = _js.group(1) if _js else ""
    _code = re.sub(r'/\*[\s\S]*?\*/', '', _code)
    _code = re.sub(r'(?m)//[^\n]*$', '', _code)
    # ⚠️ 模板串不能**整体**删掉：`${foo}` 里的标识符是**真实求值**的，
    #    删了就会漏判（C16e 假绿，运行时却 ReferenceError）。
    #    做法：先把 `${...}` 里的内容抽出来保留，再删掉其余模板文本。
    _code = re.sub(r'`(?:[^`\\]|\\.)*`', _keep_interp_c16e, _code)
    _code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', _code)
    _code = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", _code)
    _declared = set()
    for m in re.finditer(
            r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)', _code):
        _declared.add(m.group(1))
    for m in re.finditer(r'\bfunction\s+([A-Za-z_$][\w$]*)', _code):
        _declared.add(m.group(1))
    # 函数参数也当已声明
    for m in re.finditer(r'\bfunction\s*[\w$]*\s*\(([^)]*)\)', _code):
        for arg in m.group(1).split(","):
            arg = arg.strip()
            if arg:
                _declared.add(arg.split("=")[0].strip())
    # catch 参数（`catch (e)`）与箭头函数参数（`(a, b) =>`）也算已声明，
    # 否则会疯狂误报 `e` / `el` 之类。
    for m in re.finditer(r'\bcatch\s*\(\s*([A-Za-z_$][\w$]*)', _code):
        _declared.add(m.group(1))
    for m in re.finditer(r'\(([^)]*)\)\s*=>', _code):
        for arg in m.group(1).split(","):
            arg = arg.strip().split("=")[0].strip()
            if arg and arg.isidentifier():
                _declared.add(arg)
    for m in re.finditer(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*=>', _code):
        _declared.add(m.group(1))

    _builtins = {
        "document", "window", "chrome", "console", "Math", "JSON", "Date",
        "Array", "Object", "String", "Number", "Boolean", "Promise", "Error",
        "fetch", "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "encodeURIComponent", "decodeURIComponent", "parseInt", "parseFloat",
        "isNaN", "undefined", "null", "true", "false", "this", "new", "typeof",
        "typeof", "return", "if", "else", "for", "while", "do", "function",
        "const", "let", "var", "async", "await", "try", "catch", "finally",
        "class", "throw", "switch", "case", "break", "continue", "delete",
        "in", "of", "instanceof", "void", "yield", "static", "get", "set",
        "navigator", "location", "alert", "confirm", "prompt", "Event",
        "CustomEvent", "Map", "Set", "Symbol", "RegExp", "Infinity", "NaN",
        "arguments", "globalThis", "requestAnimationFrame", "btoa", "atob",
    }
    # 只看"后面跟 . 或 [ 或 .length"的标识符 —— 这些几乎必然是变量引用，
    # 而不是对象字面量的键或属性名。
    _used = {}
    for m in re.finditer(
            r'(?<![\w.$])([a-z_$][\w$]*)(?=\s*(?:\.|\[))', _code):
        nm = m.group(1)
        if nm in _declared or nm in _builtins:
            continue
        _used.setdefault(nm, _code[:m.start()].count("\n") + 1)
    # 再把"只作为裸标识符出现在条件/实参里"的也带上（如 `!ad.length` 的 ad
    # 其实已被上面捕获；这里补 `if (ad)` 这类）
    for m in re.finditer(
            r'(?<![\w.$])([a-z_$][\w$]*)(?=\s*[)\]}])', _code):
        nm = m.group(1)
        if nm in _declared or nm in _builtins or nm in _used:
            continue
        # 排除函数参数尾部、以及 `)` 前的关键字
        if nm in ("return", "typeof", "await", "new", "in", "of", "if",
                  "for", "while", "catch", "switch"):
            continue
        _used.setdefault(nm, _code[:m.start()].count("\n") + 1)
    r.ok("C16e 面板脚本没有未声明的标识符（防漏改变量名）",
         not _used,
         f"疑似未声明={ {k: v for k, v in sorted(_used.items())} or '无' }")

    r.ok("C17 截图前校验标签是否在前台",
         "tab.active" in bg and "不能截" not in bg)

    # ══════════════════════════════════════════════════════════════
    section("D. 运行时坑")
    # ══════════════════════════════════════════════════════════════
    # ⚠️ 用 src_safe 而不是 src()：这些文件里任何一个被删/改名，
    #    直接 src() 会抛 FileNotFoundError，**整个 D 段就此中断** ——
    #    后面 D2/D3/编译检查全都不执行，报告上只剩一条笼统异常，
    #    看不出到底缺了哪个文件。
    for name in ("main.py", "backends/headless_backend.py",
                 "backends/extension_backend.py", "bridge.py", "setup_guide.py"):
        body = src_safe(name)
        if not body:
            r.ok(f"D1 {name} 可读", False, "文件缺失或读不到")
        elif "logger." in body:
            r.ok(f"D1 {name} 的 logger 有定义", "logger = get_logger" in body)

    for name in ("main.py", "backends/headless_backend.py",
                 "backends/extension_backend.py"):
        body = src_safe(name)
        if re.search(r'\basyncio\.', body):
            r.ok(f"D2 {name} 用到 asyncio 且已导入",
                 bool(re.search(r'^import asyncio', body, re.M)))

    # ⚠️ 用 compile() 而不是 py_compile.compile() ——
    #    后者会往**源码树**里写 __pycache__/*.pyc，污染工作目录，
    #    还会让「文件清点」这类检查在跑完一次之后变红。
    bad_compile = []
    targets = ["main.py", "protocol.py", "bridge.py", "security.py",
               "tokens.py", "setup_guide.py", "backends/__init__.py",
               "backends/base.py", "backends/router.py",
               "backends/headless_backend.py", "backends/extension_backend.py"]
    for f in targets:
        if not exists(f):
            continue
        try:
            # 只做语法编译，不落盘任何产物
            compile((PLUGIN_DIR / f).read_text(encoding="utf-8"), str(f), "exec")
        except SyntaxError as e:
            bad_compile.append(f"{f}: {e}")
    r.ok("D3 所有 Python 文件可编译", not bad_compile,
         f"失败={bad_compile or '无'}")

    _mpid = man.get("plugin_id") or ""
    r.ok("D4 plugin_id 与上游一致（避免升级后出现两个插件）",
         _mpid == "headless_browser",
         f"plugin_id={_mpid or '（缺失）'}")
    _author = man.get("author") or ""
    r.ok("D5 manifest 作者含 nyx / znq19 / 萧洋",
         bool(_author) and all(x in _author for x in ("nyx", "znq19", "萧洋")),
         f"author={_author or '（缺失）'}")
    _icon = man.get("icon") or ""
    r.ok("D6 manifest 引用的图标存在", bool(_icon) and exists(_icon),
         f"icon={_icon or '（缺失）'}")
    _icons = exm.get("icons") or {}
    r.ok("D7 扩展图标都在",
         bool(_icons) and all(exists(f"browser-bridge/{v}") for v in _icons.values()),
         f"icons 键={'有' if _icons else '缺失/为空'}")

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
    _sw = (exm.get("background") or {}).get("service_worker") or ""
    reachable = declared | imported | popup_js | ({_sw} if _sw else set())
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
    # ⚠️ 不能直接 read_bytes()：icon.png 不存在会抛 FileNotFoundError，
    #    整个 run 在 E2 处中止 → E3/E4 永远不跑，run_all 只记一条笼统失败。
    #    E1 已把 icon.png 列为必需文件，所以"缺失"是必须被报出来的状态。
    _icon = PLUGIN_DIR / "icon.png"
    try:
        _icon_head = _icon.read_bytes()[:8]
    except OSError as _e:
        r.ok("E2 图标是有效 PNG", False, f"无法读取 icon.png：{_e}")
    else:
        r.ok("E2 图标是有效 PNG", _icon_head == b"\x89PNG\r\n\x1a\n")

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
