"""统一结果类型 —— 两个后端（扩展桥 / 无头浏览器）都回这个。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    """

    ok: bool = True
    data: Any = None
    error: Optional[str] = None
    backend: str = ""
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def fail(cls, error: str, backend: str = "") -> "OpResult":
        return cls(ok=False, error=error, backend=backend)

    def as_text(self, text: str) -> str:
        return text
