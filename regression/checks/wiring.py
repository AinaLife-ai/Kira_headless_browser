"""接线完整性：**配置项真的接通了吗、返回的数据真的给到模型了吗**。

这一组针对的是一类特别隐蔽的 bug：
**功能"看起来实现了"，但中间某根线没接上，于是完全不生效。**

例如：
  * 配置项存在、扩展也实现了，但插件从不把开关下发 → 用户开了没有用
  * 后端返回了数据，但渲染层没处理，落到默认分支 → 数据被静默丢掉

这类问题静态看代码很难发现（两边都"写了"），所以专门有一组检查盯着。
"""

from __future__ import annotations

import re

from ..harness import section, src_safe, strip_js_noise

TITLE = "接线完整性（配置接通 / 数据透传）"

#: 扩展侧**不允许裸引用**的状态标识符（ES 模块不共享顶层作用域）
NAKED_STATE_NAMES = (
    "socket|reconnectAttempt|reconnectTimer|"
    "intentionalClose|userDisconnected|lastError"
)


def _keep_interp(m):
    """模板串处理：把 `${...}` 里的内容抽出来保留，其余模板文本删掉。

    ⚠️ 模板串不能**整体**删掉 —— `${socket.readyState}` 里的标识符是
    真实求值的，删了就会漏判（检查假绿，运行时却 ReferenceError）。
    """
    inner = re.findall(r'\$\{([^{}]*)\}', m.group(0))
    return " " + " ".join(inner) + " "


def _scan_naked_state(src_text: str):
    """扫描裸的状态标识符，返回去重排序后的名字列表。

    **G1 检查与它的自检夹具都用这一个函数** —— 之前夹具内联了一份副本，
    结果两边各自演化（夹具用 `code[m.end():...]`、真逻辑改成了局部变量
    `after`），副本已与真逻辑不同步：自检永远绿，真检查坏了也发现不了。
    """
    code = strip_js_noise(src_text)
    found = []
    for m in re.finditer(r'(?<![\w.$])\b(' + NAKED_STATE_NAMES + r')\b', code):
        # 排除"对象字面量的键"（`socket: "OPEN"`）—— 那不是引用
        after = code[m.end():m.end() + 3].lstrip()
        if after.startswith(":"):
            continue
        found.append(m.group(1))
    return sorted(set(found))


def _write_commands_from_protocol() -> set:
    """从 protocol.py 的 `WRITE_COMMANDS` 取真实成员。

    ⚠️ 这是"权威定义"：插件侧的 `WRITE_CMDS` 已经从它派生。
    取真实值（而不是正则扫字面量）才能覆盖"派生形式"，
    也才能验证"protocol 加了新命令时确实会自动生效"。
    """
    # ⚠️ 不要在这里写 `src = src_safe("protocol.py")` —— 那会在本函数内
    #    遮蔽模块级导入的 `src`，之后再调 `src_safe(...)` 就 UnboundLocalError。
    _proto_src = src_safe("protocol.py")
    m = re.search(r'WRITE_COMMANDS\s*=\s*frozenset\(\{(.*?)\}\)', _proto_src, re.S)
    if not m:
        return set()
    body = m.group(1)
    # 成员写成 CMD_XXX 常量 → 去常量表里解析真实字符串值
    consts = dict(re.findall(r'^(CMD_[A-Z_]+)\s*=\s*"([a-z_]+)"', _proto_src, re.M))
    out = set()
    for name in re.findall(r'\b(CMD_[A-Z_]+)\b', body):
        if name in consts:
            out.add(consts[name])
    return out


