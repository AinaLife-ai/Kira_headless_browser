#!/usr/bin/env python3
""""逐个删文件跑全套"矩阵 —— 自动**从读取器推导**要删的文件。

⚠️ 为什么要自动推导：第一版是**手写名单**，我列了 `manifest.json`
   —— 但那是插件根的 manifest，而 `ext_manifest()` 读的是
   `browser-bridge/manifest.json`。名字像，路径不同，于是"删了 manifest
   也没崩"的假绿骗过了我（CR 后来正是点在这一处）。
   判据要盯"**读取器真正读的路径**"，不是"名字看起来像的文件"。

对每个候选文件：复制整棵树 → 删掉它 → 跑全套 → 记录
  · 是否出现 Python traceback（= 某个检查段崩了，报告会缺一大块）
  · 合计 PASS/FAIL

用法: python3 regression/delete_matrix.py [并发数]\n\n放在仓库里的原因：它是**可复用的自查工具**，不是一次性的。\n下一轮改动后直接重跑，就能知道"新引入的读取有没有兜住"。
"""
import concurrent.futures as cf
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SRC = str(pathlib.Path(__file__).resolve().parent.parent)
FW = os.environ.get("KIRA_FW_DIR", "/tmp/kiraai_latest")


def candidates():
    """扫 harness + 检查模块里的读取字面量，返回去重后的相对路径。"""
    root = pathlib.Path(SRC)
    files = set()
    targets = [root / "regression" / "harness.py"]
    targets += sorted((root / "regression" / "checks").glob("*.py"))
    for f in targets:
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(
                r'(?:src_safe|src|load_json_safe|load_json|ext_file)'
                r'\(\s*"([^"]+)"', t):
            files.add(m.group(1))
        for m in re.finditer(r'EXT_DIR\s*/\s*"([^"]+)"', t):
            files.add("browser-bridge/" + m.group(1))
    return sorted(x for x in files if (root / x).exists())


def run_one(rel):
    d = tempfile.mkdtemp(prefix="dm_")
    work = pathlib.Path(d) / "t"
    try:
        shutil.copytree(SRC, work,
                        ignore=shutil.ignore_patterns("__pycache__", ".git"))
        victim = work / rel
        if victim.is_dir():
            shutil.rmtree(victim)
        else:
            victim.unlink()
        # 清掉可能干扰的残留
        (work / "data" / "log.log").unlink(missing_ok=True)
        for p in work.rglob("__pycache__"):
            shutil.rmtree(p, ignore_errors=True)
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "KIRA_FW_DIR": FW,
               "KIRA_PLUGIN_DIR": str(work)}
        r = subprocess.run([sys.executable, "regression/run_all.py"],
                           cwd=str(work), capture_output=True, text=True,
                           env=env, timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        # ⚠️ 两个判据**缺一不可**：
        #    ① "未抛异常" = harness 自己的"某段中断"标记。
        #       （全文搜 "Traceback" 不行 —— 探针**子进程**的报错文本会以
        #        "原因"的形式正常展示，那不是崩溃。）
        #    ② **连"合计"都没有** = 套件根本没跑完（导入就炸 / 被删的就是
        #       运行器自己）。只看 ① 的话这种会算成 ✓，打印出
        #       "PASS ? / FAIL ?" —— 一个**假绿**：什么都没验，却看着通过了。
        tot = re.search(r"合计 PASS (\d+) / FAIL (\d+)", out)
        if tot is None:
            kind = "未跑完"        # 套件没产出汇总 —— 连"验了多少条"都不知道
        elif "未抛异常" in out:
            kind = "整段中断"      # 有汇总，但某段抛异常、后面的检查没跑
        else:
            kind = ""
        return (rel, kind, tot.group(1) if tot else "?",
                tot.group(2) if tot else "?", out)
    except subprocess.TimeoutExpired:
        return (rel, True, "?", "?", "TIMEOUT")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    jobs = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    cands = candidates()
    print(f"候选 {len(cands)} 个（自动从读取器推导）\n")
    bad = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for rel, kind, p, f, out in ex.map(run_one, cands):
            mark = f"✗ {kind}" if kind else "✓"
            print(f"  {mark:10} 删 {rel:44} PASS {p} / FAIL {f}", flush=True)
            if kind:
                bad.append((rel, kind, out))
    print()
    if not bad:
        print("✅ 全部 %d 个：0 处崩溃（各段照跑、各自报错）" % len(cands))
    else:
        print("❌ %d 处有问题：" % len(bad))
        for rel, kind, out in bad:
            print(f"\n──── {rel}  [{kind}] ────")
            for ln in out.splitlines():
                if "Traceback" in ln or "Error" in ln or "未抛异常" in ln \
                        or ".py\"" in ln:
                    print("   " + ln.strip()[:140])
            if kind == "未跑完":
                print("   （套件没打印汇总 —— 要么导入就炸，要么删掉的就是"
                      "运行器自己；两种情况都不算「通过」）")


if __name__ == "__main__":
    main()
