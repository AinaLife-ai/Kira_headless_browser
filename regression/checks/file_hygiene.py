"""文件冗余 / 缺失清点。

两个方向都查：
  冗余 —— 文件在但没人引用（草稿、旧版残留、误传）
  缺失 —— 代码引用了但文件不在（会导致运行时报错）
"""

from __future__ import annotations

import re
from pathlib import Path

from ..harness import EXT_DIR, PLUGIN_DIR, ext_manifest, section, src

TITLE = "文件冗余/缺失清点"

#: 运行期临时生成（写到系统临时目录），不是仓库文件
RUNTIME_GENERATED = {"kira_visible_test.html"}

#: 不该出现在提交里的后缀 / 文件名
BAD_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".sqlite", ".DS_Store"}
BAD_NAMES = {".DS_Store", "Thumbs.db"}


#: 回归测试自己的子目录 —— 它们是测试资产，规则与插件本体不同
REGRESSION_DIRS = ("regression",)


def _all_files(include_regression: bool = False):
    """列出参与清点的文件。

    默认**排除 regression/**：那是测试资产（桩、检查脚本、JS 依赖），
    引用关系自成一套，不该用插件的规则去衡量。
    """
    out = []
    for p in PLUGIN_DIR.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(PLUGIN_DIR)
        # ⚠️ 不要在这里剔除 __pycache__ —— 剔了的话 D1/D2 就永远查不到它们，
        #    那两条检查等于形同虚设。让它们进清单，由 D1/D2 去判红。
        if ".git" in rel.parts:
            continue
        if not include_regression and rel.parts and rel.parts[0] in REGRESSION_DIRS:
            continue
        out.append(rel)
    return sorted(out)


