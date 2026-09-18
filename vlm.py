"""截图 → VLM 描述。

**为什么单独一个模块**：这个能力曾经在被合并时整体丢失过（原版
`headless_browser` 有，合并后一点不剩）。独立成文件 + 配回归检查，
是为了它下次被改动时能立刻被发现，而不是悄悄消失。

## 定位

插件能截图给**用户**看，但 **bot 自己看不到图** ——
工具结果是以 `role: "tool"` 的**文本**进模型的（框架的
`ToolResult.assemble_result()` 只把附件转成路径文本）。
所以 bot 想"看到"页面，只能靠 VLM 把图转成文字描述。

## 关键：与后端无关

VLM 调用发生在**插件进程**里，跟截图是**扩展**拍的还是**无头**拍的
没有关系 —— 所以两个后端都能让 bot 看到图，不需要各写一套。

## 模型从哪来（三级回退）

1. 配置里的 `vlm_model`（用户在插件配置里显式选的）
2. **框架 WebUI 里设的默认 VLM**（`provider_mgr.get_default_vlm()`）
   —— 这是"用户没选"时的默认行为
3. 当前默认 LLM（如果它看起来支持视觉）

都拿不到就返回空描述，**不让截图本身失败**。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from core.logging_manager import get_logger

logger = get_logger("headless_browser.vlm", "blue")

#: 默认的"网页截图分析"提示词。
#  目标：让描述**可直接用于后续操作**（另一个 AI 看完就能点对地方），
#  而不只是"这是一张网页截图"这种没用的概括。
VLM_TOOL_OPTIMIZED_PROMPT = """你是一名专业的网页分析助手。请分析这张网页截图，提取对后续自动化操作有用的信息。

## 请按以下格式输出：

### 1. 页面基本信息
- 页面标题：
- 页面类型（搜索页/表单页/内容页/错误页等）：

### 2. 可交互元素清单（关键！）
- 搜索框：是否有？placeholder 文字是什么？
- 按钮：列出可见按钮的文字
- 表单字段：有哪些输入框、下拉菜单
- 重要的导航链接

### 3. 当前状态
- 页面是否已完全加载？
- 是否有加载中/转圈动画？
- 是否有错误提示、弹窗、警告、验证码？
- 是否需要登录？

### 4. 关键内容
- 页面的主要内容 / 搜索结果是什么？
- 是否有弹窗广告遮挡？

### 5. 建议的下一步操作
- 如果要点击某个内容：建议点哪个元素（尽量给出 CSS 选择器，如 #id、.class）
- 如果要填写表单：每个字段填什么
- 如果需要等待：等什么元素出现

