"""WebSocket 协议定义 —— 插件与浏览器扩展之间的通信契约。

消息统一为 JSON 对象，字段：

    {
        "type": "hello" | "welcome" | "cmd" | "result" | "event" | "ping" | "pong" | "error",
        "id":   "<命令 id，仅 cmd / result / error 需要>",
        ...
    }

方向约定：
    扩展 → 插件：hello, result, event, pong
    插件 → 扩展：welcome, cmd, ping, error

把所有魔数集中在这里，扩展侧 browser-bridge/protocol.js 是它的镜像实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# ─── 消息类型 ────────────────────────────────────────────────────────────────

MSG_HELLO = "hello"          # 扩展连上后自报家门
MSG_WELCOME = "welcome"      # 插件确认，回传服务端能力与配置
MSG_CMD = "cmd"              # 插件下发命令
MSG_RESULT = "result"        # 扩展回传命令结果
MSG_EVENT = "event"          # 扩展主动上报事件（页面加载、标签切换等）
MSG_PING = "ping"            # 插件心跳
MSG_PONG = "pong"            # 扩展心跳应答
MSG_ERROR = "error"          # 错误
MSG_CHUNK = "chunk"          # 大文件分块回传（下载用，避免把整个文件塞进一条消息）

PROTOCOL_VERSION = 1

# ─── 命令名（插件 → 扩展） ───────────────────────────────────────────────────

CMD_LIST_TABS = "list_tabs"
CMD_GET_PAGE = "get_page"
CMD_GET_SELECTION = "get_selection"
CMD_EXTRACT = "extract"
CMD_SCREENSHOT = "screenshot"
CMD_WAIT_FOR = "wait_for"

CMD_ACTIVATE_TAB = "activate_tab"
CMD_CLOSE_TAB = "close_tab"
CMD_NAVIGATE = "navigate"
CMD_SCROLL = "scroll"
CMD_CLICK = "click"
CMD_TYPE = "type"

#: 扩展桥补齐的能力（原本只有无头后端能做）
CMD_EXEC_JS = "exec_js"          # 在页面里执行任意 JS（走 chrome.userScripts）
CMD_UPLOAD = "upload"            # 开始上传：建立 upload 会话，返回 upload_id
#: 上传分块 / 收尾 / 中止 —— 与下载方向对称。
#  ⚠️ 为什么要拆成多条消息：单条 WS 帧有硬上限（uvicorn 默认 16 MiB，
#     且超限会**断开整个连接**）。把整份文件塞进一条消息，
#     文件一超过 ~12 MiB（base64 放大 4/3）就必然断线。
CMD_UPLOAD_CHUNK = "upload_chunk"
CMD_UPLOAD_FINISH = "upload_finish"
CMD_UPLOAD_ABORT = "upload_abort"
CMD_DOWNLOAD = "download"        # 用浏览器会话抓取 URL，分块回传
CMD_COOKIE_GET = "cookie_get"    # 导出当前站点的 cookie
CMD_COOKIE_SET = "cookie_set"    # 写入 cookie（跨后端打通登录态）

#: 把无头后端有、扩展桥原先缺的能力补齐，让两个后端签名完全一致
CMD_GET_INFO = "get_info"
CMD_GO_BACK = "go_back"
CMD_REFRESH = "refresh"
CMD_HOVER = "hover"
CMD_KEY_PRESS = "key_press"
CMD_KEY_DOWN = "key_down"
CMD_KEY_UP = "key_up"
CMD_MOUSE_MOVE = "mouse_move"
CMD_MOUSE_CLICK = "mouse_click"
CMD_MOUSE_DOWN = "mouse_down"
CMD_MOUSE_UP = "mouse_up"
CMD_MOUSE_WHEEL = "mouse_wheel"
CMD_MOUSE_DRAG = "mouse_drag"
CMD_LIST_FILES = "list_files"
CMD_DEBUG = "debug"

#: 写操作集合 —— 只读模式下会被拦截，且需要过域名白名单
WRITE_COMMANDS = frozenset({
    CMD_ACTIVATE_TAB,
    CMD_CLOSE_TAB,
    CMD_NAVIGATE,
    CMD_SCROLL,
    CMD_CLICK,
    CMD_TYPE,
    # 这三个会改变页面/本地状态，归入写操作一起受只读模式与域名白名单约束
    CMD_EXEC_JS,
    CMD_UPLOAD,
    CMD_DOWNLOAD,
    CMD_GO_BACK,
    CMD_REFRESH,
    CMD_HOVER,
    CMD_KEY_PRESS,
    CMD_KEY_DOWN,
    CMD_KEY_UP,
    CMD_MOUSE_MOVE,
    CMD_MOUSE_CLICK,
    CMD_MOUSE_DOWN,
    CMD_MOUSE_UP,
    CMD_MOUSE_WHEEL,
    CMD_MOUSE_DRAG,
    CMD_MOUSE_MOVE,
    CMD_COOKIE_SET,
})

#: 只读但需要单列（不属于写操作）
READ_EXTRA = frozenset({CMD_COOKIE_GET})

ALL_COMMANDS = frozenset({
    CMD_LIST_TABS, CMD_GET_PAGE, CMD_GET_SELECTION, CMD_EXTRACT,
    CMD_SCREENSHOT, CMD_WAIT_FOR,
}) | WRITE_COMMANDS | READ_EXTRA | frozenset({
    CMD_EXEC_JS, CMD_UPLOAD, CMD_DOWNLOAD, CMD_COOKIE_SET,
    CMD_UPLOAD_CHUNK, CMD_UPLOAD_FINISH, CMD_UPLOAD_ABORT,
    CMD_GET_INFO, CMD_GO_BACK, CMD_REFRESH, CMD_HOVER,
    CMD_KEY_PRESS, CMD_KEY_DOWN, CMD_KEY_UP,
    CMD_MOUSE_MOVE, CMD_MOUSE_CLICK, CMD_MOUSE_DOWN, CMD_MOUSE_UP,
    CMD_MOUSE_WHEEL, CMD_MOUSE_DRAG, CMD_LIST_FILES, CMD_DEBUG,
})

# ─── 事件名（扩展 → 插件） ───────────────────────────────────────────────────

EVT_PAGE_LOADED = "page_loaded"        # 页面加载完成
EVT_TAB_ACTIVATED = "tab_activated"    # 用户切换了标签页
EVT_TAB_CLOSED = "tab_closed"
EVT_NAVIGATED = "navigated"            # 同一标签内地址变化
EVT_USER_CONFIRMED = "user_confirmed"  # 用户在扩展侧点了允许/拒绝


@dataclass
class BridgeCommand:
    """一条待发送、等待返回的命令。"""

    cmd_id: str
    name: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict:
        return {"type": MSG_CMD, "id": self.cmd_id, "name": self.name, "params": self.params}


@dataclass
class BridgeResult:
    """扩展返回的命令结果。"""

    cmd_id: str
    ok: bool
    data: Any = None
    error: Optional[str] = None

    @classmethod
    def from_wire(cls, raw: dict) -> "BridgeResult":
        return cls(
            cmd_id=str(raw.get("id", "")),
            ok=bool(raw.get("ok", False)),
            data=raw.get("data"),
            error=raw.get("error"),
        )


@dataclass
class HelloPayload:
    """扩展握手时上报的自身信息。"""

    extension_version: str = "unknown"
    browser: str = "unknown"
    protocol: int = PROTOCOL_VERSION

    @classmethod
    def from_wire(cls, raw: dict) -> "HelloPayload":
        return cls(
            extension_version=str(raw.get("extension_version", "unknown")),
            browser=str(raw.get("browser", "unknown")),
            protocol=int(raw.get("protocol", 0) or 0),
        )
