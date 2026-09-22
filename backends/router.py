"""后端注册与自动选择。

**这是解决"真实浏览器不能共用"的核心。**

原来的无头插件是「只有一条路」：Playwright 自己启动浏览器，就必须独占
user-data-dir，于是和用户开着的浏览器抢锁 —— 用户开着插件就用不了，
插件开着用户就打不开浏览器。

现在改成**两条路，按可用性自动挑**：

    扩展桥（用户自己那个浏览器，零冲突）
        ↓ 扩展没连上 / 操作失败
    无头浏览器（插件自己拉一个，用插件自己的 profile，不碰用户的）

关键约定：**无头后端永远不碰用户的真实 profile**。它要么用插件持久化
profile，要么用临时 profile，要么用系统浏览器的独立实例 —— 总之绝不
持有 ProcessSingleton 锁，所以不会把用户挡在自己的浏览器外面。
"""

from __future__ import annotations

from typing import List, Optional

from core.logging_manager import get_logger

logger = get_logger("browser_merged", "cyan")


class Backend:
    """后端接口。两个实现：ExtensionBackend（桥）、HeadlessBackend。"""

    name = "base"
    #: 是否为"用户自己的浏览器"（读写语义更贴近用户所见）
    is_user_browser = False

    @property
    def available(self) -> bool:
        raise NotImplementedError

    @property
    def display(self) -> str:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class BackendRouter:
    """按配置策略在多个后端之间路由。

    策略：
      ``auto``（默认）—— 优先扩展桥；扩展没连上就用无头。
      ``extension``    —— 只用扩展桥，连不上就报错（不偷偷降级）。
      ``headless``     —— 只用无头。
    """

    def __init__(self, strategy: str = "auto"):
        # ⚠️ 曾经有个 `on_fallback` 参数 + `self._on_fallback` 赋值，
        #    但**全仓没有任何地方用它**（没人传、也没人读）——
        #    留着会让人以为"降级时会有回调"，实际不会。
        #    要么真接上，要么删掉；这里选删掉（没有这个需求）。
        self.strategy = (strategy or "auto").lower()
        self._ext: Optional[Backend] = None
        self._headless: Optional[Backend] = None

    def register(self, backend: Backend) -> None:
        if backend.is_user_browser:
            self._ext = backend
        else:
            self._headless = backend

    # ─── 选择 ────────────────────────────────────────────────────────

    @property
    def active(self) -> Optional[Backend]:
        """当前应该用哪个后端。"""
        if self.strategy == "extension":
            return self._ext if (self._ext and self._ext.available) else None
        if self.strategy == "headless":
            return self._headless if (self._headless and self._headless.available) else None
        # auto
        if self._ext and self._ext.available:
            return self._ext
        if self._headless and self._headless.available:
            return self._headless
        return None

    def by_name(self, name: str):
        """按后端名取（`extension` / `headless`）。

        用于**显式切换**：`browser_backend` 工具只切到明确点名的那个，
        不做任何"猜"。
        """
        for b in (self._ext, self._headless):
            if b is not None and b.name == name:
                return b
        return None

    def candidates(self) -> List[Backend]:
        """按优先级列出候选后端（用于失败后重试下一个）。"""
        if self.strategy == "extension":
            return [b for b in (self._ext,) if b]
        if self.strategy == "headless":
            return [b for b in (self._headless,) if b]
        return [b for b in (self._ext, self._headless) if b]

    def describe(self) -> str:
        ext = ("已连接" if (self._ext and self._ext.available)
               else "未连接") if self._ext else "未启用"
        hl = ("已启动" if (self._headless and self._headless.available)
              else "未启动") if self._headless else "未启用"
        act = self.active
        return (f"策略={self.strategy}；扩展桥={ext}；无头={hl}；"
                f"当前={act.display if act else '无可用后端'}")

    def hint(self) -> str:
        """没有任何后端可用时，给用户一句能照着做的话。

        ⚠️ 提示要**按策略分支**：在「只用无头」策略下说"扩展未连接"
        是误导 —— 那种配置下扩展**根本不参与**，用户去装扩展也没用。
        """
        if self.strategy == "extension":
            return ("当前策略是「只用扩展桥」，但扩展没有连接。"
                    "请在浏览器里打开 Kira Browser Bridge 扩展并确认已连接，"
                    "或把该插件配置里的「后端策略」改为 auto。")
        if self.strategy == "headless":
            return ("当前策略是「只用无头浏览器」，但无头浏览器尚未启动。"
                    "调用一次无头工具（例如 browser_page）就会拉起它；"
                    "若一直起不来，用 browser_diag(action='status') 看具体原因。")
        return ("当前没有可用的浏览器：扩展未连接，且无头浏览器也未启动。"
                "可以先在浏览器中启用扩展，或调用一次无头工具触发其启动。")
