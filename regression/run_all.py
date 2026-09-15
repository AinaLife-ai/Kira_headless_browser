#!/usr/bin/env python3
"""插件回归测试入口。

用法::

    python3 regression/run_all.py                 # 跑全部
    python3 regression/run_all.py 安全             # 只跑名称含「安全」的
    python3 regression/run_all.py --list          # 看有哪些检查
    KIRA_PLUGIN_DIR=/path/to/copy python3 regression/run_all.py

设计约定：
  * 每个检查脚本只依赖 ``harness``，不互相依赖
  * 依赖缺失（websockets / jsdom）时**跳过并提示**，不算失败
  * 退出码：全过 0，有失败 1
"""

from __future__ import annotations

import sys

# ⚠️ 必须在**导入任何检查模块之前**关掉字节码写入。
#    security_rules 先于 file_hygiene 执行，它通过 load_module() 加载
#    security.py，那条路径会写出 PLUGIN_DIR/__pycache__/*.pyc；
#    而 file_hygiene 的 _all_files() 会把 __pycache__ 算进去 →
#    D1/D2 就会莫名其妙失败（时有时无，取决于是否有缓存残留）。
sys.dont_write_bytecode = True

import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from regression.harness import Report, install_stubs  # noqa: E402
from regression.checks import ALL_CHECKS  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--list" in sys.argv:
        print("可用的检查：")
        for m in ALL_CHECKS:
            print(f"  {m.TITLE:<34} ({m.__name__.split('.')[-1]})")
        return 0

    install_stubs()

    selected = []
    for m in ALL_CHECKS:
        if not args or any(a in m.TITLE for a in args):
            selected.append(m)
    if not selected:
        print(f"没有匹配的检查。可用：{[m.TITLE for m in ALL_CHECKS]}")
        return 1

    print("=" * 76)
    print("KiraAI 浏览器插件 · 回归测试")
    print(f"插件目录: {HERE.parent}")
    print(f"检查项  : {len(selected)} 组")
    print("=" * 76)

    reports: list[tuple[str, Report]] = []
    for m in selected:
        print(f"\n{'─' * 76}\n▶ {m.TITLE}\n{'─' * 76}")
        rep = Report(m.TITLE)
        try:
            m.run(rep)
        except Exception as e:
            rep.ok(f"{m.TITLE} 未抛异常", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
        reports.append((m.TITLE, rep))

    print("\n" + "=" * 76)
    total_p = sum(len(rep.passed) for _, rep in reports)
    total_f = sum(rep.failed_count for _, rep in reports)
    total_w = sum(len(rep.warned) for _, rep in reports)

    for title, rep in reports:
        mark = "✅" if rep.failed_count == 0 else "❌"
        print(f"  {mark} {title:<36} {rep.summary()}")

    print("-" * 76)
    print(f"合计 PASS {total_p} / FAIL {total_f}" +
          (f" / WARN {total_w}" if total_w else ""))

    if total_f:
        print("\n失败项：")
        for title, rep in reports:
            for f in rep.failed:
                print(f"  - [{title}] {f}")
    if total_w:
        print("\n提示（多为环境缺依赖，不算失败）：")
        for title, rep in reports:
            for w in rep.warned:
                print(f"  - [{title}] {w}")
    print("=" * 76)

    return 1 if total_f else 0


if __name__ == "__main__":
    sys.exit(main())
