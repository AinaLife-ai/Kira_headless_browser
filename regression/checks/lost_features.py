"""v2.1.0 重写时丢掉的能力 —— 逐项守住，别再丢第二次。

## 背景

v2.1.0 重写双后端架构时，**整段丢掉了三样东西**：

1. **cookie 目录自动加载**（`_load_cookies`）——
   `data/files/cookie/` 目录还在 `.gitignore` 里，但已无代码读它，成了死目录；
   用户重装/换机器后登录态找不回来。
2. **自动下载内置 Chromium**（`_download_chromium`）——
   原版是启动回退的第 4 级；丢之后只剩一句"请手动安装"的提示，
   **而 README 一直在承诺"全部失败会自动下载"**（文档说有、代码没有）。
3. **`browser_check_vlm` 工具** —— VLM 配置的自查入口。

这三样当时**没有任何检查会发现**：缺功能不是"报错"，是"静默没有"。

所以这个模块专门守它们 —— 结构 + 行为两层。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..harness import PLUGIN_DIR, section, src_safe

TITLE = "重写时丢失的能力（cookie / 下载浏览器 / VLM 自查）"

#: 这些文件是"能力本体"，丢了就是能力没了
CORE_FILES = {
    "cookies.py": "cookie 目录自动加载",
    "vlm.py": "截图 → VLM 描述",
}


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


COOKIE_PROBE = r'''
import asyncio, importlib.util, json, os, sys, tempfile
PLUGIN = os.environ["KIRA_PLUGIN_DIR"]
sys.path.insert(0, PLUGIN); sys.path.insert(0, os.environ["KIRA_FW_DIR"])
spec = importlib.util.spec_from_file_location("ck", os.path.join(PLUGIN, "cookies.py"))
ck = importlib.util.module_from_spec(spec); sys.modules["ck"] = ck
spec.loader.exec_module(ck)

class Ctx:
    def __init__(self): self.added = []
    async def add_cookies(self, items): self.added.extend(items)

async def main():
    out = {}
    # ⚠️ 用 TemporaryDirectory（而不是 mkdtemp）—— mkdtemp 建的目录
    #    没人清理，每跑一次回归就留一份 cookie 夹具在 /tmp 里。
    with tempfile.TemporaryDirectory(prefix="kira_cookie_fixture_") as d:
        return await _run_cases(out, d)


async def _run_cases(out, d):
    json.dump({"cookies": [{"name": "sid", "value": "v", "domain": ".e.com",
        "secure": True, "httpOnly": True, "sameSite": "no_restriction",
        "expirationDate": 1893456000.5}]}, open(os.path.join(d, "a.json"), "w"))
    json.dump([{"name": "tok", "value": "v2", "domain": ".b.com",
        "sameSite": "strict"}], open(os.path.join(d, "b.json"), "w"))
    open(os.path.join(d, "broken.json"), "w").write("{ not json")

    ctx = Ctx()
    st = await ck.load_into_context(ctx, d)
    out["two_loaded"] = st["loaded"] == 2
    out["broken_isolated"] = any("broken" in e for e in st["errors"])
    a = [c for c in ctx.added if c["name"] == "sid"][0]
    out["samesite"] = a["sameSite"] == "None"
    out["expires"] = a["expires"] == 1893456000

    s2 = await ck.load_into_context(Ctx(), os.path.join(d, "no", "dir"))
    out["missing_dir_created"] = os.path.isdir(os.path.join(d, "no", "dir"))
    s3 = await ck.load_into_context(None, d)
    out["none_ctx_ok"] = s3["loaded"] == 0
    print("RESULT:" + json.dumps(out))

asyncio.run(main())
'''


def run(r) -> None:
    section("A. 三样能力都还在（结构）")

    main = src_safe("main.py")
    hb = src_safe("backends/headless_backend.py")
    sch = {}
    try:
        sch = json.loads(src_safe("schema.json"))
    except Exception:
        pass

    # ── ① cookie 自动加载 ─────────────────────────────────────────────
    r.ok("A1 cookies.py 存在", (PLUGIN_DIR / "cookies.py").is_file(),
         "cookie 目录自动加载的实现")
    r.ok("A2 无头后端启动时真的会加载它",
         "load_into_context" in hb and "cookies_dir" in hb,
         "只写模块不接线 = 等于没有")
    r.ok("A3 有 load_cookies_on_start 开关", "load_cookies_on_start" in sch)
    r.ok("A4 有 cookies_dir 配置", "cookies_dir" in sch)

    # ── ② 自动下载内置 Chromium ───────────────────────────────────────
    # ⚠️ 查的是**函数定义**，不是名字是否出现 ——
    #    提示文案里也写着 `playwright install chromium`，
    #    只查名字的话，实现被删了照样能过。
    r.ok("A5 有 _download_chromium 实现",
         "async def _download_chromium(" in hb,
         "README 承诺『全部失败会自动下载内置 Chromium』")
    r.ok("A6 启动回退里真的会调它",
         "_allow_auto_download" in hb and "await self._download_chromium(" in hb)
    r.ok("A7 有 auto_download_browser 开关", "auto_download_browser" in sch)
    r.ok("A8 下载有超时保护（不能无限等）",
         "auto_download_timeout" in hb and "wait_for" in hb)
    r.ok("A9 下载失败时给出手动安装命令（而不是裸报错）",
         "playwright install chromium" in hb)

    # ── ③ browser_check_vlm ──────────────────────────────────────────
    r.ok("A10 browser_check_vlm 工具在", 'name="browser_check_vlm"' in main,
         "VLM 配置的自查入口（原版有，重写时丢了）")
    r.ok("A11 它会指出『模型配错组』这个坑",
         "大语言模型" in main and "图像" in main)

    # ── README 与代码一致 ────────────────────────────────────────────
    readme = src_safe("README.md")
    r.ok("A12 README 承诺的『自动下载』现在真的实现了",
         "自动下载" in readme and "_download_chromium" in hb,
         "曾经是『文档说有、代码没有』")
    r.ok("A13 README 提到 cookie 自动加载",
         "cookie" in readme.lower() and "自动加载" in readme)

    section("B. 行为：cookie 加载真跑一遍")

    if not Path(os.environ.get("KIRA_FW_DIR", "/tmp/kiraai_latest")).is_dir():
        r.warn("未提供框架目录，跳过 cookie 行为测试", "设置 KIRA_FW_DIR 后启用")
        return

    data = _run_probe(COOKIE_PROBE)
    if "__error__" in data:
        r.ok("B0 cookie 探针可运行", False, data["__error__"])
        return
    cases = [
        ("B1 两种格式（裸数组 / {cookies:[]}）都能载入", "two_loaded"),
        ("B2 坏文件只跳过它，不连累其它站点", "broken_isolated"),
        ("B3 sameSite 映射正确（no_restriction→None）", "samesite"),
        ("B4 expirationDate 转成 expires", "expires"),
        ("B5 目录不存在时自动创建", "missing_dir_created"),
        ("B6 context 为 None 时不抛异常", "none_ctx_ok"),
    ]
    for label, key in cases:
        r.ok(label, bool(data.get(key)), f"探针结果={data.get(key)}")
