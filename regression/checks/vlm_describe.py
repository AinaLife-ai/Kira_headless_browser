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

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from ..harness import PLUGIN_DIR, section, src_safe

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
# ⚠️ 要一张**真的、够大的** PNG：压缩那一步走的是框架的真
#    `compress_image_element`（PIL 打开 → 缩放 → 重编码），
#    喂它一个假的 PNG（只有魔数 + 一堆 0）会**打不开**，
#    于是"压缩"这条路径根本没被验到（看着绿，其实什么都没测）。
try:
    from PIL import Image as _PILImage
    import random as _rnd
    _rnd.seed(7)
    _w, _h = 1800, 1200
    _im = _PILImage.new("RGB", (_w, _h))
    _im.putdata([(_rnd.randrange(256), _rnd.randrange(256), _rnd.randrange(256))
                 for _ in range(_w * _h)])
    _im.save(PNG, "PNG")
except Exception:
    with open(PNG, "wb") as f:
        f.write(bytes.fromhex("89504e470d0a1a0a") + b"0" * 128)

RAW_B64_LEN = 0
try:
    import base64 as _b64
    with open(PNG, "rb") as f:
        RAW_B64_LEN = len(_b64.b64encode(f.read()))
except Exception:
    pass

#: 假客户端收到的 data URL 长度 —— 压缩有没有真的生效就看它比 RAW_B64_LEN 小多少
DATA_URL_LEN = 0


class Model:
    def __init__(self, mid):
        self.model_id = mid
        self.provider_name = "fake"
        self.model_type = "llm"


CHAT_CALLS = 0


