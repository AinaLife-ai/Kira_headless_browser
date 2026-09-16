"""扩展 content.js 的点击行为（用真实 DOM 跑）。

最关键的一条：**一次 browser_click 只能产生一次点击**。
历史上这里有个 bug —— 派发完 click 之后又调了 `target.click()`，
导致 button / [role=button] 收到两次点击，下单、发帖会重复提交。

需要 jsdom。没装就跳过（并提示怎么装）。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..harness import HERE, JS_DIR, PLUGIN_DIR, section

TITLE = "扩展点击行为（真实 DOM）"

RUNNER = r"""
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const contentPath = process.env.CONTENT_JS;
const src = readFileSync(contentPath, "utf8");

// 从 content.js 里原样切出 click() 实现（不改一个字符）
function extractFn(s, name) {
  const start = s.indexOf(`    ${name}(payload) {`);
  if (start === -1) throw new Error(`找不到 ${name}`);
  let i = s.indexOf("{", start), depth = 0;
  for (let j = i; j < s.length; j++) {
    if (s[j] === "{") depth++;
    else if (s[j] === "}") { depth--; if (depth === 0) return s.slice(start, j + 1); }
  }
  throw new Error("括号不匹配");
}

const helpers = String.raw`
function isVisible(el) {
  if (!el || !el.getBoundingClientRect) return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return false;
  const st = window.getComputedStyle(el);
  if (st.display === "none" || st.visibility === "hidden" || st.opacity === "0") return false;
  return true;
}
function cleanText(t) {
  return (t || "").replace(/\r\n/g, "\n").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
}
function collectInteractive() {
  const sel = "a[href], button, input[type=submit], input[type=button], input[type=checkbox], input[type=radio], [role=button], [onclick], summary, label[for]";
  return Array.from(document.querySelectorAll(sel)).filter(isVisible)
    .map((el, i) => ({ index: i, tag: el.tagName.toLowerCase(), text: "", el }));
}
function flash(el) {}
function fail(m) { return { __error: m }; }
function mouseInit(x, y, button, cc) {
  return { bubbles: true, cancelable: true, view: window,
           clientX: Number(x), clientY: Number(y),
           button: (button === "right" ? 2 : button === "middle" ? 1 : 0),
           buttons: (button === "right" ? 2 : button === "middle" ? 4 : 1),
           clickCount: Number(cc) || 1 };
}
`;

function patchLayout(win) {
  win.Element.prototype.getBoundingClientRect = function () {
    return { x: 10, y: 10, width: 120, height: 30,
             top: 10, left: 10, right: 130, bottom: 40 };
  };
  if (!("innerText" in win.HTMLElement.prototype)) {
    Object.defineProperty(win.HTMLElement.prototype, "innerText", {
      get() { return this.textContent; }, set(v) { this.textContent = v; },
      configurable: true,
    });
  }
}

const HTML = `<!doctype html><html><body>
  <a id="link" href="https://example.com/t">链接</a>
  <button id="btn">按钮</button>
  <div id="div" role="button">div按钮</div>
</body></html>`;

const dom = new JSDOM(HTML, { url: "https://test.local/" });
patchLayout(dom.window);
global.window = dom.window; global.document = dom.window.document;
global.location = dom.window.location;
global.MouseEvent = dom.window.MouseEvent;
global.KeyboardEvent = dom.window.KeyboardEvent;
global.InputEvent = dom.window.InputEvent;
global.Event = dom.window.Event;

