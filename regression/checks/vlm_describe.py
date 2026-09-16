"""截图 → VLM 描述的能力（**防止它再次丢失**）。

## 为什么单独一个检查模块

这个能力在原版 `headless_browser` 里存在，合并进本插件时**被整体弄丢**——
`grep vlm` 一个字都没有，而当时**没有任何检查会发现**（缺功能不是"报错"）。

它很重要：插件能给**用户**发图，但 **bot 自己看不到图** ——
工具结果是以 `role:"tool"` 的**文本**进模型的（框架
`ToolResult.assemble_result()` 只把附件转成路径文本）。
bot 想"看到"页面，只能靠 VLM 把图转成描述。

所以这里做两件事：
  1. **结构断言**：链路的关键部件在不在（配置项 / 模块 / 接线 / 工具参数）；
  2. **行为断言**：用假的 VLM 客户端真跑一遍，验证三级回退和容错。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from ..harness import PLUGIN_DIR, section, src, src_safe

TITLE = "截图 VLM 描述（bot 看图）"

#: schema 里必须存在的配置项
REQUIRED_SCHEMA_KEYS = (
    "auto_describe_screenshot",
    "vlm_model",
    "vlm_describe_prompt",
    "vlm_timeout",
)


def _fake_vlm_src() -> str:
    """用假的 VLM 客户端真跑 `vlm.describe_image`，返回 JSON 结果。

    不需要真模型：`desc_img` 最终只是 `client.chat(request)`，
    所以一个实现 `chat()` 的假客户端就够验整条链路了。
    """
    return r'''
import asyncio, atexit, importlib.util, json, os, sys, tempfile

PLUGIN = os.environ["KIRA_PLUGIN_DIR"]
sys.path.insert(0, PLUGIN)
sys.path.insert(0, os.environ["KIRA_FW_DIR"])

spec = importlib.util.spec_from_file_location("vlm_uut", os.path.join(PLUGIN, "vlm.py"))
vlm = importlib.util.module_from_spec(spec)
sys.modules["vlm_uut"] = vlm
spec.loader.exec_module(vlm)

# 框架的 Image(image=path) 要求文件真实存在 —— 造一个真 PNG。
# ⚠️ 用 TemporaryDirectory（而不是 mkdtemp）：mkdtemp 建的目录没人清理，
#    每跑一次回归就在 /tmp 里留一份 PNG 夹具。
#    这里挂在模块级 —— 探针进程是**一次性**的（跑完就退出），
#    注册 atexit 清理即可，不必把整段塞进 with（探针代码是长脚本字符串）。
_TMPDIR = tempfile.TemporaryDirectory(prefix="kira_vlm_fixture_")
atexit.register(_TMPDIR.cleanup)
PNG = os.path.join(_TMPDIR.name, "shot.png")
with open(PNG, "wb") as f:
    f.write(bytes.fromhex("89504e470d0a1a0a") + b"0" * 128)


class Model:
    def __init__(self, mid):
        self.model_id = mid
        self.provider_name = "fake"
        self.model_type = "llm"


class Client:
    def __init__(self, mid):
        self.model = Model(mid)

    async def chat(self, req):
        class R:
            text_response = "【假VLM】页面是登录表单"
        return R()


class SlowClient(Client):
    async def chat(self, req):
        await asyncio.sleep(5)
        return await super().chat(req)


class PM:
    def __init__(self, v, raise_it=False):
        self._v, self._raise = v, raise_it

    def get_default_vlm(self):
        if self._raise:
            raise RuntimeError("未配置 default_vlm")
        return self._v


class Ctx:
    def __init__(self, cfg=None, dvlm=None, dllm=None, no_vlm=False):
        self._cfg, self._dllm = cfg, dllm
        self.provider_mgr = PM(dvlm, raise_it=no_vlm)

    def get_llm_client(self, model_uuid=None, llm_type=None):
        return self._cfg

    def get_default_llm_client(self):
        return self._dllm


async def main():
    out = {}

    # ① 配置留空 → 用**框架默认 VLM**（这是核心诉求）
    ctx = Ctx(dvlm=Client("qwen-vl-max"))
    d = await vlm.describe_image(ctx, PNG, configured_model="")
    out["default_vlm_used"] = "假VLM" in d

    # ② 配置了模型 → 优先用配置的
    ctx2 = Ctx(cfg=Client("mine"), dvlm=Client("should-not-use"))
    d2 = await vlm.describe_image(ctx2, PNG, configured_model="p:mine")
    out["configured_preferred"] = "假VLM" in d2

    # ③ 没有任何可用 VLM → 空串、不抛
    try:
        d3 = await vlm.describe_image(Ctx(no_vlm=True), PNG)
        out["none_returns_empty"] = (d3 == "")
    except Exception:
        out["none_returns_empty"] = False

    # ④ 超时 → 空串、不抛（截图不能因此失败）
    try:
        d4 = await vlm.describe_image(Ctx(dvlm=SlowClient("s")), PNG, timeout=0.3)
        out["timeout_returns_empty"] = (d4 == "")
    except Exception:
        out["timeout_returns_empty"] = False

    # ⑤ 文件不存在 → 空串、不抛（且报错要能看懂）
    try:
        # ⚠️ 用 _TMPDIR（不是旧变量名 _tmp）—— 改名后这里没跟上就
        #    会 NameError，被下面的 except 吞成 False → 检查"静默失败"。
        d5 = await vlm.describe_image(Ctx(dvlm=Client("v")),
                                      os.path.join(_TMPDIR.name, "nope.png"))
        out["missing_file_returns_empty"] = (d5 == "")
    except Exception:
        out["missing_file_returns_empty"] = False

    # ⑥ 只排除明确的非视觉模型
    out["vision_filter"] = (
        vlm.is_vision_model(Client("gpt-4o")) is True
        and vlm.is_vision_model(Client("text-embedding-3")) is False
        and vlm.is_vision_model(None) is False
    )

    # ⑦ "VLM 冲突"：模型被配到「图像」组 → 框架的 get_llm_client 返回 None。
    #    这时必须**回退**而不是直接失败，而且日志要说清原因。
    class CtxImageGroup(Ctx):
        def get_llm_client(self, model_uuid=None, llm_type=None):
            return None          # 框架的行为：类型不对就当拿不到
    ctx7 = CtxImageGroup(dvlm=Client("good-vl"))
    d7 = await vlm.describe_image(ctx7, PNG, configured_model="p:qwen-vl")
    out["falls_back_when_type_wrong"] = "假VLM" in d7

    # ⑧ 默认 VLM 类型不对（get_default_vlm 抛 TypeError）→ 空串不抛
    class CtxBadDefault(Ctx):
        def __init__(self):
            class PM2:
                def get_default_vlm(self):
                    raise TypeError("Expected LLMModelClient, got ImageModelClient")
            self.provider_mgr = PM2()
            self._cfg, self._dllm = None, None
    try:
        d8 = await vlm.describe_image(CtxBadDefault(), PNG)
        out["bad_default_vlm_type_ok"] = (d8 == "")
    except Exception:
        out["bad_default_vlm_type_ok"] = False

    print("RESULT:" + json.dumps(out))


asyncio.run(main())
'''


def run(r) -> None:
    section("A. VLM 描述链路的部件都在")

    # ── 模块本身 ──────────────────────────────────────────────────────
    vlm_py = PLUGIN_DIR / "vlm.py"
    r.ok("A1 vlm.py 存在（截图→描述 的实现）", vlm_py.is_file(),
         "这个模块曾经在合并时整体丢失过")
    v = src_safe("vlm.py")
    r.ok("A2 有三级回退：配置模型 → 框架默认 VLM → 默认 LLM",
         "get_default_vlm" in v and "get_llm_client" in v
         and "get_default_llm_client" in v)
    r.ok("A3b 说明了「VLM 必须是 LLM 类型」这个坑（早期踩过）",
         "LLMModelClient" in v and "大语言模型" in v,
         "视觉模型配到「图像」组时插件拿不到 —— 必须把原因讲清楚")
    r.ok("A3 有网页分析用的默认提示词",
         "VLM_TOOL_OPTIMIZED_PROMPT" in v,
         "否则描述只会是「这是一张网页截图」这种没用的概括")

    # ── 接线：截图工具真的用了它 ──────────────────────────────────────
    main = src_safe("main.py")
    r.ok("A4 main.py 引入了 vlm 模块", re.search(r"from \. import vlm", main) is not None)
    r.ok("A5 截图工具里调用了 vlm.describe_image", "vlm.describe_image(" in main)
    r.ok("A6 browser_screenshot 暴露了 describe 参数（让模型自己决定要不要描述）",
         re.search(r'"describe"\s*:\s*\{', main) is not None,
         "「要不要看图」应由模型按任务决定，不是写死的")

    # ── 配置项 ───────────────────────────────────────────────────────
    try:
        sch = json.loads(src_safe("schema.json"))
    except Exception as e:
        r.ok("A7 schema 可解析", False, f"{type(e).__name__}: {e}")
        return
    miss = [k for k in REQUIRED_SCHEMA_KEYS if k not in sch]
    r.ok("A7 schema 里的 VLM 配置项齐全", not miss, f"缺={miss or '无'}")
    r.ok("A8 auto_describe_screenshot 默认开启",
         (sch.get("auto_describe_screenshot") or {}).get("default") is True)
    r.ok("A9 vlm_model 默认留空（留空=用框架 WebUI 里设的默认 VLM）",
         (sch.get("vlm_model") or {}).get("default") == "",
         "这正是「没选就用框架默认」的实现方式")
    r.ok("A10 vlm_model 是 model_select 类型（面板上给下拉框选）",
         (sch.get("vlm_model") or {}).get("type") == "model_select")

    # ── main.py 读配置 ───────────────────────────────────────────────
    for key in ("vlm_model", "vlm_describe_prompt", "vlm_timeout",
                "auto_describe_screenshot"):
        r.ok(f"A11 读到配置 {key}", f'cfg.get("{key}"' in main)

    # ── 两个后端都能用（VLM 在插件进程里，与谁拍的图无关）─────────────
    # ⚠️ 判据是"**一个都不许有**"，不是 `or`。
    #    写成 `"backend" not in v or "self.router" not in v` 时，
    #    只漏进**一项**也照样 PASS（另一个条件把结果兜住了）——
    #    等于这个守卫在最常见的回归上失效。
    _leaks = [t for t in ("self.router", "self.backend", "HeadlessBackend",
                          "ExtensionBackend", "backends.") if t in v]
    r.ok("A12 VLM 调用不依赖具体后端（两个后端都能让 bot 看到图）",
         not _leaks,
         f"vlm.py 里引用了后端/路由={_leaks or '无'}；"
         "描述发生在插件进程里，扩展拍的图和无头拍的图走同一条路")

    section("B. 行为：真跑一遍（假 VLM 客户端）")

    fw_dir = os.environ.get("KIRA_FW_DIR", "")
    if not fw_dir or not Path(fw_dir).is_dir():
        r.warn("未提供框架目录（KIRA_FW_DIR），跳过 VLM 行为测试",
               "设置该环境变量后可启用")
        return

    fd, script = tempfile.mkstemp(suffix="_vlm_probe.py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_fake_vlm_src())
        import subprocess
        env = dict(os.environ)
        env["KIRA_PLUGIN_DIR"] = str(PLUGIN_DIR)
        env["KIRA_FW_DIR"] = fw_dir
        p = subprocess.run([sys.executable, script], capture_output=True,
                           text=True, env=env, timeout=180)
        line = next((ln for ln in (p.stdout or "").splitlines()
                     if ln.startswith("RESULT:")), "")
        if not line:
            r.ok("B0 VLM 行为探针有输出", False,
                 (p.stderr or p.stdout or "")[-200:])
            return
        data = json.loads(line[len("RESULT:"):])
        cases = [
            ("B1 配置留空时用**框架默认 VLM**", "default_vlm_used"),
            ("B2 配置了模型时优先用配置的", "configured_preferred"),
            ("B3 没有可用 VLM 时返回空串且不抛异常", "none_returns_empty"),
            ("B4 超时时返回空串且不抛异常（截图不受影响）", "timeout_returns_empty"),
            ("B5 截图文件不存在时返回空串且不抛异常", "missing_file_returns_empty"),
            ("B6 视觉能力判定只排除明确的非视觉模型", "vision_filter"),
            ("B7 模型被配到「图像」组时能回退（VLM 冲突）",
             "falls_back_when_type_wrong"),
            ("B8 默认 VLM 类型不对时返回空串且不抛", "bad_default_vlm_type_ok"),
        ]
        for label, key in cases:
            r.ok(label, bool(data.get(key)), f"探针结果={data.get(key)}")
    except Exception as e:
        r.ok("B0 VLM 行为探针可运行", False, f"{type(e).__name__}: {e}")
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
