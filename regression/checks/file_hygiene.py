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

#: ⚠️ 运行期**正常**会在插件目录里生成的产物 —— 由框架/插件自己写，
#    且已在 .gitignore 里排除。它们出现在磁盘上**不是**问题。
#    （判据必须是"会不会被提交"，不是"磁盘上有没有" ——
#      否则只要用户正常用过一次插件，D1 就必然误报。）
#      data/log.log 就是典型：框架导入插件时会自己写这个日志。
RUNTIME_ARTIFACTS = {
    "data/log.log",
}


#: 回归测试自己的子目录 —— 它们是测试资产，规则与插件本体不同
REGRESSION_DIRS = ("regression",)


def _git_tracked_files():
    """返回 git **已跟踪**的文件（相对插件根的 Path 列表）。

    ⚠️ 为什么不用文件系统扫描：`.gitignore` 挡不住**已经跟踪**的文件
    （曾 `git add -f` 或先提交后加 ignore 都会绕过），
    所以"磁盘上有没有 node_modules"判断不出"会不会被提交"。

    返回 ``None`` 表示拿不到（没有 git / 不是仓库）—— 调用方自行降级。
    """
    import subprocess
    try:
        p = subprocess.run(["git", "ls-files", "-z"],
                           cwd=str(PLUGIN_DIR), capture_output=True,
                           timeout=60)
        if p.returncode != 0:
            return None
        raw = p.stdout.decode("utf-8", errors="replace")
        return [Path(x) for x in raw.split("\0") if x]
    except Exception:
        return None


def _read_gitignore() -> list:
    """读 .gitignore，返回去掉注释/空行的规则列表。"""
    p = PLUGIN_DIR / ".gitignore"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _is_ignored(rel: str, rules: list) -> bool:
    """判断相对路径是否被 .gitignore 覆盖（够用的简化实现）。

    ⚠️ 只做 gitignore 的**常见形态**：目录规则（``x/``）、后缀规则（``*.log``）、
    精确路径。不追求完整语义 —— 这里只用来做一条自检，
    宁可漏判（会报 FAIL 让人来看）也不错判成"已忽略"。
    """
    import fnmatch
    for rule in rules:
        r = rule.rstrip("/")
        if rule.endswith("/"):
            # 目录规则：路径在它里面
            if rel == r or rel.startswith(r + "/"):
                return True
            continue
        if fnmatch.fnmatch(rel, rule):
            return True
        if fnmatch.fnmatch(Path(rel).name, rule):
            return True
    return False


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
    # ⚠️ 判据是"**会不会被提交**"，而不是"磁盘上有没有"。
    #    正常用一次插件就会生成 `screenshots/`、`browser_profile/`、
    #    `data/log.log`、`__pycache__/` 这些东西（都已 .gitignore）——
    #    拿文件系统判红的话，D1/D2/D3 对**任何真实使用过的副本**都必然误报，
    #    而那不是产品问题。
    #
    #    优先用 git 跟踪清单（`.gitignore` 拦不住**已跟踪**的文件，
    #    所以"跟踪清单"才是唯一准确的判据）；没有 git 时退回
    #    "扫文件系统 + 排除已忽略的路径"。
    tracked = _git_tracked_files()
    _gi = _read_gitignore()
    if tracked is None:
        commit_files = [f for f in files if not _is_ignored(str(f), _gi)]
        r.note(f"   无 git，D1~D3 按 .gitignore 过滤后的清单判定"
               f"（{len(commit_files)}/{len(files)} 个文件）")
    else:
        commit_files = [f for f in tracked
                        if not (f.parts and f.parts[0] in REGRESSION_DIRS)]
        r.note(f"   D1~D3 按 git 跟踪清单判定（{len(commit_files)} 个文件）")

    _checked = [f for f in commit_files if str(f) not in RUNTIME_ARTIFACTS]
    bad = [f for f in _checked
           if f.suffix in BAD_SUFFIX or f.name in BAD_NAMES]
    r.ok("D1 无编译产物 / 日志 / 系统文件", not bad, f"发现={bad or '无'}")
    # 反向保护：白名单里的东西**必须**真的被 .gitignore 排除，
    # 否则就变成"用白名单掩盖漏提交"。
    _unignored = [a for a in RUNTIME_ARTIFACTS if not _is_ignored(a, _gi)]
    r.ok("D1b 运行期产物确实被 .gitignore 排除（白名单不是遮羞布）",
         not _unignored,
         f"未被忽略={_unignored or '无'}；白名单={sorted(RUNTIME_ARTIFACTS)}")
    r.ok("D2 无 __pycache__",
         not any("__pycache__" in str(f) for f in commit_files))
    # ⚠️ 判据是"**目录**里的运行时数据"，不是"路径里含某个词" ——
    #    后者会误伤源码文件（例如 `cookies.py` 含 "cookie"，
    #    但它是模块不是数据目录）。
    _RUNTIME_DIR_MARKERS = ("cookie", "browser_profile", "inherited_profile",
                            "screenshots")
    _runtime_hits = []
    for f in commit_files:
        rel = str(f)
        # 只看这些标记作为**路径片段**出现（后面跟着 / 或者是目录本身）
        if any(f"/{k}/" in f"/{rel}" or f"/{k}s/" in f"/{rel}"
               for k in _RUNTIME_DIR_MARKERS):
            _runtime_hits.append(rel)
    r.ok("D3 无运行时数据目录", not _runtime_hits,
         f"发现={_runtime_hits or '无'}")
    # 回归测试自己的产物（node_modules / 临时脚本）不该被提交
    # ⚠️ 同样用跟踪清单 —— 只靠"排除 node_modules 目录"是不够的：
    #    `.gitignore` 拦不住**已经跟踪**（或曾被 `git add -f`）的文件，
    #    那些依赖产物会稳稳通过 D5。
    reg_files = [t for t in tracked if t.parts and t.parts[0] == "regression"] \
        if tracked else []
    if tracked is None:
        # 没有 git（例如只拿到一个解压出来的目录）→ 退回扫文件系统，
        # 保留原有的排除规则，并说明降级了。
        r.warn("无法读取 git 跟踪清单，D5 退化为文件系统扫描",
               "在 git 仓库里跑能覆盖『已跟踪的依赖产物』")
        reg_files = [p.relative_to(PLUGIN_DIR)
                     for p in (PLUGIN_DIR / "regression").rglob("*")
                     if p.is_file() and "node_modules" not in p.parts] \
            if (PLUGIN_DIR / "regression").is_dir() else []
    reg_bad = [p for p in reg_files
               if p.suffix in BAD_SUFFIX
               or "__pycache__" in p.parts
               or p.name.startswith("_click_runner")
               or p.name == "_ext_client.mjs"
               # ⚠️ 实际生成的是**带唯一后缀**的 kira_ext_client_*.mjs
               #    （为了让并发跑互不干扰）。只匹配旧的确切名字，
               #    这些残留就永远清不出来、D5 会一直报脏。
               or p.name.startswith("kira_ext_client_")
               # ⚠️ node_modules **里的文件**一旦被跟踪就是真问题
               #    （依赖产物不该进版本库）。未跟踪时 git 不会报出来，
               #    所以这里不再需要"整个目录排除"。
               or "node_modules" in p.parts]
    r.ok("D5 回归测试目录无临时产物/缓存（含被跟踪的依赖产物）", not reg_bad,
         f"发现={[str(x) for x in reg_bad[:8]] or '无'}（跟踪清单 {len(reg_files)} 个）")
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
