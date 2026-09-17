"""统一结果类型 —— 两个后端（扩展桥 / 无头浏览器）都回这个。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class TabInfo:
    id: Any = None
    title: str = ""
    url: str = ""
    active: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title,
                "url": self.url, "active": self.active}


@dataclass
class OpResult:
    """一次操作的结果。

    ``ok=False`` 时 ``error`` 是给模型看的话（不是异常栈）。
    ``backend`` 记录这次是谁干的，方便排查"为什么行为不一样"。

    ``declined`` 表示**用户明确拒绝了**这次操作。它必须和"执行失败"
    区分开：失败可以换后端重试，被拒绝**绝不能**重试 ——
    否则用户点了「拒绝」，插件却偷偷换条路把同一件事做了。
    """

    ok: bool = True
    data: Any = None
    error: Optional[str] = None
    backend: str = ""
    warnings: List[str] = field(default_factory=list)
    declined: bool = False

    #: **不确定状态**：命令可能已经在浏览器里执行了，只是我们没等到回执
    #  （典型：页面操作超时）。这种结果**绝不能换后端重试** ——
    #  否则同一个点击/输入会被做两次。和 ``declined`` 一样属于"终止性"结果。
    indeterminate: bool = False

    @classmethod
    def fail(cls, error: str, backend: str = "") -> "OpResult":
        return cls(ok=False, error=error, backend=backend)

    @classmethod
    def declined_by_user(cls, backend: str = "",
                         reason: str = "用户在浏览器中拒绝了本次操作") -> "OpResult":
        return cls(ok=False, error=reason, backend=backend, declined=True)

    @classmethod
    def indeterminate_result(cls, error: str, backend: str = "") -> "OpResult":
        """命令**可能已经执行**但没等到回执（如页面操作超时）。

        调用方据此**禁止**换后端重试 —— 重复执行的伤害比失败更大。
        """
        return cls(ok=False, error=error, backend=backend, indeterminate=True)

    def as_text(self, text: str) -> str:
        return text
