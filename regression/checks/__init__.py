"""检查脚本登记表。

加新检查：在 ``checks/`` 下建一个模块，写 ``TITLE`` 和 ``run(report)``，
然后在这里登记。``run_all.py`` 会按顺序跑。
"""

from __future__ import annotations

from . import (
    bridge_e2e,
    callgraph,
    content_dom,
    contract,
    file_hygiene,
    runtime_behavior,
    security_rules,
    static_audit,
    tool_merge,
    wiring,
)

#: (模块, 是否默认启用)
ALL_CHECKS = [
    static_audit,        # 静态一致性 / README / 历史回归 / 运行时坑 / 打包
    tool_merge,          # 工具合并零丢失
    wiring,              # 接线完整性（配置接通 / 数据透传）
    contract,            # 两后端返回契约一致性
    callgraph,           # 调用图完整性（未定义方法 / 签名合规）
    security_rules,      # 域名与本机地址规则
    file_hygiene,        # 文件冗余/缺失清点
    runtime_behavior,    # 生命周期 / 路由 / 内存（假 Playwright）
    bridge_e2e,          # 真实 WebSocket 端到端
    content_dom,         # 扩展点击行为（真实 DOM）
]

__all__ = ["ALL_CHECKS"]