def run(r) -> None:
    files = _all_files()
    r.metric("文件数", len(files))
    r.note(f"包里共 {len(files)} 个文件")

    py = [f for f in files if f.suffix == ".py"]
    js = [f for f in files if f.suffix in (".js", ".mjs")]

    # ── A. Python 引用关系 ──────────────────────────────────────────
    section("A. Python 文件引用关系")
    all_py = "\n".join((PLUGIN_DIR / f).read_text(encoding="utf-8") for f in py)
    # ⚠️ 顶层的 main.py/__init__.py 是入口，本来就不会被 import ——
    #    但其它顶层模块必须检查（之前一条 `f.parent == Path(".")` 把
    #    **所有顶层模块**都跳过了，等于大部分文件根本没验）。
    ENTRY_FILES = {"main.py", "__init__.py"}
    for f in py:
        if f.name in ENTRY_FILES:
            continue
        name = f.stem
        used = (f"from .{name} import" in all_py
                or f"from . import {name}" in all_py
                or f"{name}.py" in all_py)
        if f.parent.name == "backends" and f.name != "__init__.py":
            used = used or f"backends.{name}" in all_py
        r.ok(f"A1 {f} 被引用", used)

    # ── B. JS 引用关系 ──────────────────────────────────────────────
    section("B. JS 文件引用关系")
    exm = ext_manifest()
    sw = exm["background"]["service_worker"]
    content_scripts = set()
    for v in exm.get("content_scripts", []):
        content_scripts |= set(v.get("js", []))
    all_js = "\n".join((EXT_DIR / f.name).read_text(encoding="utf-8")
                       for f in js if f.parent.name == "browser-bridge")
    popup_html = src("browser-bridge/popup.html")
    popup_js = set(re.findall(r'src="([\w.]+)"', popup_html))

    for f in js:
        if f.parent.name != "browser-bridge":
            continue
        if f.name == sw:
            r.ok(f"B1 {f.name} = service_worker", True)
        elif f.name in content_scripts:
            r.ok(f"B1 {f.name} = content_script", True)
        else:
            used = (f'"{f.name}"' in all_js or f'"./{f.name}"' in all_js
                    or f.name in popup_js)
            r.ok(f"B2 {f.name} 被 import 或 HTML 引用", used)

    # ── C. 引用到的文件都在 ─────────────────────────────────────────
    section("C. 引用的文件都存在")
    refs = set()
    for s_ in (all_py, all_js, popup_html):
        refs |= set(re.findall(
            r'["\']\.?/?([\w./-]+\.(?:js|mjs|html|png|css))["\']', s_))
    man_refs = set()
    import json
    man_refs.add(json.loads(src("manifest.json")).get("icon", ""))
    for v in exm.get("icons", {}).values():
        man_refs.add("browser-bridge/" + v)
    for v in exm.get("action", {}).get("default_icon", {}).values():
        man_refs.add("browser-bridge/" + v)
    if exm.get("action", {}).get("default_popup"):
        man_refs.add("browser-bridge/" + exm["action"]["default_popup"])

    missing = []
    for x in sorted(refs | man_refs):
        if not x or Path(x).name in RUNTIME_GENERATED:
            continue
        cands = [PLUGIN_DIR / x, EXT_DIR / x, EXT_DIR / Path(x).name]
        if not any(c.is_file() for c in cands):
            missing.append(x)
    r.ok("C1 所有被引用的文件都存在", not missing, f"缺={missing or '无'}")

    # ── D. 不该有的东西 ─────────────────────────────────────────────
    section("D. 不该出现在提交里的东西")
    bad = [f for f in files
           if f.suffix in BAD_SUFFIX or f.name in BAD_NAMES]
    r.ok("D1 无编译产物 / 日志 / 系统文件", not bad, f"发现={bad or '无'}")
    r.ok("D2 无 __pycache__",
         not any("__pycache__" in str(f) for f in files))
    r.ok("D3 无运行时数据目录",
         not any(any(k in str(f) for k in
                     ("cookie", "browser_profile", "inherited_profile",
                      "screenshots"))
                 for f in files))
    # 回归测试自己的产物（node_modules / 临时脚本）不该被提交
    reg_files = list((PLUGIN_DIR / "regression").rglob("*")) \
        if (PLUGIN_DIR / "regression").is_dir() else []
    reg_bad = [p.relative_to(PLUGIN_DIR) for p in reg_files
               if p.is_file()
               # node_modules 是被 .gitignore 掉的依赖目录，不逐个查
               and "node_modules" not in p.parts
               and (p.suffix in BAD_SUFFIX
                    or "__pycache__" in p.parts
                    or p.name.startswith("_click_runner")
                    or p.name == "_ext_client.mjs"
                    # ⚠️ 实际生成的是**带唯一后缀**的 kira_ext_client_*.mjs
                    #    （为了让并发跑互不干扰）。只匹配旧的确切名字，
                    #    这些残留就永远清不出来、D5 会一直报脏。
                    or p.name.startswith("kira_ext_client_"))]
    r.ok("D5 回归测试目录无临时产物/缓存", not reg_bad,
         f"发现={[str(x) for x in reg_bad] or '无'}")
    # 插件本体只允许 README（+ 一份**白名单**的说明文档）。
    # ⚠️ 规则的本意是防"文档散落"，不是禁止一切文档 —— 所以用白名单
    #    而不是放开。目前白名单里只有 SECURITY_DESIGN.md：
    #    它写的是"哪些看似可疑的行为是有意设计"（给自动审查看的），
    #    放在 README 里会淹没正文，独立成篇才是合适的。
    ALLOWED_MD = {"README.md", "SECURITY_DESIGN.md"}
    stray_md = [f for f in files
                if f.suffix == ".md" and f.name not in ALLOWED_MD]
    r.ok("D4 插件本体除 README/白名单外没有游离的 md",
         not stray_md, f"发现={stray_md or '无'}")

    # ── E. .gitignore ───────────────────────────────────────────────
    section("E. .gitignore")
    gi = src(".gitignore") if (PLUGIN_DIR / ".gitignore").is_file() else ""
    need = ["__pycache__", "*.py[cod]", "data/files/cookie", "browser_profile"]
    miss = [k for k in need if k not in gi]
    r.ok("E1 .gitignore 覆盖缓存与运行时数据", not miss, f"缺={miss or '无'}")
    # 自己的回归产物也不该被提交
    r.ok("E2 .gitignore 覆盖回归测试产物",
         "node_modules" in gi and "__pycache__" in gi)
