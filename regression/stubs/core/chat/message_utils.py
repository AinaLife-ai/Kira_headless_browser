class Session:
    """框架 `Session` 的最小替身。

    ⚠️ 这里**必须是对象**（带 `sid` 属性与 `__str__`），不能图省事写成字符串 ——
    框架真实类型 `core.chat.session.Session` 就是对象，而插件里
    `_sid_of()` 正是按"对象 → 优先取 `.sid`，否则退回 `str(session)`"来处理。
    如果 stub 给的是字符串，那条分支**永远走不到**：
    测试会绿，但测的是另一条路径（假保真度）。
    """

    def __init__(self, adapter_name="qq", session_type="dm", session_id="1"):
        self.adapter_name = adapter_name
        self.session_type = session_type
        self.session_id = session_id

    @property
    def sid(self):
        return f"{self.adapter_name}:{self.session_type}:{self.session_id}"

    def __str__(self):
        return f"{self.adapter_name}:{self.session_type}:{self.session_id}"


class MessageChain:
    def __init__(self, elements=None):
        self.elements = elements or []


class KiraMessageBatchEvent:
    def __init__(self, session=None):
        # ⚠️ 默认给 **Session 对象**（与框架一致），不是字符串。
        #    显式传入的值原样保留（调用方要传字符串也能跑）。
        self.session = session if session is not None else Session()

    @property
    def sid(self):
        """转发给 `session.sid` —— **与框架一致**。

        ⚠️ 框架的 `KiraMessageBatchEvent` 真的有这个 property
        （`core/chat/message_utils.py`：`def sid: return self.session.sid`）。
        插件里 `_sid_of()` 的第一分支就是取 `event.sid` ——
        stub 少这个属性的话，那条分支**永远走不到**：
        测试全绿但测的是兜底路径（假保真度）。
        """
        return self.session.sid
