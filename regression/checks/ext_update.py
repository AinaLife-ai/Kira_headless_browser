"""扩展版本提示：面板要能发现"用户在跑旧版扩展"。

## 为什么要有它

扩展是**随插件打包**的（`browser-bridge/`），插件自己加了新能力、旧扩展却
连不上那些能力时，用户看到的只是"某个工具不好用"，很难联想到是扩展旧了。
最典型的一次：插件侧把桥接改成"空闲先探测再判死"、扩展侧加了半开连接自愈，
没更新的扩展就吃不到后者。

所以面板要**主动说**："你用的是 v1.4.0，插件自带的已经是 v1.5.0"，
并给出更新步骤（复用现成的 `/extension` 接口）。

## 设计要点（"未来都可以检测"就靠这两条）

1. **判断只在后端做一次**（`setup_guide.extension_update_state` →
   `/status.extension`）。前端只按 `state` 取文案，**不许出现任何版本号字面量** ——
   以后插件把扩展升到 1.6.0，提示会自动出现，前端一行都不用改。
2. **版本号来自两处真源**：插件自带的 `browser-bridge/manifest.json`，
   和扩展握手时上报的 `chrome.runtime.getManifest().version`。
   两边都不写死在代码里。

这个检查盯四件事：版本比较的行为（含字符串比较陷阱、预发布、垃圾串）、
状态机、"插件升级后提示会自动出现"（拿假 manifest 模拟未来版本）、
以及**前后端状态名必须一致**（真实的一致性审计）。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

from ..harness import PLUGIN_DIR, load_module, src_safe

TITLE = "扩展版本提示（旧版扩展要能发现）"


def _load_setup_guide():
    from ..harness import install_stubs
    install_stubs()
    return load_module("setup_guide", PLUGIN_DIR / "setup_guide.py",
                       "hb_extver", PLUGIN_DIR)


def _load_main():
    """按依赖顺序把插件模块挂到包下面加载，返回 main 模块。"""
    from ..harness import install_stubs
    install_stubs()
    pkg = "hb_extver_main"
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg] = m
    order = ["setup_guide", "protocol", "security", "cookies", "vlm", "tokens",
             "backends", "backends.base", "backends.router",
             "backends.headless_backend", "bridge", "main"]
    mods = {}
    for name in order:
        path = PLUGIN_DIR / (name.replace(".", "/") + ".py")
        if path.is_file():
            mods[name] = load_module(name, path, pkg, PLUGIN_DIR)
    return mods.get("main")


def run(r) -> None:
    js = src_safe("web/app.js")
    html = src_safe("web/index.html")

    try:
        sg = _load_setup_guide()
    except Exception as e:
        r.ok("能加载 setup_guide（版本比较所在的模块）", False,
             f"{type(e).__name__}: {e}")
        return

    # ── V1 版本比较（别用字符串比大小：'1.10' < '1.9' 是错的）─────────
    _bad1 = []
    if sg.compare_versions("1.10.0", "1.9.0") != 1:
        _bad1.append("1.10.0 没有判成比 1.9.0 新（典型字符串比较陷阱）")
    if sg.compare_versions("1.5.0", "1.5.0") != 0:
        _bad1.append("同版本没判成相等")
    if sg.compare_versions("1.4.9", "1.5.0") != -1:
        _bad1.append("旧版本没判成更旧")
    if sg.compare_versions("1.5.0-beta", "1.5.0") != -1:
        _bad1.append("预发布版应比同号正式版旧（写反了提示就永远不出现）")
    if sg.parse_version("v1.5") != sg.parse_version("1.5.0"):
        _bad1.append("`v1.5` / `1.5.0` 没归一成同一个版本")
    if sg.compare_versions("abc", "1.5.0") is not None:
        _bad1.append("解析不了的版本号应返回 None（而不是瞎判）")
    r.ok("V1 版本比较正确（字符串陷阱 / 预发布 / 垃圾串都兜住）",
         not _bad1, f"问题={_bad1 or '无'}")

    # ── V2 状态机 ────────────────────────────────────────────────────
    bundled = sg.bundled_extension_version(PLUGIN_DIR)
    r.ok("V2 能读到插件自带扩展的版本号（来自 browser-bridge/manifest.json）",
         bool(bundled), f"bundled={bundled!r}")
    cases = [
        ("没连上不提示", dict(connected=None, is_connected=False), False),
        ("连上但没上报版本 → 提示", dict(connected=None, is_connected=True), True),
        ("比自带旧 → 提示", dict(connected="0.0.1", is_connected=True), True),
        ("同版本 → 不提示", dict(connected=bundled, is_connected=True), False),
        ("比自带新 → 不提示（用户自己换了新版，别催他）",
         dict(connected="99.99.99", is_connected=True), False),
        ("协议不匹配 → 提示",
         dict(connected=bundled, is_connected=True, protocol_ok=False), True),
    ]
    _bad2 = []
    for name, kw, want in cases:
        st = sg.extension_update_state(bundled=bundled, **kw)
        if bool(st.get("needs_update")) != want:
            _bad2.append(f"{name}（得到 {st.get('state')}）")
    r.ok("V2 状态机：该提示的提示、不该提示的闭嘴", not _bad2,
         f"问题={_bad2 or '无'}")

    # ── V3 「未来都可以检测」：插件升了扩展版本，提示要自动出现 ──────
    #    用假 manifest 模拟"以后插件自带 9.9.9"，用户那边还是现在这版
    _fake = sg.bundled_extension_version(PLUGIN_DIR, manifest={"version": "9.9.9"})
    _st = sg.extension_update_state(bundled=_fake, connected=bundled,
                                    is_connected=True)
    r.ok("V3 插件自带版本一升，提示自动出现（无需改前端）",
         _st["state"] == sg.EXT_UPDATE_AVAILABLE and _st["needs_update"] is True,
         f"内置={bundled} · 假装自带={_fake} → {_st['state']}")

    # ── V4 前后端**状态名**必须一致（一致性审计）────────────────────
    #    后端给 state、前端按 state 取文案。名字对不上 = 提示永远不显示，
    #    而且这种错**静态看着都很正常**（两边各自都合法）→ 必须真的对一遍。
    #    对齐的是"需要提示的那几个状态"：EXT_NOTICE_STATES ↔ 前端 EXT_TEXT 的键。
    _m = re.search(r"const EXT_TEXT = \{(.*?)\n\};", js, re.S)
    #    前端结构是 { zh: {...}, en: {...} } —— 取 zh 那一层的键
    _zh_block = ""
    if _m:
        _zh = re.search(r"zh:\s*\{(.*?)\n  \},", _m.group(1), re.S)
        _zh_block = _zh.group(1) if _zh else _m.group(1)
    _front = set(re.findall(r"^\s{4}([a-z_]+):", _zh_block, re.M))
    _backend = set(getattr(sg, "EXT_NOTICE_STATES", ()))
    _only_backend = sorted(_backend - _front)
    _only_front = sorted(_front - _backend)
    # 反向自检：故意加一个后端没有的状态名，判据必须能发现
    _self4 = bool((_front | {"definitely_not_a_state"}) - _backend)
    r.ok("V4 前后端状态名一致（EXT_NOTICE_STATES ↔ 前端文案键，含反自检）",
         bool(_backend) and not _only_backend and not _only_front and _self4,
         f"后端有提示没文案={_only_backend}；前端有文案后端没有={_only_front}；"
         f"后端={sorted(_backend)} 前端={sorted(_front)}")

    # ── V4b 前端读的字段必须是后端真的给的（前后端一致性）────────────
    #    这类错**最难发现**：前端读 `ext.bundledVersion`、后端给
    #    `bundled_version` —— 两边各自都"语法正确"，界面上只是少显示一行，
    #    没人报错、也没人发现。
    _provided = {"state", "needs_update", "bundled_version",
                 "connected_version", "protocol_ok"}
    _read = set(re.findall(r"\b_?ext\.([A-Za-z_][A-Za-z0-9_]*)", js))
    _unknown = sorted(_read - _provided)
    # 反向自检：喂一个后端没有的字段名，判据必须发现
    _self4b = bool({"_definitely_not_provided"} - _provided)
    r.ok("V4b 前端只读后端真的给的字段（含反自检）",
         not _unknown and _self4b and bool(_read),
         f"前端读了但后端没给={_unknown or '无'}；前端用到={sorted(_read)}；"
         f"反向自检={'通过' if _self4b else '失败'}")

    # ── V5 前端不许写死扩展版本号（写死 = 以后永远发现不了新版本）─────────
    #    ⚠️ 判据要拿**当前自带的那个版本**去搜，而不是"像版本号的都算" ——
    #       后者会被 SVG 图标路径（`c1.1 0 2-.9`）和 `127.0.0.1` 误伤，
    #       假红一次之后大家就学会忽略它了。
    _html = html
    _hits = [f for f in ("web/app.js", "web/index.html")
             if bundled and bundled in (js if f.endswith(".js") else _html)]
    r.ok("V5 前端没有写死扩展版本号（以后换版本不用改前端）",
         not _hits and bool(bundled),
         f"出现 {bundled!r} 的文件={_hits or '无'}")

    # ── V6 接线：/status 要带 extension 块，面板要真的渲染它 ──────────
    _bad6 = []
    if "self.bridge.info" not in src_safe("main.py") or '"extension": _ext_state' not in src_safe("main.py"):
        _bad6.append("/status 没有返回 extension 块")
    if "setup_guide.extension_update_state" not in src_safe("main.py"):
        _bad6.append("/status 没有调用后端的版本判断（前端会各算一套）")
    if "renderExtNotice(" not in js:
        _bad6.append("前端没有渲染提示")
    if 'id="extNotice"' not in html or 'id="extSteps"' not in html:
        _bad6.append("HTML 缺少提示容器（#extNotice / #extSteps）")
    if "/extension" not in js:
        _bad6.append("提示里没有复用 /extension 的更新步骤")
    r.ok("V6 接线完整：/status 出数据 → 面板渲染 → 给出更新步骤",
         not _bad6, f"问题={_bad6 or '无'}")

    # ── V8 改了扩展代码就必须升扩展版本（否则用户永远收不到更新）────
    #    ⚠️ 真事故（2026-09-22）：插件侧加了"空闲先探测再判死"、扩展侧加了
    #       "半开连接自愈"，扩展代码改了四个文件，`browser-bridge/manifest.json`
    #       的 version 却还是 1.5.0 ⇒ 面板比对两边版本号一样 → 判定"已是最新"
    #       → **一个字都不提示**，用户永远停在旧扩展上。
    #       而这套提示本来就是为了防这个的 —— 只能说守卫不够。
    try:
        import importlib.util
        _stamp_tool = PLUGIN_DIR / "regression" / "ext_version_stamp.py"
        spec = importlib.util.spec_from_file_location("hb_ext_stamp", _stamp_tool)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        _problems = _mod.check()

        # 反向自检：把扩展代码"改一个字节"，判据必须能发现（证明指纹真的覆盖内容）
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory(prefix="extstamp_") as _td:
            _copy = Path(_td) / "browser-bridge"
            shutil.copytree(PLUGIN_DIR / "browser-bridge", _copy)
            _js = _copy / "background.js"
            _js.write_text(_js.read_text(encoding="utf-8") + "\n// touched\n",
                           encoding="utf-8")
            _self8 = bool(_mod.check(ext_dir=_copy))
        r.ok("V8 改了扩展代码就必须升扩展版本（含反自检）",
             not _problems and _self8,
             f"问题={_problems or '无'}；反向自检={'通过' if _self8 else '失败'}"
             f"（不一致时跑 regression/ext_version_stamp.py 刷新戳）")
    except Exception as e:                                  # pragma: no cover
        r.ok("V8 扩展版本戳检查", False, f"{type(e).__name__}: {e}"[:160])

    # ── V9 真跑一遍 api_status（形状对不上的话前端会静默不显示）───────
    try:
        import asyncio
        import tempfile

        M = _load_main()
        if M is None:
            raise RuntimeError("main.py 没能加载")

        class _Ctx:
            def __init__(self, d):
                self._d = Path(d)

            def get_plugin_data_dir(self):
                return self._d

        plugin = M.BrowserPlugin(_Ctx(tempfile.mkdtemp(prefix="extver_")),
                                 {"enabled": True})
        fake = getattr(M.setup_guide, "HelloPayload", None)  # 仅用于类型提示
        _ = fake
        plugin.bridge._ws = object()                 # 假装"扩展连着"
        plugin.bridge._hello = M.P.HelloPayload.from_wire({
            "type": "hello", "protocol": M.P.PROTOCOL_VERSION,
            "extension_version": "0.0.1", "browser": "Chrome"})
        st = asyncio.run(plugin.api_status())
        _ext = st.get("extension") or {}
        _need = {"state", "needs_update", "bundled_version",
                 "connected_version", "protocol_ok"}
        _miss = sorted(_need - set(_ext))
        r.ok("V9 api_status 真跑：extension 块的字段齐全且判定正确",
             not _miss and _ext.get("state") == "update_available"
             and _ext.get("needs_update") is True,
             f"缺字段={_miss or '无'}；state={_ext.get('state')}；"
             f"connected={_ext.get('connected_version')} "
             f"bundled={_ext.get('bundled_version')}")

        # 反过来：换成当前版本 → 不该提示
        plugin.bridge._hello = M.P.HelloPayload.from_wire({
            "type": "hello", "protocol": M.P.PROTOCOL_VERSION,
            "extension_version": _ext.get("bundled_version"), "browser": "Chrome"})
        st2 = asyncio.run(plugin.api_status())
        ext2 = st2.get("extension") or {}
        r.ok("V9 同版本时不提示（不会无脑催用户更新）",
             ext2.get("needs_update") is False
             and ext2.get("state") == "up_to_date",
             f"state={ext2.get('state')}")
    except Exception as e:                                  # pragma: no cover
        r.ok("V9 api_status 真跑一遍", False, f"{type(e).__name__}: {e}"[:180])
