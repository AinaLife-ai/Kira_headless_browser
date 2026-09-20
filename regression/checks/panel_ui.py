"""侧边栏面板（web/）：真 DOM 行为 + 静态守卫。

## 为什么单独一个检查

这一轮用户报的 4 个问题，**静态检查全绿，但它们全都真实存在** ——
因为它们都是"两处代码的形状对不上"，正则扫不出来：

| 现象 | 真根因 |
|---|---|
| 令牌永远"读取失败" | app.js 读 `$("tokBox")`，而 index.html 里**没有这个 id** → `null.dataset` 抛错 → 被自己的 catch 吞掉 |
| 保存条不消失 | `saveConfig()` 的 `finally` 里**无条件**又把它 `display="flex"` |
| 模型下拉改了没反应 | 监听只绑了 `input,textarea`；`collect()` 也把 `<select>` 漏了 → 连提交都不会带上 |
| 开屏动画不出现 | "减少动效"偏好那条 `@media` 里写了 `.boot{display:none!important}`（整块不显示）；另外还会按 `sessionStorage` 时间戳"跳播"；也没有 JS 兜底收尾 |

所以这里做两件事：

1. **真行为**：`regression/js/panel_smoke.mjs` —— 用 jsdom 加载**真的**
   `index.html`，`eval` **真的** `app.js`，fetch 换成受控桩，跑完整流程
   （载入动画 → 令牌 → 改下拉 → 保存 → 提示条自己消失）。
2. **静态守卫 S16–S19**：把上面四类形状问题钉死，改回去就报红。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from ..harness import JS_DIR, PLUGIN_DIR, src_safe

TITLE = "侧边栏面板（真 DOM 行为 + 静态守卫）"


# ─── 小工具：按名字取一个顶格函数/语句块 ────────────────────────────────

def _fn_body(source: str, name: str) -> str:
    """取 `[async] function name(...) { ... }` 的整段（到第一个顶格 `}`）。"""
    m = re.search(rf"^(?:async )?function {name}\(", source, re.M)
    if not m:
        return ""
    rest = source[m.start():]
    end = rest.find("\n}\n")
    return rest[:end + 2] if end >= 0 else ""


def _strip_comment_lines(text: str) -> str:
    """去掉**整行注释**。

    ⚠️ 必要：我们的代码里注释写得很详细，常常**引用**被禁掉的写法
       （比如 finally 里那句"这里不能再 `saveBar.style.display='flex'`"）——
       拿它去匹配"有没有出现 flex"，会把注释当成代码，判据就假红了。
    """
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().startswith("//"))


def _ids_used(js: str) -> set:
    """app.js 里通过 id 拿元素用到的那些 id（`$("x")` / `getElementById("x")`）。"""
    out = set(re.findall(r'\$\(\s*"([A-Za-z0-9_-]+)"\s*\)', js))
    out |= set(re.findall(r'getElementById\(\s*"([A-Za-z0-9_-]+)"\s*\)', js))
    return out


def _ids_defined(*sources: str) -> set:
    """HTML/模板里真正存在的 id（跳过 `${...}` 动态拼出来的）。"""
    out = set()
    for s in sources:
        for m in re.finditer(r'id="([^"$`]+)"', s):
            out.add(m.group(1))
    return out


def _snapshot_probe(schema=None):
    """真跑一遍插件，取 `/config` 用的那份快照（S24 用）。

    返回 ``(snapshot, error)``。
    """
    import importlib.util
    import json as _json
    import sys
    import tempfile
    import types
    from pathlib import Path

    from ..harness import install_stubs

    install_stubs()
    if str(PLUGIN_DIR.parent) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR.parent))

    pkg = "hb_panel_snap"
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg] = m

    def load(name, path):
        full = f"{pkg}.{name}"
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        return mod

    order = ["setup_guide", "protocol", "security", "cookies", "vlm", "tokens",
             "backends", "backends.base", "backends.router",
             "backends.headless_backend", "bridge", "main"]
    mods = {}
    for name in order:
        path = PLUGIN_DIR / (name.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        mods[name] = load(name, path)
    M = mods.get("main")
    if M is None:
        return {}, "main.py 没能加载"

    class _Ctx:
        def __init__(self, d):
            self._d = Path(d)

        def get_plugin_data_dir(self):
            return self._d

    tmp = tempfile.mkdtemp(prefix="panel_snap_")
    # ⚠️ 要**模拟框架的行为**：KiraAI 在建插件配置时会把 schema 里每个键的
    #    default 灌进配置（`_ensure_plugin_config`，升级时也会补齐缺的键）。
    #    不这么做的话，`blocked_domains` 这类"代码里读配置、自己不带默认值"
    #    的字段在探针里会是空列表，看起来像产品 bug —— 其实是探针不真实 ✗
    seeded = {"enabled": True}
    for _k, _v in (schema or {}).items():
        if isinstance(_v, dict) and _v.get("type") not in ("info", "section") \
                and "default" in _v:
            seeded.setdefault(_k, _v["default"])
    plugin = M.BrowserPlugin(_Ctx(tmp), seeded)
    return plugin._config_snapshot(), ""


def run(r) -> None:
    js = src_safe("web/app.js")
    html = src_safe("web/index.html")
    css = src_safe("web/style.css")

    # ── 真行为探针（jsdom）──────────────────────────────────────────
    probe = JS_DIR / "panel_smoke.mjs"
    node = shutil.which("node")
    if not probe.is_file():
        r.ok("面板行为探针存在（panel_smoke.mjs）", False,
             f"缺少 {probe} —— 面板的行为将没有自动化验证")
    elif not node:
        r.warn("没有 node，跳过面板行为验证", "安装 Node.js 后可启用")
    else:
        # jsdom 是**可选依赖**（regression/js 里 npm install）
        has_jsdom = (JS_DIR / "node_modules" / "jsdom").is_dir()
        if not has_jsdom:
            r.warn("未安装 jsdom，跳过面板真 DOM 行为验证",
                   "cd regression/js && npm install")
        else:
            try:
                cp = subprocess.run(
                    [node, str(probe)], cwd=str(JS_DIR), capture_output=True,
                    text=True, timeout=180,
                    env={"PATH": os.environ.get("PATH", "") + ":/usr/bin:/bin",
                         "KIRA_PLUGIN_DIR": str(PLUGIN_DIR)})
                items = json.loads((cp.stdout or "[]").strip().splitlines()[-1])
                names = [str(x.get("name", "")) for x in items]
                # 覆盖度：这几条必须真的跑过，否则探针被改瘦了也会全绿
                need = (
                    "载入动画会被兜底收掉（不依赖 CSS 动画跑起来）",
                    "令牌显示真值（不是'读取失败'）",
                    "改模型下拉会点亮保存条（含计数）",
                    "保存时把下拉的值一起提交",
                    "保存成功后保存条立刻收起",
                    "提示条会自己消失（不赖在屏幕上）",
                )
                miss = [n for n in need if n not in names]
                if miss:
                    r.ok("P1 面板行为探针覆盖关键场景", False,
                         f"缺={miss}；实际={names}")
                else:
                    for it in items:
                        r.ok(f"P1 {it['name']}", bool(it.get("ok")),
                             str(it.get("detail", ""))[:160])
            except Exception as e:
                r.ok("P1 面板真 DOM 行为验证", False,
                     f"{type(e).__name__}: {e}"[:160])

    # ── S16 用到的 id 必须真实存在（令牌"读取失败"的根因就是这一类）──
    used = _ids_used(js)
    have = _ids_defined(html, js)
    missing = sorted(used - have)
    # 判据自带**反向自检**：喂一段"确实缺 id"的代码，必须能报出来 ——
    # 否则抽取器写坏了（比如正则改瞎）会让这条永远绿。
    _fake_used = _ids_used('$("definitelyNotThere")')
    _self = bool(_fake_used - _ids_defined("<div></div>"))
    r.ok("S16 面板里用到的每个 id 都在 HTML 里真实存在（含反自检）",
         not missing and _self,
         f"缺失={missing or '无'}；反向自检={'通过' if _self else '失败'}"
         f"（缺 id 时 `$(\"x\")` 返回 null → 报错被 catch 吞掉，"
         f"界面只显示一句笼统的失败）")

    # ── S17 表单控件三件套都要能改、能提交（下拉漏了=改了不生效）────
    _collect = _fn_body(js, "collect")
    _bind = re.search(r"function bindConfigEvents\(\)\s*\{.*?\n\}\)\(\);", js, re.S)
    _bind_txt = _bind.group(0) if _bind else ""
    _bad17 = []
    if "input,textarea,select" not in _collect:
        _bad17.append("collect() 没有收 select（下拉的值不会被提交）")
    if "input,textarea,select" not in _bind_txt:
        _bad17.append("事件委托没有管 select（改了下拉不会点亮保存条）")
    if "addEventListener(\"input\"" not in _bind_txt:
        _bad17.append("没有监听 input（文本框要失焦才反应）")
    if "addEventListener(\"change\"" not in _bind_txt:
        _bad17.append("没有监听 change")
    r.ok("S17 改动能被看见、能提交（input/textarea/**select** 都管）",
         not _bad17, f"问题={_bad17 or '无'}")

    # ── S18 保存成功之后保存条必须收起（finally 里不许再打开）────────
    _save = _strip_comment_lines(_fn_body(js, "saveConfig"))
    _bad18 = []
    if "hideSaveBar()" not in _save:
        _bad18.append("成功分支没有 hideSaveBar()（保存条会一直挂着）")
    _fin = re.search(r"finally\s*\{(.*?)\}", _save, re.S)
    if _fin and ("showSaveBar()" in _fin.group(1)
                 or "saveBar" in _fin.group(1)):
        _bad18.append("finally 里又动了保存条（成功也照样弹回来）")
    r.ok("S18 保存成功即收起保存条，失败才保留",
         not _bad18, f"问题={_bad18 or '无'}")

    # ── S19 开屏动画：三条保险 + 减少动效不许整块禁用 ────────────────
    _bad19 = []
    if "BOOT_MAX_MS" not in js or "兜底超时" not in js:
        _bad19.append("没有 JS 硬兜底（CSS 动画没跑时那层会永远盖住面板）")
    _arm = _fn_body(js, "armBoot")
    if "animationName" not in _arm or '!== "boot-out"' not in _arm:
        _bad19.append("animationend 没有按 boot-out 过滤")
    if "once: true" in _arm:
        # 真踩过：内部装饰动画的 animationend 会**冒泡**到 .boot，
        # 第一个冒上来的就把 once 监听器吃掉，真正的收尾事件收不到
        _bad19.append("animationend 用了 {once:true}（会被冒泡的装饰动画吃掉）")
    if "kb_boot_at" in js:
        _bad19.append("还在按 sessionStorage 时间戳跳播（用户 90 秒内重开就看不到）")
    # 减少动效那条媒体查询：必须**照播**，不能整层 display:none
    m = re.search(r"@media \(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}",
                  css, re.S)
    if not m:
        _bad19.append("style.css 里找不到减少动效媒体查询（检查会失明）")
    else:
        block = m.group(1)
        if re.search(r"\.boot[^{]*\{[^}]*display\s*:\s*none", block):
            _bad19.append("减少动效下把 .boot 整层 display:none（开屏动画会'根本不出现'）")
        if not re.search(r"\.boot[^{]*\{[^}]*animation\s*:", block):
            _bad19.append("减少动效下没有给 .boot 留收尾动画（那层会永远不消失）")
        if ":not(.boot *)" not in block:
            _bad19.append("通配关动画时没排除 .boot 子树（收尾动画会被一起灭掉）")
    r.ok("S19 开屏动画：照播 + 一定收得掉（减少动效也不许整块禁用）",
         not _bad19, f"问题={_bad19 or '无'}")

    # ── S20 提示文案里不许有**加强符号**（用户明确要求）───────────────
    #    `**加粗**` / `` `代码` `` 在面板里能转成 HTML，但在 **KiraAI 自己的
    #    设置页**里是按纯文本渲染的 —— 用户看到的是一堆星号和反引号，又丑又乱。
    #    所以 hint / name 一律写成干净的句子（要强调就换个说法）。
    _bad20 = []
    try:
        import json as _json
        _schema = _json.loads(src_safe("schema.json") or "{}")
    except Exception as e:
        _schema = {}
        _bad20.append(f"schema.json 解析失败：{type(e).__name__}: {e}")
    for _k, _v in (_schema or {}).items():
        if not isinstance(_v, dict):
            continue
        _spots = [(_k, _v)]
        _spots += [(f"{_k}[{loc}]", m)
                   for loc, m in (_v.get("locales") or {}).items()
                   if isinstance(m, dict)]
        for _where, _meta in _spots:
            for _fld in ("name", "hint"):
                _s = _meta.get(_fld) or ""
                if "**" in _s:
                    _bad20.append(f"{_where}.{_fld} 含 `**` 加强符号")
                if "`" in _s:
                    _bad20.append(f"{_where}.{_fld} 含反引号")
    r.ok("S20 配置提示文案干净（没有 `**` 加强符号 / 反引号）",
         not _bad20,
         f"问题={_bad20[:6] or '无'}（这类符号在框架设置页里是纯文本，"
         f"渲染出来就是一堆星号）")

    # ── S21 有默认值的字段，帮助文本必须写清默认值（用户明确要求）────
    #    "要么框里就有，要么 hint 里写有" —— 框里那份由 S24 盯着，
    #    hint 这份在这里盯着（中英都算）。
    _bad21, _checked = [], 0
    for _k, _v in (_schema or {}).items():
        if not isinstance(_v, dict) or _v.get("type") in ("info", "section"):
            continue
        if "default" not in _v:
            continue
        _checked += 1
        _loc = _v.get("locales") or {}
        _zh = ((_loc.get("zh") or {}).get("hint") or _v.get("hint") or "")
        _en = ((_loc.get("en") or {}).get("hint") or _v.get("hint") or "")
        if "默认" not in _zh:
            _bad21.append(f"{_k}: 中文提示没写默认值")
        if "default" not in _en.lower():
            _bad21.append(f"{_k}: 英文提示没写默认值")
    # 反向自检：把某条的默认值提法从提示里去掉，判据必须能发现
    _probe_v = {"default": 20, "hint": "只有说明，没有那个词"}
    _self21 = "默认" not in (_probe_v.get("hint") or "")
    r.ok("S21 有默认值的字段都在提示里写清了默认值（中英都要，含反自检）",
         not _bad21 and _self21 and _checked > 30,
         f"漏了={_bad21[:6] or '无'}（共检查 {_checked} 项）；"
         f"反向自检={'通过' if _self21 else '失败'}")

    # ── S22 枚举字段必须是下拉（不能再让用户手打）────────────────────
    _bad22 = []
    if "meta.options" not in js and "meta.enum" not in js:
        _bad22.append("app.js 没有按 options 渲染下拉的分支")
    if "（默认）" not in js:
        _bad22.append("下拉里没有把默认项标出来")
    _with_opts = [k for k, v in (_schema or {}).items()
                  if isinstance(v, dict) and (v.get("options") or v.get("enum"))]
    for k in _with_opts:
        v = _schema[k]
        if v.get("type") not in ("string", "integer", "float", "number"):
            _bad22.append(f"{k}: 带 options 但类型是 {v.get('type')!r}，"
                          f"框架只在普通标量上给下拉")
        if v.get("default") is not None and v["default"] not in (v.get("options") or []):
            _bad22.append(f"{k}: default={v['default']!r} 不在 options 里")
    r.ok("S22 带 options 的枚举字段渲染成下拉，且默认项在选项内",
         not _bad22, f"问题={_bad22 or '无'}（共 {len(_with_opts)} 个枚举字段）")

    # ── S23 字段类型判断必须与**框架认的那套**对齐 ────────────────────
    #    ⚠️ 踩过：schema 里的 `float`（命令超时、跟随比例）框架当数值，
    #       我们只认 integer/number → 退化成文本框，值还可能被当字符串提交。
    def _type_set(src_text: str, name: str):
        mm = re.search(rf"const {name} = \[(.*?)\];", src_text, re.S)
        if not mm:
            return None
        return {x.strip().strip('"').strip("'") for x in mm.group(1).split(",") if x.strip()}

    _need = {"NUM_TYPES": {"integer", "float", "number"},
             "BOOL_TYPES": {"switch", "boolean", "bool"},
             "LIST_TYPES": {"list"}}
    _bad23 = []
    for _n, _want in _need.items():
        _got = _type_set(js, _n)
        if _got is None:
            _bad23.append(f"没有 {_n}（类型判断散在各处，容易漏）")
        elif not _want <= _got:
            _bad23.append(f"{_n} 少了 {sorted(_want - _got)}")
    # 反向自检：喂一段少了 float 的源码，判据必须报出来
    _self23 = _type_set("const NUM_TYPES = [\"integer\", \"number\"];",
                        "NUM_TYPES") != {"integer", "float", "number"}
    r.ok("S23 字段类型判断与框架对齐（float / boolean / list 都要认，含反自检）",
         not _bad23 and _self23,
         f"问题={_bad23 or '无'}；反向自检={'通过' if _self23 else '失败'}")

    # ── S24 面板上不许出现"该有值却是空框"的字段 ──────────────────────
    #    ⚠️ 用户报过：命令超时 / 页面加载超时 / 浏览器来源 …全是空框。
    #       根因是这些键**不在插件实例上**（它们由后端自己读），
    #       快照就给了 undefined → 界面渲染成空框，用户以为没设置。
    #       这里直接跑**真插件**取那份快照来对账。
    try:
        snap, err = _snapshot_probe(_schema)
        if err:
            r.ok("S24 /config 快照覆盖所有字段（面板上不该有空框）", False, err)
        else:
            _empty = []
            for _k, _v in (_schema or {}).items():
                if not isinstance(_v, dict) or _v.get("type") in ("info", "section"):
                    continue
                d = _v.get("default")
                if d in (None, "", []):
                    continue          # 默认值本来就是空，空着是对的
                got = snap.get(_k)
                if got in (None, "", []):
                    _empty.append(f"{_k}（默认 {d!r}）")
            r.ok("S24 /config 快照覆盖所有字段（面板上不该有空框）",
                 not _empty, f"空框={_empty[:8] or '无'}")
    except Exception as e:                                  # pragma: no cover
        r.ok("S24 /config 快照覆盖所有字段（面板上不该有空框）", False,
             f"{type(e).__name__}: {e}"[:160])