const fnSrc = extractFn(src, "click").replace(/^\s*click\(payload\)\s*\{/, "function click(payload) {");
const click = new Function(`${helpers}\n${fnSrc}\nreturn click;`)();

function hits(id) {
  const el = document.getElementById(id);
  const c = { click: 0 };
  el.addEventListener("click", () => c.click++);
  return c;
}

const out = [];
function check(name, ok, detail) { out.push({ name, ok, detail }); }

let h = hits("link"); click({ selector: "#link" });
check("a 元素只点一次", h.click === 1, `click=${h.click}`);
h = hits("btn"); click({ selector: "#btn" });
check("button 只点一次", h.click === 1, `click=${h.click}`);
h = hits("div"); click({ selector: "#div" });
check("role=button 只点一次", h.click === 1, `click=${h.click}`);
h = hits("btn"); click({ text: "按钮" });
check("按文字定位只点一次", h.click === 1, `click=${h.click}`);

console.log(JSON.stringify(out));
"""


def run(r) -> None:
    node = subprocess.run(["which", "node"], capture_output=True, text=True)
    if node.returncode != 0:
        r.warn("没有 node，跳过 DOM 检查")
        return

    # jsdom 是否可用
    probe = subprocess.run(
        ["node", "-e", "require('jsdom')"],
        cwd=str(JS_DIR), capture_output=True, text=True)
    if probe.returncode != 0:
        r.warn("未安装 jsdom，跳过 DOM 检查",
               f"cd {JS_DIR} && npm install jsdom")
        return

    # runner 必须放在 js/ 目录里，否则 Node 找不到同级的 node_modules。
    # ⚠️ 用**唯一文件名** —— 固定名字在并行执行时会互相删掉
    #    （一个进程在另一个启动 Node 之前把文件删了 → 随机失败）。
    #    以 _ 开头，已被 .gitignore 覆盖，不会误提交。
    import os as _os2
    runner = JS_DIR / f"_click_runner_{_os2.getpid()}.mjs"
    runner.write_text(RUNNER, encoding="utf-8")
    import os as _os
    env = {
        # ⚠️ 不要写死 PATH —— node 可能装在别的目录（nvm / homebrew / apk）。
        #    继承当前环境，找不到再补几个常见位置。
        "PATH": _os.environ.get("PATH", "") +
                ":/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "CONTENT_JS": str(PLUGIN_DIR / "browser-bridge" / "content.js"),
        "NODE_PATH": str(JS_DIR / "node_modules"),
    }
    try:
        p = subprocess.run(["node", str(runner)], cwd=str(JS_DIR),
                           capture_output=True, text=True, env=env, timeout=120)

        if p.returncode != 0:
            r.ok("D0 DOM 检查可运行", False, (p.stderr or "")[:200])
            return

        try:
            data = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            r.ok("D0 解析 DOM 检查输出", False, (p.stdout or "")[:200])
            return

        for item in data:
            r.ok(f"D {item['name']}", item["ok"], item.get("detail", ""))

        # ── 分块流式上传：真实 DOM 下逐字节校验 ──────────────────────
        #  ⚠️ 只做静态检查是不够的：这段链路的正确性取决于
        #     "base64 → Uint8Array → Blob → File → input.files"
        #     每一步的字节是否无损，以及分块顺序/上限是否真的生效。
        #     这里用 jsdom 真跑一遍，把塞进 input.files 的内容读回来比对。
        up = JS_DIR / "upload_stream.mjs"
        if up.is_file():
            env2 = dict(env)
            # ⚠️ 必须把当前插件的根目录传下去，否则脚本会去测**默认目录**，
            #    反向验证/多副本跑的时候等于没测。
            env2["KIRA_PLUGIN_DIR"] = str(PLUGIN_DIR)
            env2["UPLOAD_TARGET_MB"] = "1,5,20"
            try:
                p2 = subprocess.run(
                    ["node", str(up)], cwd=str(JS_DIR),
                    capture_output=True, text=True, env=env2, timeout=300)
                out2 = (p2.stdout or "")
                # ⚠️ 必须**先看退出码**：脚本异常退出却恰好打印了全部成功
                #    标记时（比如在最后一步崩掉前已经打完了），只看输出会
                #    误判为通过。非零退出码一律先记失败。
                if p2.returncode != 0:
                    r.ok("U0 分块上传 DOM 测试脚本正常退出", False,
                         f"exit={p2.returncode}；"
                         f"{(p2.stderr or '')[:160] or out2[-160:]}")
                    for nm in ("U1 分块上传在真实 DOM 下逐字节一致",
                               "U2 乱序分块被拒绝",
                               "U3 上限为 0 时不误拒"):
                        r.ok(nm, False, "脚本未正常退出，结果不可信")
                    return
                checks = [
                    ("U1 分块上传在真实 DOM 下逐字节一致",
                     "内容 ✓ 逐字节一致" in out2 and out2.count("内容 ✓") >= 3,
                     [ln.strip() for ln in out2.splitlines()
                      if "逐字节一致" in ln or "不一致" in ln][:3]),
                    ("U2 乱序分块被拒绝",
                     "乱序分块是否被拒: ✓ 已拒绝" in out2, ""),
                    ("U3 上限为 0 时不误拒",
                     "未误拒" in out2, ""),
                ]
                for name, ok2, det in checks:
                    r.ok(name, ok2, str(det)[:160] if not ok2 else
                         "base64→Uint8Array→Blob→File 全链路字节无损")
            except subprocess.TimeoutExpired:
                r.ok("U0 分块上传 DOM 测试", False, "超时")

        # ── 上传会话的回收行为 ──────────────────────────────────────
        sweep = JS_DIR / "upload_sweep.mjs"
        if sweep.is_file():
            try:
                _e3 = dict(env)
                _e3["KIRA_PLUGIN_DIR"] = str(PLUGIN_DIR)
                p3 = subprocess.run(["node", str(sweep)], cwd=str(JS_DIR),
                                    capture_output=True, text=True,
                                    env=_e3, timeout=120)
                _d3 = json.loads((p3.stdout or "[]").strip().splitlines()[-1]
                                 if p3.stdout.strip() else "[]")
                for _it in _d3:
                    r.ok(f"U4 {_it['name']}", bool(_it.get("ok")),
                         _it.get("detail", "")[:150])
            except Exception as e:
                r.ok("U4 上传会话回收行为", False, f"{type(e).__name__}: {e}"[:140])
    finally:
        # 无论成功失败都要清掉临时 runner，否则会污染下一次的文件清点
        try:
            runner.unlink()
        except OSError:
            pass
