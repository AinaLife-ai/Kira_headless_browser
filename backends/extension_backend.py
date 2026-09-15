"""扩展桥后端 —— 直接驱动**用户自己的浏览器**（零冲突）。

这是合并方案里解决"真实浏览器不能共用"的关键：扩展作为 WebSocket 客户端
主动连出，插件不需要启动任何浏览器，因此**不存在 profile 抢占**。

与无头后端的区别：
  * 用的就是用户眼前那个浏览器（`is_user_browser = True`）
  * 拿得到真实的登录态、Cookie、已打开的标签页
  * 但能力受扩展限制（不能执行任意 JS、不能上传文件）
"""

from __future__ import annotations

from typing import Optional

from core.logging_manager import get_logger

from .base import OpResult, TabInfo
from .router import Backend

logger = get_logger("browser_merged", "cyan")


class ExtensionBackend(Backend):
    """包装 kira_browser_bridge 的 BrowserBridge。"""

    name = "extension"
    is_user_browser = True

    #: 上传大小上限（默认 200MB）—— 内容要经 WebSocket 传，别太大
    MAX_UPLOAD_BYTES = 200 * 1024 * 1024

    def __init__(self, bridge, protocol, max_upload_bytes=None,
                 max_download_bytes=None, download_timeout=None):
        self._bridge = bridge
        self._P = protocol
        self.max_upload_bytes = int(max_upload_bytes or self.MAX_UPLOAD_BYTES)
        self.max_download_bytes = max_download_bytes or 2 * 1024 ** 3
        self.download_timeout = download_timeout or 600.0

    # ─── Backend 接口 ────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        try:
            return bool(self._bridge.connected)
        except Exception:
            return False

    @property
    def display(self) -> str:
        info = getattr(self._bridge, "info", {}) or {}
        ver = info.get("extension_version") or "?"
        br = info.get("browser") or "?"
        return f"用户浏览器（{br} · 扩展 v{ver}）"

    async def close(self) -> None:
        await self._bridge.close()

    async def _send(self, cmd: str, params: Optional[dict] = None, timeout=None) -> OpResult:
        try:
            data = await self._bridge.send_command(cmd, params or {}, timeout=timeout)
            return OpResult(data=data, backend=self.name)
        except Exception as e:
            return OpResult.fail(str(e), self.name)

    # ══════════════════════════════════════════════════════════════════
    #  能力实现（与无头后端同一套签名，便于路由互换）
    # ══════════════════════════════════════════════════════════════════

    async def list_tabs(self) -> OpResult:
        r = await self._send(self._P.CMD_LIST_TABS)
        return r

    async def get_page(self, detail: str = "text", tab_id=None,
                       offset: int = 0, max_chars=None) -> OpResult:
        return await self._send(self._P.CMD_GET_PAGE,
                                {"detail": detail or "text", "tab_id": tab_id})

    async def extract(self, selector: str, attr=None, limit: int = 50, tab_id=None) -> OpResult:
        return await self._send(self._P.CMD_EXTRACT,
                                {"selector": selector, "attr": attr,
                                 "limit": int(limit or 50), "tab_id": tab_id})

    async def navigate(self, url: str, new_tab: bool = False, tab_id=None) -> OpResult:
        return await self._send(self._P.CMD_NAVIGATE,
                                {"url": url, "tab_id": tab_id, "new_tab": bool(new_tab)})

    async def click(self, selector=None, text=None, index=None, **kw) -> OpResult:
        return await self._send(self._P.CMD_CLICK,
                                {"selector": selector, "text": text, "index": index})

    async def type_text(self, selector: str, text: str, submit: bool = False,
                        clear_first: bool = True, **kw) -> OpResult:
        return await self._send(self._P.CMD_TYPE,
                                {"selector": selector, "text": text,
                                 "submit": bool(submit), "clear_first": bool(clear_first)})

    async def scroll(self, direction: str, amount=None, **kw) -> OpResult:
        return await self._send(self._P.CMD_SCROLL,
                                {"direction": direction, "amount": amount})

    async def wait_for(self, selector=None, text=None, timeout: int = 10, **kw) -> OpResult:
        limit = max(1, min(int(timeout or 10), 60))
        return await self._send(self._P.CMD_WAIT_FOR,
                                {"selector": selector, "text": text, "timeout": limit},
                                timeout=limit + 5)

    async def screenshot(self, path: str, full_page: bool = False, selector=None) -> OpResult:
        """扩展用 captureVisibleTab 截图，返回 base64；这里落盘。"""
        r = await self._send(self._P.CMD_SCREENSHOT, {})
        if not r.ok:
            return r
        data = r.data or {}
        img = data.get("image") or ""
        if not img.startswith("data:image"):
            return OpResult.fail("扩展没有返回截图数据", self.name)
        try:
            import base64
            payload = img.split(",", 1)[1]
            with open(path, "wb") as f:
                f.write(base64.b64decode(payload))
            return OpResult(data={"path": path, "url": data.get("url")}, backend=self.name)
        except Exception as e:
            return OpResult.fail(f"保存截图失败: {e}", self.name)

    async def execute_js(self, script: str) -> OpResult:
        """执行任意 JS。

        MV3 下 `chrome.scripting.executeScript` 的 eval 会被页面 CSP 挡掉，
        所以扩展走 `chrome.userScripts`（官方为运行任意代码字符串设计）。
        代价：Chrome 138+ 需要用户手动打开「Allow User Scripts」开关 ——
        扩展会把这种情况翻译成一句用户能照做的话，而不是一个裸错误。
        """
        return await self._send(self._P.CMD_EXEC_JS, {"script": script})

    async def upload_file(self, selector: str, file_path: str) -> OpResult:
        """上传本地文件。

        扩展读不到本地磁盘路径，所以插件把文件内容分块送过去，
        扩展在页面里用 DataTransfer 构造 FileList 塞进 input[type=file]。
        路径白名单由插件侧负责（和原版无头插件一致）。
        """
        import base64
        import os
        resolved = os.path.realpath(file_path)
        if not os.path.isfile(resolved):
            return OpResult.fail(f"文件不存在或不是常规文件: {file_path}", self.name)
        try:
            size = os.path.getsize(resolved)
            if self.max_upload_bytes > 0 and size > self.max_upload_bytes:
                return OpResult.fail(
                    f"文件过大（{size} > {self.max_upload_bytes} 字节），已拒绝上传。"
                    f"如需放开请调整插件配置里的 upload_max_bytes。", self.name)
            with open(resolved, "rb") as f:
                raw = f.read()
            # 分块（每块 256KB）base64，避免一条消息过大
            step = 256 * 1024
            chunks = [base64.b64encode(raw[i:i + step]).decode()
                      for i in range(0, len(raw), step)]
            name = os.path.basename(resolved)
            return await self._send(self._P.CMD_UPLOAD, {
                "selector": selector, "name": name,
                "mime": _guess_mime(name), "chunks": chunks,
            }, timeout=max(30.0, size / (1024 * 1024) * 2))
        except Exception as e:
            return OpResult.fail(f"上传失败: {e}", self.name)

    async def download(self, url: str, path: str) -> OpResult:
        """下载文件。

        让**扩展用用户浏览器的会话**去抓（带上已登录的 Cookie），
        再分块回传给插件落盘。比让插件自己裸奔去下要好：
        登录态资源也能下，而且不会把文件塞进浏览器的下载目录。
        """
        import base64
        r = await self._send(self._P.CMD_DOWNLOAD,
                             {"url": url, "max_bytes": self.max_download_bytes},
                             timeout=self.download_timeout)
        if not r.ok:
            return r
        chunks = (r.data or {}).get("chunks") or []
        if not chunks:
            return OpResult.fail("扩展没有回传文件内容", self.name)
        try:
            with open(path, "wb") as f:
                for c in chunks:
                    f.write(base64.b64decode(c))
            import os
            size = os.path.getsize(path)
            return OpResult(data={"path": path, "size": size,
                                  "url": (r.data or {}).get("url", url),
                                  "mime": (r.data or {}).get("mime")},
                            backend=self.name)
        except Exception as e:
            return OpResult.fail(f"写入下载文件失败: {e}", self.name)

    async def get_info(self) -> OpResult:
        return await self._send(self._P.CMD_GET_INFO)

    async def go_back(self) -> OpResult:
        return await self._send(self._P.CMD_GO_BACK)

    async def refresh(self) -> OpResult:
        return await self._send(self._P.CMD_REFRESH)

    async def hover(self, selector: str) -> OpResult:
        return await self._send(self._P.CMD_HOVER, {"selector": selector})

    async def keyboard_type(self, text: str, delay: int = 0) -> OpResult:
        # 扩展侧没有真正的"逐字输入"，退化为 type 到当前聚焦元素：
        # 用 type 命令 + 当前焦点。这里直接走 key 系列更稳。
        return await self._send(self._P.CMD_TYPE, {"selector": ":focus", "text": text,
                                                   "clear_first": False})

    async def keyboard_press(self, key: str) -> OpResult:
        return await self._send(self._P.CMD_KEY_PRESS, {"key": key})

    async def keyboard_down_up(self, action: str, key: str) -> OpResult:
        cmd = self._P.CMD_KEY_DOWN if action == "down" else self._P.CMD_KEY_UP
        return await self._send(cmd, {"key": key})

    async def mouse_move(self, x: int, y: int, steps: int = 1) -> OpResult:
        return await self._send(self._P.CMD_MOUSE_MOVE, {"x": x, "y": y, "steps": steps})

    async def mouse_click(self, x=None, y=None, button: str = "left",
                          click_count: int = 1) -> OpResult:
        return await self._send(self._P.CMD_MOUSE_CLICK, {
            "x": x, "y": y, "button": button, "click_count": click_count})

    async def mouse_down_up(self, action: str, button: str = "left") -> OpResult:
        cmd = self._P.CMD_MOUSE_DOWN if action == "down" else self._P.CMD_MOUSE_UP
        return await self._send(cmd, {"button": button})

    async def mouse_wheel(self, delta_x: int = 0, delta_y: int = 0) -> OpResult:
        return await self._send(self._P.CMD_MOUSE_WHEEL,
                                {"delta_x": delta_x, "delta_y": delta_y})

    async def mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
                         button: str = "left", steps: int = 10) -> OpResult:
        return await self._send(self._P.CMD_MOUSE_DRAG, {
            "start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y,
            "button": button, "steps": steps}, timeout=60.0)

    async def list_files(self, dir_type: str = "downloads", limit: int = 20) -> OpResult:
        return await self._send(self._P.CMD_LIST_FILES,
                                {"dir_type": dir_type, "limit": limit})

    async def debug_state(self) -> OpResult:
        return await self._send(self._P.CMD_DEBUG)

    async def start(self):
        """扩展后端不需要"启动" —— 连上了就能用。"""
        return None if self.available else "扩展未连接"

    async def cookie_get(self, url: str = "", tab_id=None) -> OpResult:
        """导出当前站点的 cookie（用于打通到无头后端的登录态）。"""
        return await self._send(self._P.CMD_COOKIE_GET, {"url": url, "tab_id": tab_id})

    async def cookie_set(self, cookies: list) -> OpResult:
        """把 cookie 写进用户浏览器。"""
        return await self._send(self._P.CMD_COOKIE_SET, {"cookies": cookies})


_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
    ".csv": "text/csv", ".json": "application/json", ".xml": "application/xml",
    ".zip": "application/zip", ".gz": "application/gzip", ".7z": "application/x-7z-compressed",
    ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4",
    ".webm": "video/webm", ".html": "text/html",
}


def _guess_mime(name: str) -> str:
    import os
    return _MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