def run(r) -> None:
    # ⚠️ 前置读取用 src_safe —— 缺文件时各段自己报错，不中断整组。
    main = src_safe("main.py")
    eb = src_safe("backends/extension_backend.py")
    # 注意：共享状态与常量的定义在 shared.js（不是 background.js）——
    # 这正是之前那次「模块作用域」事故的产物，检查里也要跟上。
    bg = src_safe("browser-bridge/background.js") + src_safe("browser-bridge/shared.js")
    bg_bg = src_safe("browser-bridge/background.js")

    # ══════════════════════════════════════════════════════════════
    section("A. 后端返回的数据，渲染层（_render）真的用上了吗")
    # ══════════════════════════════════════════════════════════════

    called = set(re.findall(r'await self\._call\("([a-z_]+)"', main))
    render_map = set(re.findall(r'if method == "([a-z_]+)"', main))
    for grp in re.findall(r'if method in \(([^)]+)\)', main):
        render_map |= set(re.findall(r'"([a-z_]+)"', grp))

    fallthrough = sorted(called - render_map)
    r.ok("A1 每个后端方法的返回值都有专门的渲染分支",
         not fallthrough,
         f"落默认分支（会把数据丢掉）={fallthrough or '无'}")
    r.metric("后端方法数", len(called))
    r.metric("有专门渲染的方法数", len(render_map))

    # cookie_get 是「导出数据」，最不能落默认分支
    seg = main.split('if method == "cookie_get"')
    r.ok("A2 cookie_get 会把 cookie 内容回传（不能只回「完成」）",
         len(seg) > 1 and "cookies" in seg[1][:600],
         "导出的是数据，丢了就等于白跑")

    # list_files / get_info 也要真的回内容
    for m, key in (("list_files", "files"), ("get_info", "title")):
        s_ = main.split(f'if method == "{m}"')
        r.ok(f"A3 {m} 会把 {key} 回传",
             len(s_) > 1 and key in s_[1][:400])

    # ══════════════════════════════════════════════════════════════
    section("B. 配置项真的下发给扩展了吗")
    # ══════════════════════════════════════════════════════════════

    # require_confirm：插件侧配置 → 扩展侧弹通知
    ext_has = "PRIVILEGED_COMMANDS" in bg and "require_confirm" in bg
    backend_sends = ("require_confirm" in eb and "setdefault(\"require_confirm\"" in eb)
    plugin_wires = "require_confirm=self.require_confirm" in main
    r.ok("B1 require_confirm 全链路接通（配置→后端→扩展）",
         ext_has and backend_sends and plugin_wires,
         f"扩展实现={ext_has}；后端下发={backend_sends}；插件接线={plugin_wires}")

    # 确认覆盖的命令集合两端要一致
    bg_set = re.search(r'PRIVILEGED_COMMANDS = new Set\(\[([^\]]+)\]', bg, re.S)
    bg_cmds = set(re.findall(r'"([a-z_]+)"', bg_set.group(1))) if bg_set else set()
    # ⚠️ Python 侧现在是**从 protocol 派生**（`WRITE_CMDS = set(...)`），
    #    不再是字面量集合。判据要认两种形态：
    #      · 派生形式 → 去 protocol.py 里取 **WRITE_COMMANDS 的真实成员**
    #        （那才是权威定义，而且是"加了新命令自动纳入"的保证）
    #      · 字面量形式 → 直接解析（兼容旧写法）
    eb_set = re.search(r'WRITE_CMDS = \{(.*?)\}', eb, re.S)
    if eb_set:
        eb_cmds = set(re.findall(r'"([a-z_]+)"', eb_set.group(1)))
    else:
        eb_cmds = _write_commands_from_protocol()
    only_bg = sorted(bg_cmds - eb_cmds)
    only_eb = sorted(eb_cmds - bg_cmds)
    r.ok("B2 两端「需要确认的命令」集合一致",
         not only_bg and not only_eb,
         f"仅扩展侧={only_bg or '无'}；仅插件侧={only_eb or '无'}")

    # confirm_timeout 也要传（否则扩展用默认值，可能与插件等待时长不匹配）
    r.ok("B3 confirm_timeout 也下发了（两端超时才对得上）",
         "confirm_timeout" in eb and "CONFIRM_WAIT_SECONDS" in main)

    # ══════════════════════════════════════════════════════════════
    section("C. 配置项没有「孤儿」：有 UI、有读取、有使用者")
    # ══════════════════════════════════════════════════════════════

    from ..harness import schema
    sch = schema()
    read = set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', main))
    read |= set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"',
                           src_safe("backends/headless_backend.py")))
    # ⚠️ 只有前端用的配置（比如"启动动画"开关）Python 侧当然不读 ——
    #    把面板也当"使用者"，否则会被误判成孤儿配置。
    _ui = src_safe("web/app.js")
    for _k in list(sch):
        if re.search(r"(?<![\w])" + re.escape(_k) + r"(?![\w])", _ui):
            read.add(_k)
    orphan = sorted(set(sch) - read)
    r.ok("C1 没有「schema 里有但没人读」的配置项",
         not orphan, f"孤儿={orphan or '无'}（共 {len(sch)} 项）")

    # 读出来的配置要真的被用（不能只赋给变量又不引用）
    #: 读进来后换成别的名字使用的配置项（配置名 ≠ 属性名）
    RENAMED = {"command_timeout": "bridge.command_timeout"}
    #: **只有前端用**的配置：Python 侧读它没意义（面板开场播不播动画），
    #: 使用方就是 web/app.js —— 那里出现就算"真的被用了"。
    FRONTEND_ONLY = {"boot_animation", "boot_replay_seconds"}

    unread_attrs = []
    for key in sorted(set(sch) & read):
        if key in RENAMED:
            continue
        if key in FRONTEND_ONLY:
            continue
        attr_uses = len(re.findall(rf'self\.{key}\b', main))
        if attr_uses == 0:
            # 也可能只是透传给 _headless_cfg
            if f'"{key}":' not in main:
                unread_attrs.append(key)
    r.ok("C2 每个配置项都被真正使用（不只是读进来）",
         not unread_attrs,
         f"读了但没用={unread_attrs or '无'}"
         + (f"（已排除改名使用的：{list(RENAMED)}）" if RENAMED else ""))

    # C3 代码里的默认值必须与 schema 一致
    #    （"默认值分裂"：schema 写 true、代码写 false，谁都没注意）
    import re as _re2
    # ⚠️ 必须检查**所有**读配置的地方，不能只看 main.py。
    #    headless_backend.py 也直接读 cfg（如 screenshot_dir），
    #    那里写错默认值同样不会被发现。
    config_sources = [("main.py", main),
                      ("backends/headless_backend.py",
                       src_safe("backends/headless_backend.py"))]
    mismatch = []
    for key, item in sch.items():
        if isinstance(item.get("default"), bool):
            for name, body in config_sources:
                for m2 in _re2.finditer(
                        rf'cfg\.get\(\s*"{key}",\s*(True|False)', body):
                    if (m2.group(1) == "True") != item["default"]:
                        mismatch.append(
                            f"{key}: schema={item['default']} "
                            f"代码={m2.group(1)} @{name}")
        elif isinstance(item.get("default"), int) and not isinstance(item.get("default"), bool):
            # 值可能是表达式（如 `2 * 1024 ** 3`），截到逗号再求值
            for name, body in config_sources:
                for m2 in _re2.finditer(
                        rf'cfg\.get\(\s*"{key}",\s*([^,)]+)', body):
                    expr = m2.group(1).strip()
                    try:
                        val = int(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
                    except Exception:
                        val = None
                    if val is not None and val != item["default"]:
                        mismatch.append(
                            f"{key}: schema={item['default']} "
                            f"代码={expr} @{name}")
    r.ok("C3 布尔/整数配置的默认值与 schema 一致", not mismatch,
         f"不一致={mismatch or '无'}")

    # ══════════════════════════════════════════════════════════════
    section("D. 两后端的「确认」行为一致性")
    # ══════════════════════════════════════════════════════════════
    hb = src_safe("backends/headless_backend.py")
    r.ok("D1 无头后端不做二次确认（它没有通知通道）",
         "require_confirm" not in hb,
         "确认只在扩展桥有意义；无头侧由只读/白名单把关")
    r.ok("D2 只读模式仍会摘掉写工具（与确认无关，是另一道闸）",
         "drop_write_tools_in_read_only" in main)

    # ══════════════════════════════════════════════════════════════
    section("E. 用户拒绝 ≠ 执行失败（绝不能换后端重试）")
    # ══════════════════════════════════════════════════════════════
    # 这是个安全语义问题：如果「拒绝」被当成普通失败，路由会换到下一个后端
    # 把同一件事做了 —— 用户以为自己拦住了，其实没有。
    base = src_safe("backends/base.py")
    r.ok("E1 OpResult 区分「被拒绝」与「失败」",
         "declined" in base and "declined_by_user" in base)
    r.ok("E2 扩展后端用 declined 标记用户拒绝",
         "declined_by_user" in eb)
    r.ok("E3 路由遇到「拒绝」立刻停手，不换后端",
         "declined" in main and "已按你的决定终止" in main)
    # 精确一点：declined 分支里必须是 return，不能 fallthrough 到重试
    seg = main.split('getattr(res, "declined", False)')
    ok_stop = len(seg) > 1 and "return" in seg[1][:400]
    r.ok("E4 declined 分支是 return（不是继续 for 循环）", ok_stop)

    # ══════════════════════════════════════════════════════════════
    section("F. 扩展的 async 回调都兜住了异常")
    # ══════════════════════════════════════════════════════════════
    # MV3 的 background 是 Service Worker：async 事件回调里抛出的
    # unhandled rejection 是 worker 级错误，可能直接把 worker 干掉，
    # 表现就是「扩展莫名其妙掉线了」。
    lines = bg_bg.splitlines()
    naked = []
    for i, ln in enumerate(lines):
        if "addListener(async" in ln:
            seg = "\n".join(lines[i:i + 22])
            if "catch" not in seg:
                naked.append(f"行 {i + 1}: {ln.strip()[:50]}")
    r.ok("F1 没有未兜异常的 async 事件回调", not naked,
         f"未处理={naked or '无'}")
    # ── G1 的**自检夹具** ────────────────────────────────────────────
    #  CodeRabbit 指出的盲区：模板串整体删除会漏掉 `${...}` 里的标识符。
    #  喂一段"只在插值里出现裸标识符"的样本，要求它**必须报错**。
    #
    #  ⚠️ 这里**必须调用 G1 真正用的那个扫描器**（`_scan_naked_state`），
    #     不能内联一份"看起来一样"的副本 —— 副本会与真逻辑一起漂移，
    #     于是自检永远绿、而真检查已经坏了（这正是上一版的毛病：
    #     夹具用 `code[m.end():...]`，真逻辑后来改成了局部变量 `after`，
    #     两边早已不同步）。
    def _scan(src_text):
        return _scan_naked_state(src_text)

    _fixture = 'const s = `readyState=${socket.readyState}`;'
    r.ok("G1 自检夹具：插值里的裸标识符必须被抓到",
         _scan(_fixture) == ["socket"],
         f"夹具扫描结果={_scan(_fixture)}（期望 ['socket']）")
    _clean = 'const s = `readyState=${state.socket.readyState}`;'
    r.ok("G1 自检夹具：正常写法不得误报",
         _scan(_clean) == [],
         f"夹具扫描结果={_scan(_clean)}（期望 []）")

    # ── G1 自检夹具（二）：插值里的**词法陷阱** ─────────────────────
    #  CR 指出：插值体里 `}` 不一定是插值的结束 —— 它可能在字符串 /
    #  注释 / 嵌套模板里。只按 `{`/`}` 计数会在那里**提前收尾**，
    #  剩下的代码被当模板文本丢掉 → 真会抛 ReferenceError 的裸引用**漏检**。
    #  这几条夹具把每种陷阱都钉住（都调用同一个 `_scan`，不写副本）。
    _lex = [
        ("字符串里的 }",
         'const s = `${"}"; socket.readyState}`;'),
        ("转义之后的 }",
         'const s = `${"\\"}" + socket.readyState}`;'),
        ("块注释里的 }",
         'const s = `${/* } */ socket.readyState}`;'),
        ("行注释里的 }",
         'const s = `${ // }\n socket.readyState}`;'),
        ("嵌套模板里的 }",
         'const s = `${`${"}"}` + socket.readyState}`;'),
        ("正则字面量里的 }",
         'const s = `${/}/.test(x) ? socket.readyState : 0}`;'),
    ]
    _missed = [n for n, src in _lex if _scan(src) != ["socket"]]
    r.ok("G1 自检夹具：插值里的词法陷阱不得让裸引用漏检",
         not _missed,
         f"漏检={_missed or '无'}（这些都是真会抛 ReferenceError 的引用）")

    # ── F1b 写命令清单：两侧必须逐字一致 ──────────────────────────
    #  ⚠️ 这两份清单**各自写着"必须与对方保持同步"**，但一直没人验。
    #    它们决定"开了『写操作需确认』时，哪些命令要弹确认框"：
    #    一边漏了，那条命令就会**静默放行**（AI 能在用户没批准的情况下
    #    切标签页 / 关标签页 / 模拟鼠标）。
    #    历史上已经漏过一次（activate_tab/close_tab/mouse_move），
    #    所以这里把它变成机器可判定的约束。
    # ⚠️ 与 B2 用**同一个解析入口**：Python 侧现在是"从 protocol 派生"，
    #    不再是字面量集合。判据要认两种形态，否则一改定义方式就误报。
    _eb_src = src_safe("backends/extension_backend.py")
    _lit = re.search(r'WRITE_CMDS\s*=\s*\{(.*?)\}', _eb_src, re.S)
    if _lit:
        _py_writes = set(re.findall(r'"([a-z_]+)"', _lit.group(1)))
    else:
        _py_writes = _write_commands_from_protocol()
    _js_writes = set(re.findall(
        r'"([a-z_]+)"', re.search(
            r'PRIVILEGED_COMMANDS\s*=\s*new Set\(\[([^\]]*)\]',
            src_safe("browser-bridge/shared.js"), re.S).group(1))) \
        if re.search(r'PRIVILEGED_COMMANDS\s*=\s*new Set\(\[',
                     src_safe("browser-bridge/shared.js")) else set()
    _diff = sorted(_py_writes ^ _js_writes)
    r.ok("F1b 写命令清单两侧一致（Python WRITE_CMDS ⟷ 扩展 PRIVILEGED_COMMANDS）",
         bool(_py_writes) and bool(_js_writes) and not _diff,
         f"仅一侧有={_diff or '无'}（会让那条命令静默绕过确认框）；"
         f"共 {len(_py_writes)} 条")

    r.ok("F2 生命周期回调走了 safeRun 包装",
         "safeRun" in bg_bg and "bootstrap" in bg_bg)

    # ══════════════════════════════════════════════════════════════
    section("G. 扩展侧不能有裸的模块级标识符（ES 模块不共享作用域）")
    # ══════════════════════════════════════════════════════════════
    # 真实事故：把 socket 挪进 shared.js 的 state 之后，
    # background.js 里还留着 `socket.onopen = ...` 这种裸引用 ——
    # 严格模式下直接 ReferenceError，**扩展永远连不上**。
    naked = _scan_naked_state(bg_bg)
    r.ok("G1 没有裸的状态标识符（都走 state.xxx）", not naked,
         f"裸引用={naked or '无'} —— ES 模块严格模式下会 ReferenceError")