请尽量详细，让另一个 AI 能根据你的描述直接完成操作。"""

#: 明确不具备视觉能力的模型特征 —— 用来排除掉误配。
#  ⚠️ 只排除**确定的**类型，不要靠关键词猜"支持不支持视觉"：
#     框架的 `desc_img` 本身不做视觉校验，模型到底行不行由实际调用决定。
#     硬编码关键词很容易误杀真正支持视觉的模型。
_NON_VISION_MARKERS = (
    "embedding", "rerank", "tts", "stt", "davinci", "babbage", "whisper",
)


def is_vision_model(model_client) -> bool:
    """这个模型"看起来能用于视觉任务"吗（宽松判断，只排除明确不行的）。"""
    if not model_client or not getattr(model_client, "model", None):
        return False
    model_id = str(getattr(model_client.model, "model_id", "")).lower()
    return not any(m in model_id for m in _NON_VISION_MARKERS)


async def get_vlm_client(ctx, configured: str = "") -> Optional[object]:
    """按三级回退取一个 VLM 客户端；取不到返回 None。

    Args:
        ctx: 插件上下文（`self.ctx`）
        configured: 配置里的 `vlm_model`（形如 ``provider_id:model_id``），
            为空表示用户没选 —— 这时用框架 WebUI 里设的默认 VLM。

    ⚠️ **关于"VLM 冲突"**（早期版本踩过的坑，别再踩回去）：

    框架侧要求用于描述的模型必须是 **`LLMModelClient`** —— 即"放在
    **大语言模型**组里的视觉模型"，**不是图像组**。
    把 Qwen-VL 这类模型配到「图像」组里，插件这边是拿不到它的。

    框架的 `get_llm_client()` 与 `get_default_vlm()` 内部都会做
    `isinstance(..., LLMModelClient)` 检查（不是就当拿不到），
    所以**不会把错类型的客户端传进来**；但代价是**静默返回 None** ——
    用户只知道"没描述"，不知道为什么。

    所以这里必须把原因**说清楚**：区分"压根没配"和"配了但类型不对"，
    并在日志里直接给出怎么改。
    """
    tried = []

    # ── 1) 用户在插件配置里显式指定的模型 ────────────────────────────
    if configured:
        try:
            client = ctx.get_llm_client(model_uuid=configured)
            if client and is_vision_model(client):
                return client
            if client:
                logger.warning(
                    f"配置的 VLM 模型 {configured} 看起来不支持视觉"
                    f"（模型名像 embedding/tts 之类），改用框架默认 VLM")
            else:
                # 这是"VLM 冲突"的典型表现：模型存在，但不在这条路上。
                tried.append(
                    f"配置的模型 {configured} 取不到 —— **它很可能被配到了"
                    f"「图像」模型组**。本功能需要的是 **LLM 类型**的模型："
                    f"请在「提供商」设置里把这个视觉模型加到"
                    f"**大语言模型**组，再回这里选。")
        except Exception as e:
            tried.append(f"取配置的 VLM 模型失败（{configured}）：{e}")

    # ── 2) 框架 WebUI 里配的默认 VLM（"没选"时的默认行为）────────────
    try:
        client = ctx.provider_mgr.get_default_vlm()
        if client and is_vision_model(client):
            logger.info(
                f"使用框架默认 VLM：{getattr(client.model, 'model_id', '?')}")
            return client
        if client:
            tried.append("框架默认 VLM 看起来不支持视觉")
    except TypeError as e:
        # get_default_vlm 自己会因类型不对抛 TypeError
        tried.append(
            f"框架默认 VLM 的类型不对（{e}）—— 默认 VLM 必须是"
            f"**大语言模型**组里的模型")
    except Exception as e:
        # 用户可能压根没配 default_vlm —— 常见情况，不该刷 ERROR
        logger.info(f"取框架默认 VLM 失败（可能未配置）：{e}")

    # ── 3) 当前默认 LLM（如果它支持视觉）────────────────────────────
    try:
        client = ctx.get_default_llm_client()
        if client and is_vision_model(client):
            logger.info(
                f"使用默认 LLM（支持视觉）：{getattr(client.model, 'model_id', '?')}")
            return client
    except Exception as e:
        tried.append(f"取默认 LLM 失败：{e}")

    if tried:
        logger.warning("没有可用的 VLM 模型：" + "；".join(tried))
    return None


async def describe_image(ctx, image_path: str, *,
                         configured_model: str = "",
                         prompt: str = "",
                         timeout: float = 30.0) -> str:
    """用 VLM 描述一张图；失败/超时返回空串（**不抛异常**）。

    调用方（截图工具）据此决定是否把描述附给 bot —— 描述拿不到
    不应该让截图本身变成失败。
    """
    client = await get_vlm_client(ctx, configured_model)
    if client is None:
        logger.info("没有可用的 VLM 模型，跳过截图描述"
                    "（可在插件配置里选一个，或在 KiraAI 的模型设置里指定默认 VLM）")
        return ""

    # ⚠️ 框架的 `Image(image=path)` 要求**文件真实存在**（否则抛
    #    `Unknown file type`，那个报错完全看不出是"路径不对"）。
    #    先自己确认一次，给出能看懂的原因。
    import os as _os
    if not _os.path.isfile(image_path):
        logger.warning(f"截图文件不存在，跳过描述：{image_path}")
        return ""

    try:
        from core.chat.message_elements import Image as _Image
        from core.utils.common_utils import desc_img
    except Exception as e:
        logger.warning(f"框架的 desc_img 不可用，跳过描述：{e}")
        return ""

    try:
        desc = await asyncio.wait_for(
            desc_img(client=client,
                     image=_Image(image=image_path),
                     prompt=prompt or VLM_TOOL_OPTIMIZED_PROMPT),
            timeout=float(timeout or 30.0),
        )
        return (desc or "").strip()
    except asyncio.TimeoutError:
        logger.warning(f"VLM 描述超时（{timeout}s），跳过")
        return ""
    except Exception as e:
        logger.warning(f"VLM 描述失败：{type(e).__name__}: {e}")
        return ""
