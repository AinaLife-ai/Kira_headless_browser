#!/usr/bin/env python3
"""扩展版本戳：**改了扩展代码就必须升版本**，否则用户永远收到不更新。

## 为什么需要它（真事故）

`browser-bridge/` 是**随插件打包**的扩展。插件升级时用户浏览器里那份
**不会**自动更新 —— 面板会比对"你装的版本"和"插件自带的版本"来提示更新。

于是有个隐蔽的坑：**改了扩展代码却忘了改 `browser-bridge/manifest.json`
里的 version** ⇒ 两边版本号一样 ⇒ 面板判定"已是最新"、一个字都不提 ⇒
用户永远停在旧扩展上（新加的能力他一个也吃不到）。

2026-09-22 就这么翻过一次车：插件侧加了"空闲先探测再判死"、扩展侧加了
"半开连接自愈"，扩展代码改了四个文件，版本号却还是 1.5.0 ——
用户看到的是 `v1.5.0 ✓`，什么提示都没有。

## 它怎么防

`.version-stamp.json` 里记着「上次升版本时，扩展目录的**内容指纹** + 当时的版本号」。
检查脚本每次重算指纹：

* 指纹变了、版本号没变 ⇒ **报红**（就是上面那个坑）
* 版本号变了、指纹没更新 ⇒ **报红**（说明忘了跑这个脚本，戳没跟上）

改完扩展代码后：

    # 1. 手动把 browser-bridge/manifest.json 里的 version 往上加
    # 2. 刷新版本戳（本脚本，不带参数 = 写入）
    python3 regression/ext_version_stamp.py

用法：
    python3 regression/ext_version_stamp.py          # 按当前内容刷新戳
    python3 regression/ext_version_stamp.py --check  # 只检查（退出码 1 = 不一致）
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
EXT_DIR = PLUGIN_DIR / "browser-bridge"
STAMP = EXT_DIR / ".version-stamp.json"


def extension_code_hash(ext_dir: Path = EXT_DIR) -> str:
    """扩展目录的**内容指纹**。

    ⚠️ 覆盖目录里**所有**文件（含 manifest、icons）—— 只要是随扩展发布的东西，
       改了就该让用户能感知到"我这份和插件自带的不一样"。
       排序后再算，保证跨平台一致；只跳过戳文件自己。
    """
    h = hashlib.sha256()
    files = sorted(p for p in ext_dir.rglob("*")
                   if p.is_file() and p.name != STAMP.name
                   and "__pycache__" not in p.parts)
    for p in files:
        rel = p.relative_to(ext_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def read_manifest_version(ext_dir: Path = EXT_DIR) -> str:
    return str(json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
               .get("version", "")).strip()


def load_stamp(path: Path = STAMP) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def check(ext_dir: Path = EXT_DIR, stamp_path: Path = STAMP) -> list:
    """返回问题列表（空 = 一致）。"""
    problems = []
    stamp = load_stamp(stamp_path)
    if not stamp:
        problems.append(f"版本戳不存在或读不了：{stamp_path.name}")
        return problems
    version = read_manifest_version(ext_dir)
    cur = extension_code_hash(ext_dir)
    if stamp.get("version") != version:
        problems.append(
            f"版本戳里的版本（{stamp.get('version')}）与 manifest 的（{version}）不一致"
            f" —— 升完版本要跑一次 regression/ext_version_stamp.py")
    if stamp.get("code_sha256") != cur:
        problems.append(
            f"扩展代码变了但版本号还是 {version}（戳={str(stamp.get('code_sha256'))[:12]}…、"
            f"现在={cur[:12]}…）—— 必须把 browser-bridge/manifest.json 的 version "
            f"往上升一级，再跑 regression/ext_version_stamp.py 刷新戳；"
            f"否则用户浏览器里那份永远不会被提示更新")
    return problems


def write_stamp(ext_dir: Path = EXT_DIR, stamp_path: Path = STAMP) -> dict:
    data = {"version": read_manifest_version(ext_dir),
            "code_sha256": extension_code_hash(ext_dir)}
    stamp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return data


def main() -> int:
    if "--check" in sys.argv:
        problems = check()
        for p in problems:
            print("✗", p)
        print("✓ 版本戳一致" if not problems else f"共 {len(problems)} 处不一致")
        return 1 if problems else 0
    before = load_stamp()
    data = write_stamp()
    print(f"版本戳已更新：version={data['version']} "
          f"code_sha256={data['code_sha256'][:12]}…")
    if before and before.get("version") != data["version"]:
        print(f"（版本号从 {before.get('version')} → {data['version']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
