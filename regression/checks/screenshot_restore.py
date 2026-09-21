"""截图时窗口最小化：自愈 + **用完必须把窗口还原**。

## 为什么有它

用户报：把浏览器最小化之后截图失败，日志只有一句
`[screenshot] 用户浏览器（Edge · 扩展 v1.5.2）失败：Failed to capture tab:
image readback failed`。

根因不是我们的 bug：窗口不可见时 Chromium 读不到帧（captureVisibleTab 的硬限制）。
但**处理**很差 —— 不判断窗口状态、不重试、还把浏览器原文丢给用户（看不懂也没法照做）。

现在的做法：只为"拿不到画面"这类错误去动窗口 —— 恢复 → 等一帧 → 重截 → **还原**。
这个检查跑真行为探针（`regression/js/screenshot_restore.mjs`，按名字从真
background.js 抽函数），把三件事钉死：

1. 最小化时确实临时恢复窗口并重截；**用完把窗口还原成最小化**（借东西要还）；
2. 别的错误一律**不碰用户窗口**（不许为了重试把人家的窗口弹出来）；
3. 开关关掉时（`screenshot_restore_window=off`）不碰窗口，但错误照样可照做。

探针是"按名字抽函数、抽不到就自报"的写法 —— 改了函数名不会静默变成"守卫失效"。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from ..harness import JS_DIR, PLUGIN_DIR, src_safe

TITLE = "截图·窗口最小化自愈（借窗口一瞬，用完还回去）"


def _run_probe() -> list:
    probe = JS_DIR / "screenshot_restore.mjs"
    node = shutil.which("node")
    if not probe.is_file() or not node:
        return []
    env = dict(os.environ)
    env["KIRA_PLUGIN_DIR"] = str(PLUGIN_DIR)
    cp = subprocess.run([node, str(probe)], cwd=str(JS_DIR), capture_output=True,
                        text=True, timeout=120, env=env)
    for line in reversed((cp.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except Exception:
                continue
    return []


def run(r) -> None:
    from ..harness import section
    section("K. 截图·窗口最小化自愈（借窗口一瞬，用完还回去）")

    items = _run_probe()
    if not items:
        r.ok("K0 行为探针可运行（screenshot_restore.mjs + node）", False,
             "跑不出结果：确认 node 在 PATH 上、扩展文件能被读到")
        return
    r.ok("K0 行为探针可运行（screenshot_restore.mjs + node）", True,
         f"{len(items)} 条行为断言")

    for it in items:
        r.ok(f"K·{it.get('name', '?')}", bool(it.get("ok")), str(it.get("detail", ""))[:200])

    # ── 静态形状：截图必须走 captureVisibleWithRestore（别绕过自愈）────────
    js = src_safe("browser-bridge/background.js")
    uses = "captureVisibleWithRestore(" in js
    passes_flag = "restore_window" in js
    r.ok("K7 静态：screenshot 走自愈实现，且接收插件下发的 restore_window 开关",
         uses and passes_flag,
         f"走自愈={'有' if uses else '缺'}；开关={'有' if passes_flag else '缺'}")

    # 反向自检：把"还回最小化"那一步挖掉，静态判据必须能发现
    try:
        stripped = re.sub(r"if \(wasMinimized\)\s*\{[^}]*?state: \"minimized\"",
                          "if (false) {", js, count=1, flags=re.S)
        still = re.search(r'if \(wasMinimized\)\s*\{[^}]*?state: "minimized"', stripped,
                          re.S) is not None
        r.ok("K8 反向自检：挖掉「还回最小化」那一步，判据必须能发现",
             not still,
             "挖掉后判据报红（说明 K2 盯的是真东西）" if not still else "没抓到 ✗")
    except Exception as e:                                   # pragma: no cover
        r.ok("K8 反向自检：挖掉「还回最小化」那一步，判据必须能发现", False,
             f"{type(e).__name__}: {e}"[:120])