class Client:
    def __init__(self, mid):
        self.model = Model(mid)

    async def chat(self, req):
        # ⚠️ 返回值要**带上自己的 model_id** —— 两个假客户端都返回同一句
        #    固定文本的话，"到底用了哪个模型"就区分不出来：
        #    即使代码错误地走了 fallback，断言也照样通过（假绿）。
        #    带上标识后，才能断言"用的就是我配的那个"。
        class R:
            text_response = "【假VLM:" + self.model.model_id + "】页面是登录表单"
        # ⚠️ 记一笔"假客户端真的被调用了" —— desc_img 最终就是调 client.chat()。
        #    这是**链路真跑通**的证据：如果 core.utils.common_utils 导不到
        #    （缺桩/框架不在），describe_image 会静默走 except 返回空串，
        #    那条路径下这个计数器是 0。
        global CHAT_CALLS, DATA_URL_LEN
        CHAT_CALLS += 1
        # 顺手量一下**真正发给模型的那串 data URL 有多长** ——
        # 压缩有没有生效，全看它。不压的话它 ≈ 原图的 base64 长度。
        try:
            _url = req.messages[0]["content"][0]["image_url"]["url"]
            DATA_URL_LEN = len(_url)
        except Exception:
            pass
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
    out["default_vlm_used"] = "假VLM:qwen-vl-max" in d

    # ② 配置了模型 → 优先用配置的
    #    ⚠️ 必须断言**用的是 "mine"**，而不是泛泛地断言"有描述" ——
    #       后者在"错误地走了 fallback（should-not-use）"时同样成立。
    ctx2 = Ctx(cfg=Client("mine"), dvlm=Client("should-not-use"))
    d2 = await vlm.describe_image(ctx2, PNG, configured_model="p:mine")
    out["configured_preferred"] = ("假VLM:mine" in d2
                                   and "should-not-use" not in d2)

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
    # 探针自检：至少有一次描述是**真的走了框架链路**（假 client 被调用）。
    # 全 0 的话说明每次都静默走了 except 分支 —— 那些断言就是空转。
    out["desc_img_path_exercised"] = CHAT_CALLS > 0
    out["raw_b64_len"] = RAW_B64_LEN
    out["data_url_len"] = DATA_URL_LEN

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
        if not isinstance(sch, dict):
            raise ValueError(f"schema 顶层不是对象（{type(sch).__name__}）")
    except Exception as e:
        r.ok("A7 schema 可解析", False, f"{type(e).__name__}: {e}")
        # ⚠️ **不要 return** —— 下面 A7~A12 与 B 段（真跑探针）与
        #    "schema 能否解析"**互相独立**，早退会让它们的结果完全看不见
        #    （报告上只剩一条 A7 失败）。赋空对象继续走，各自报各自的结果。
        sch = {}
    miss = [k for k in REQUIRED_SCHEMA_KEYS if k not in sch]
    r.ok("A7 schema 里的 VLM 配置项齐全", not miss, f"缺={miss or '无'}")
    r.ok("A8 auto_describe_screenshot 默认开启",
         (sch.get("auto_describe_screenshot") or {}).get("default") is True)
    r.ok("A9 vlm_model 默认留空（留空=用框架 WebUI 里设的默认 VLM）",
         (sch.get("vlm_model") or {}).get("default") == "",
         "这正是「没选就用框架默认」的实现方式")
    r.ok("A10 vlm_model 是 model_select 类型（面板上给下拉框选）",
         (sch.get("vlm_model") or {}).get("type") == "model_select")

    # ── A13 超时默认 30 秒，且 schema 与代码**不能各说各话** ──────────
    #    ⚠️ 原来默认 10 秒，对视觉模型偏紧：一张大截图的编码+推理经常就要
    #       十几秒，超时后描述被丢掉，用户看到的是"没拿到描述"。
    #    判据两处都要看 —— 只查 schema 的话，代码里的回落值偷偷留着 10
    #    也照样绿（用户没在面板上存过配置时走的就是代码回落）。
    _sch_to = (sch.get("vlm_timeout") or {}).get("default")
    _m_to = re.search(r'cfg\.get\(\s*["\']vlm_timeout["\']\s*,\s*(\d+)', main)
    r.ok("A13a schema 里 vlm_timeout 默认 60 秒", _sch_to == 60,
         f"实际={_sch_to!r}")
    r.ok("A13b 代码回落值也是 60，且与 schema 一致",
         bool(_m_to) and _m_to.group(1) == "60",
         f"实际={_m_to.group(1) if _m_to else '没找到 cfg.get(...)'}")
    r.ok("A13c describe_image 的 timeout 形参默认也是 60",
         "timeout: float = 60.0" in v,
         "调用方不传 timeout 时用的就是它")

    # ── A15 发图给 VLM 前**必须压缩**（这是"VLM 老是超时"的根因）──────
    #    ⚠️ `desc_img` 内部走 `image.to_data_url()`（**原样 base64**，
    #       不缩放不重编码）再配它写死的 `detail: "high"` —— 1920×1080 的
    #       截图就是好几 MB，又慢又贵还容易超时。框架自己在 message_manager
    #       发消息前会压，但直接调 desc_img **绕过了那一步**；
    #       而框架默认 `image_compression.enabled = False`，
    #       所以插件这边必须自己默认压。
    _bad15 = []
    if "compress_image_element" not in v:
        _bad15.append("vlm.py 里没有压缩调用")
    _m_c = re.search(r'cfg\.get\(\s*["\']vlm_compress["\']\s*,\s*(True|False)', main)
    if not _m_c or _m_c.group(1) != "True":
        _bad15.append("vlm_compress 默认不是开")
    if '"vlm_compress"' not in src_safe("schema.json"):
        _bad15.append("schema 里没有 vlm_compress")
    r.ok("A15 发图给 VLM 前会先压缩（默认开）", not _bad15,
         f"问题={_bad15 or '无'}")

    # ── A17 「只截图、不描述」这条路要走得通，而且要说得清什么时候用 ──
    #    用户问的是："bot 能不能选择截图发出去但不做 VLM 描述？"
    #    功能一直有（`describe=false`），但原来那句说明只写了
    #    "默认取插件配置（通常 true）" —— **bot 看不出什么时候该传 false**，
    #    等于这个开关对它不存在。
    _bad17 = []
    if "describe" not in main:
        _bad17.append("截图工具没有 describe 参数")
    if "browser_page" not in main:
        _bad17.append("说明里没告诉它'文字多时用 snapshot 更快'")
    if "看不到画面本身" not in main:
        _bad17.append("没说清关掉之后会失去什么（不可逆的代价）")
    r.ok("A17 「只截图不描述」可用，且说明写清了何时该关", not _bad17,
         f"问题={_bad17 or '无'}")

    # ── A14 失败文案必须**短**（不要一段排查说明挤进每次返回）────────
    #    ⚠️ 原来失败时返回六行"常见原因…图片本身已保存…"，既占 token
    #       又干扰模型。细节都在 browser_diag(action="vlm") 里，要查的人
    #       自然会去查 —— 内联只需要说清"这次没拿到"。
    #    ⚠️ 只查**截图工具里那段**，不要全文查 —— "常见原因" 这个词
    #       在 `_diag_vlm`（browser_diag 的详细诊断）里**本来就该有**，
    #       全文查会把它一起算进来（首跑就是这么误报的）。
    _seg = ""
    _i = main.find("if desc:")
    if _i >= 0:
        _j = main.find('return "\n".join(parts)', _i)
        _seg = main[_i:_j if _j > 0 else _i + 600]
    _long = []
    if not _seg:
        _long.append("找不到截图工具里 if desc: 那段")
    else:
        if "常见原因" in _seg or "未能生成图片描述" in _seg:
            _long.append("失败分支还在展开排查说明")
        if "这次没拿到图片描述" not in _seg:
            _long.append("失败分支没换成短文案")
        if "browser_diag" not in _seg:
            _long.append("没把细节指向 browser_diag")
    r.ok("A14 没拿到描述时的内联文案简短（细节指向 browser_diag）",
         not _long, f"问题={_long or '无'}")

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
        # ⚠️ **先看退出码**：脚本可能已经打印了 RESULT，但在**收尾阶段**
        #    （atexit 的临时目录清理、解释器关停）出错而非零退出。
        #    那种情况下结果不可信 —— 只看输出会把"跑挂了"当成"跑通了"。
        if p.returncode != 0:
            r.ok("B0 VLM 行为探针正常退出", False,
                 f"exit={p.returncode}；{(p.stderr or '')[-220:]}")
            return
        line = next((ln for ln in (p.stdout or "").splitlines()
                     if ln.startswith("RESULT:")), "")
        if not line:
            r.ok("B0 VLM 行为探针有输出", False,
                 (p.stderr or p.stdout or "")[-200:])
            return
        data = json.loads(line[len("RESULT:"):])
        cases = [
            # ⚠️ 先验"链路真的跑到了框架的 desc_img" —— 否则下面的断言
            #    可能全是在"静默返回空串"的路径上成立的（空转）。
            ("B0b 描述链路真的走到了框架 desc_img（不是走了 except 空转）",
             "desc_img_path_exercised"),
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

        # ── A16 行为验证：真正发给模型的 data URL **确实变小了** ────────
        #    ⚠️ 只查"代码里有没有 compress_image_element"是不够的 ——
        #       夹具是假 PNG 时 PIL 打不开，压缩会静默返回 False，
        #       那条路径根本没被验到（看着绿）。所以夹具换成**真的
        #       1800×1200 PNG**，再量真正发出去的那串 data URL。
        _raw = data.get("raw_b64_len") or 0
        _sent = data.get("data_url_len") or 0
        r.ok("A16 实测：发给 VLM 的图确实被压小了（data URL 明显短于原图）",
             bool(_raw) and bool(_sent) and _sent < _raw * 0.5,
             f"原图 base64={_raw} 实发={_sent}（应小于一半）")
    except Exception as e:
        r.ok("B0 VLM 行为探针可运行", False, f"{type(e).__name__}: {e}")
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
