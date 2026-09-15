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

from ..harness import section, src

TITLE = "接线完整性（配置接通 / 数据透传）"


def run(r) -> None:
    main = src("main.py")
    eb = src("backends/extension_backend.py")
    # 注意：共享状态与常量的定义在 shared.js（不是 background.js）——
    # 这正是之前那次「模块作用域」事故的产物，检查里也要跟上。
    bg = src("browser-bridge/background.js") + src("browser-bridge/shared.js")
    bg_bg = src("browser-bridge/background.js")

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
    eb_set = re.search(r'WRITE_CMDS = \{([^}]+)\}', eb, re.S)
    eb_cmds = set(re.findall(r'"([a-z_]+)"', eb_set.group(1))) if eb_set else set()
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
                           src("backends/headless_backend.py")))
    orphan = sorted(set(sch) - read)
    r.ok("C1 没有「schema 里有但没人读」的配置项",
         not orphan, f"孤儿={orphan or '无'}（共 {len(sch)} 项）")

    # 读出来的配置要真的被用（不能只赋给变量又不引用）
    #: 读进来后换成别的名字使用的配置项（配置名 ≠ 属性名）
    RENAMED = {"command_timeout": "bridge.command_timeout"}

    unread_attrs = []
    for key in sorted(set(sch) & read):
        if key in RENAMED:
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
    mismatch = []
    for key, item in sch.items():
        if isinstance(item.get("default"), bool):
            m2 = _re2.search(rf'cfg\.get\(\s*"{key}",\s*(True|False)', main)
            if m2 and (m2.group(1) == "True") != item["default"]:
                mismatch.append(f"{key}: schema={item['default']} 代码={m2.group(1)}")
        elif isinstance(item.get("default"), int) and not isinstance(item.get("default"), bool):
            # 值可能是表达式（如 `2 * 1024 ** 3`），截到逗号再求值
            m2 = _re2.search(rf'cfg\.get\(\s*"{key}",\s*([^,)]+)', main)
            if m2:
                expr = m2.group(1).strip()
                try:
                    val = int(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
                except Exception:
                    val = None
                if val is not None and val != item["default"]:
                    mismatch.append(f"{key}: schema={item['default']} 代码={expr}")
    r.ok("C3 布尔/整数配置的默认值与 schema 一致", not mismatch,
         f"不一致={mismatch or '无'}")

    # ══════════════════════════════════════════════════════════════
    section("D. 两后端的「确认」行为一致性")
    # ══════════════════════════════════════════════════════════════
    hb = src("backends/headless_backend.py")
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
    base = src("backends/base.py")
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
    r.ok("F2 生命周期回调走了 safeRun 包装",
         "safeRun" in bg_bg and "bootstrap" in bg_bg)

    # ══════════════════════════════════════════════════════════════
    section("G. 扩展侧不能有裸的模块级标识符（ES 模块不共享作用域）")
    # ══════════════════════════════════════════════════════════════
    # 真实事故：把 socket 挪进 shared.js 的 state 之后，
    # background.js 里还留着 `socket.onopen = ...` 这种裸引用 ——
    # 严格模式下直接 ReferenceError，**扩展永远连不上**。
    import re as _re
    # 只看真正的代码行 —— 注释里提到这些名字是**正常的**（说明为什么这么写）
    # ⚠️ 注释必须**完整剥掉**，包括跨行的块注释 ——
    #    逐行过滤只能处理 `//`，块注释（/* ... */，中间那些行不以
    #    注释符开头）会漏，里面的词会被误当成裸引用。
    bg_code = _re2.sub(r'/\*[\s\S]*?\*/', '', bg_bg)        # 块注释
    bg_code = _re2.sub(r'(?m)//[^\n]*$', '', bg_code)          # 行注释
    # ⚠️ 还要剥掉**字符串字面量**（含反引号模板串）。
    #    注释剥完仍会把 `\`...socket 开着...\`` 这种提示文本里的词
    #    当成标识符引用 —— 那是运行时才存在的字符串，不是变量。
    bg_code = _re2.sub(r'`(?:[^`\\]|\\.)*`', '``', bg_code)      # 模板串
    bg_code = _re2.sub(r'"(?:[^"\\\n]|\\.)*"', '""', bg_code)     # 双引号
    bg_code = _re2.sub(r"'(?:[^'\\\n]|\\.)*'", "''", bg_code)     # 单引号
    naked = []
    for m in _re.finditer(r'(?<![\w.$])\b(socket|reconnectAttempt|reconnectTimer|'
                          r'intentionalClose|userDisconnected|lastError)\b', bg_code):
        # 排除"对象字面量的键"（`socket: "OPEN"`）—— 那不是引用
        after = bg_code[m.end():m.end() + 3].lstrip()
        if after.startswith(":"):
            continue
        naked.append(m.group(1))
    naked = sorted(set(naked))
    r.ok("G1 没有裸的状态标识符（都走 state.xxx）", not naked,
         f"裸引用={naked or '无'} —— ES 模块严格模式下会 ReferenceError")
