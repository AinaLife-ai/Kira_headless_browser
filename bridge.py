"""Bridge —— 与浏览器扩展的长连接管理与会话路由。

设计要点：

1. **扩展主动连出**。浏览器扩展无法监听端口，所以由扩展作为 WebSocket 客户端
   连到 KiraAI 的 ``/ws/plugin/headless_browser/bridge``。

2. **请求/应答配对**。插件下发 ``cmd``（带唯一 ``id``），为每个 id 建一个
   ``asyncio.Future``，扩展返回 ``result`` 时按 id 唤醒。这样 Tool 调用可以
   ``await`` 到扩展的返回值。

3. **单连接模型**。同一时刻只接受一个扩展连接（新连接踢掉旧连接），避免多窗口
   抢同一个浏览器造成不可预期的行为。握手时用 ``hello`` 上报自身信息。

4. **事件回调**。扩展主动上报的 ``event`` 不等待应答，直接分发给注册的监听者，
   用于"可感知"（页面加载、标签切换）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from core.logging_manager import get_logger

from . import protocol as P

logger = get_logger("browser_bridge", "cyan")


class BridgeNotConnected(RuntimeError):
    """扩展尚未连接。"""


class BridgeTimeout(RuntimeError):
    """等待扩展响应超时。"""


class BridgeError(RuntimeError):
    """扩展返回了错误。"""

    #: 扩展上报的错误类别（如 ``"timeout"``）。None 表示未分类。
    err_code: Optional[str] = None


class BrowserBridge:
    """管理唯一的扩展连接，并提供 ``send_command`` 请求/应答能力。"""

    #: 心跳间隔（秒）。扩展的 MV3 Service Worker 会被浏览器回收，
    #: 这个间隔决定了回收后多久能被发现。
    HEARTBEAT_INTERVAL = 25.0

    def __init__(self, command_timeout: float = 20.0):
        self.command_timeout = command_timeout

        self._ws = None
        self._session_id: Optional[str] = None
        self._hello: Optional[P.HelloPayload] = None
        self._connected_at: float = 0.0

        # cmd_id -> Future[BridgeResult]
        self._pending: Dict[str, asyncio.Future] = {}

        # 顺序发送锁：避免同一 WebSocket 上多协程交错写
        self._send_lock = asyncio.Lock()

        # 事件监听者：name -> [callback]
        self._event_listeners: Dict[str, list] = {}
        self._any_listener: list = []

        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None

        # cmd_id -> 一个「分块接收器」。下载时扩展边收边回传，
        # 这里**边收边写盘**，不把整份文件攒在内存里。
        #   {"path": 目标文件, "handle": 打开的文件对象, "total": 已收字节}
        self._sinks: Dict[str, dict] = {}

        # 统计
        self.commands_sent = 0
        self.commands_failed = 0

    # ─── 连接生命周期 ────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def read_timeout(self) -> float:
        """多久没收到扩展任何数据就判定连接已死。

        取「心跳间隔的 2 倍」和「命令超时 + 余量」里更大的那个：
        前者兜住半开连接，后者保证不会在正常等长命令时误判。
        """
        return max(self.HEARTBEAT_INTERVAL * 2, self.command_timeout + 10.0)

    @property
    def info(self) -> dict:
        return {
            "connected": self.connected,
            "session_id": self._session_id,
            "extension_version": getattr(self._hello, "extension_version", None),
            "browser": getattr(self._hello, "browser", None),
            "protocol": getattr(self._hello, "protocol", None),
            "connected_at": self._connected_at or None,
            "uptime": (time.time() - self._connected_at) if self._connected_at else 0,
            "commands_sent": self.commands_sent,
            "commands_failed": self.commands_failed,
        }

    async def handle_connection(self, ws) -> None:
        """处理一条新的扩展连接。由插件 WS 端点调用。

        **必须先 accept**：KiraAI 的插件 WS 路由把原始 ``WebSocket`` 对象直接交给
        插件 handler（见 ``plugin_registry._register_plugin_ws_for``），鉴权依赖
        ``require_ws_auth`` 只做校验、不负责握手。所以 ``accept()`` 由本函数负责。
        漏掉它会得到「ASGI callable returned without sending handshake」，
        客户端表现为连上即断、反复重连。
        """
        session_id = uuid.uuid4().hex[:8]

        # 握手：接受连接后才能收发消息
        try:
            await ws.accept()
        except Exception as e:
            logger.warning(f"WebSocket 握手失败: {type(e).__name__}: {e}")
            return

        # 单连接模型：踢掉旧连接
        if self._ws is not None:
            logger.warning("已有扩展连接，主动断开旧连接")
            # ⚠️ 必须用 _force_close，不能用 _close_ws。
            #    _close_ws 只是关 socket，它**不清 _pending 也不收 _sinks**；
            #    而旧连接的接收循环随后走到 _cleanup 时会因为
            #    self._session_id 已被换掉而直接 return ——
            #    结果：旧连接上的在途命令一直等到超时，
            #          下载 sink 的文件句柄也一直开着到那时。
            #    _force_close 会一次性做完：cancel 心跳 + 失败在途命令 +
            #    收掉下载 sink。
            await self._force_close(self._ws, code=4001,
                                    reason="Replaced by a new connection")

        self._ws = ws
        self._session_id = session_id
        self._connected_at = time.time()
        self._hello = None

        logger.info(f"扩展已连接 (session={session_id})")

        try:
            # 等扩展发 hello，最多 10 秒
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") == P.MSG_HELLO:
                    self._hello = P.HelloPayload.from_wire(msg)
                    logger.info(
                        f"扩展握手成功: {self._hello.browser} / "
                        f"v{self._hello.extension_version} / proto={self._hello.protocol}"
                    )
                    if self._hello.protocol != P.PROTOCOL_VERSION:
                        logger.warning(
                            f"协议版本不一致: 扩展={self._hello.protocol} "
                            f"插件={P.PROTOCOL_VERSION}，可能出现兼容问题"
                        )
                else:
                    logger.warning(f"扩展首帧不是 hello，实际为: {msg.get('type')}")
            except asyncio.TimeoutError:
                logger.warning("等待扩展 hello 超时，仍继续建立连接")

            await self._send({"type": P.MSG_WELCOME, "protocol": P.PROTOCOL_VERSION,
                              "session_id": session_id})

            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # 主接收循环。
            #
            # 这里必须带读超时：只靠心跳发 ping 是不够的 —— 扩展被
            # 休眠/唤醒、MV3 Service Worker 被系统回收之后，socket 会成为
            # 半开连接：send 可能不报错，但对面永远不回。此时 `self._ws`
            # 仍然非 None，`connected` 一直是 True，面板显示"已连接"，
            # 而每个工具调用都卡到超时。读超时是唯一能兜住这种情况的闸门。
            while True:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(),
                                                 timeout=self.read_timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"{self.read_timeout:.0f}s 没收到扩展任何数据，"
                        f"判定连接已失效，主动断开 (session={session_id})"
                    )
                    await self._force_close(ws, code=4002,
                                            reason="No data from extension")
                    break
                if raw is None:
                    # 显式收到关闭帧（部分实现回 None 而不是抛异常）
                    break
                await self._on_message(raw)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 扩展正常关闭也会走到这里，所以不刷 ERROR；但要留下可诊断的痕迹
            from starlette.websockets import WebSocketDisconnect
            if isinstance(e, WebSocketDisconnect):
                logger.info(
                    f"扩展关闭连接 (session={session_id}, code={e.code})"
                )
            else:
                logger.warning(
                    f"扩展连接异常结束 (session={session_id}): "
                    f"{type(e).__name__}: {e}"
                )
        finally:
            self._cleanup(session_id)

    async def _force_close(self, ws, code: int, reason: str) -> None:
        """服务端判断连接已死时，自己把状态清干净。

        先作废 session 再关 socket：关 socket 会让接收循环抛出
        WebSocketDisconnect 并走到 ``_cleanup``，session 已变 None 时
        那次 cleanup 是空操作，避免两边重复清理。
        """
        if self._ws is not ws:
            return  # 已经被新连接替换了，不要动

        self._session_id = None
        # ⚠️ 不能取消**自己**：心跳循环在发送失败时会调 _force_close，
        #    那时 current_task() 就是 _heartbeat_task —— cancel() 会把
        #    当前协程在下一个 await 点取消掉，**后面的 _close_ws 根本执行不到**，
        #    socket 就那么留着（面板显示已断开、实际连接还在）。
        #    自己调用自己时只需把引用清掉（本协程随后就会 return）。
        try:
            _cur = asyncio.current_task()
        except RuntimeError:
            _cur = None
        if (self._heartbeat_task and not self._heartbeat_task.done()
                and self._heartbeat_task is not _cur):
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

        for cmd_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(BridgeNotConnected("扩展连接已失效"))
        self._pending.clear()
        for cid in list(self._sinks):
            self._abort_sink(cid, RuntimeError("disconnected"))

        self._ws = None
        self._hello = None
        self._connected_at = 0.0

        await self._close_ws(ws, code=code, reason=reason)

    def _cleanup(self, session_id: str) -> None:
        if self._session_id != session_id:
            # 已被新连接替换，交给新连接管理
            return

        logger.info(f"扩展断开 (session={session_id})")
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

        # 未完成的命令全部失败，避免调用方无限等待
        for cmd_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(BridgeNotConnected("扩展连接已断开"))
        self._pending.clear()
        for cid in list(self._sinks):
            self._abort_sink(cid, RuntimeError("disconnected"))

        self._ws = None
        self._session_id = None
        self._hello = None
        self._connected_at = 0.0

    async def close(self) -> None:
        """插件卸载时调用，干净地断开扩展。"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        for cmd_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(BridgeNotConnected("插件正在关闭"))
        self._pending.clear()

        # 关掉所有还开着的下载 sink，删掉半成品文件
        for cid in list(self._sinks):
            self._abort_sink(cid, RuntimeError("插件正在关闭"))

        if self._ws is not None:
            await self._close_ws(self._ws, code=1001, reason="Plugin shutting down")

        self._ws = None
        self._session_id = None

    @staticmethod
    async def _close_ws(ws, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass

    # ─── 收发 ────────────────────────────────────────────────────────────

    async def _send(self, payload: dict) -> None:
        ws = self._ws
        if ws is None:
            raise BridgeNotConnected("扩展未连接")
        async with self._send_lock:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def _on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("收到无法解析的扩展消息")
            return

        mtype = msg.get("type")

        if mtype == P.MSG_RESULT:
            result = P.BridgeResult.from_wire(msg)
            fut = self._pending.pop(result.cmd_id, None)
            if fut and not fut.done():
                fut.set_result(result)
            else:
                logger.debug(f"收到无人认领的命令结果: {result.cmd_id}")

        elif mtype == P.MSG_CHUNK:
            # 下载分块：只接受**属于当前活动下载命令**的分块，并立刻写盘。
            # （旧实现把所有分块攒进列表，大文件会占住与完整文件相当的内存；
            #   而且未知 cmd_id 的分块会一直留着不释放。）
            cid = str(msg.get("id", ""))
            data = msg.get("data")
            sink = self._sinks.get(cid)
            if not (cid and data and sink):
                return
            # ⚠️ 关于"同步写盘会不会阻塞事件循环 / 会不会与 _abort_sink 竞态"：
            #    · **竞态：没有**。从 `self._sinks.get(cid)` 取到 sink 引用、
            #      到 `handle.write()` 之间**没有任何 await** —— 在事件循环里
            #      这一段是原子的，_abort_sink()（另一个 task）插不进来，
            #      所以不会出现"写已经关掉的句柄"。
            #    · **阻塞：量很小**。分块是 32KB（扩展侧 step=0x8000），
            #      写普通文件是微秒级。慢盘/网络挂载上会有短暂阻塞，
            #      但这是单文件顺序写，排队不会改善。
            #    曾考虑过 per-sink 队列 + to_thread 彻底移出事件循环，
            #    但那样要额外维护"写入/abort/close 三者的生命周期协调"，
            #    为一个微秒级操作引入并发复杂度不划算 —— 保持现状。
            try:
                import base64 as _b64
                raw = _b64.b64decode(data)
                sink["handle"].write(raw)
                sink["total"] += len(raw)
                limit = sink.get("limit") or 0
                if limit and sink["total"] > limit:
                    self._abort_sink(cid, RuntimeError(f"下载超过上限 {limit} 字节"))
                    # 通知调用方失败
                    fut = self._pending.pop(cid, None)
                    if fut and not fut.done():
                        fut.set_result(P.BridgeResult(
                            cmd_id=cid, ok=False, error=f"下载超过上限 {limit} 字节"))
            except Exception as e:
                logger.warning(f"写入下载分块失败: {e}")
                self._abort_sink(cid, e)
                # ⚠️ 必须**同样把这个 future resolve 掉**（上面"超上限"分支就是
                #    这么做的）。只 abort sink 的话 future 一直挂着 →
                #    send_command 会干等到 download_timeout（默认 600 秒），
                #    磁盘写满 / base64 非法这种情况要卡 10 分钟才报错。
                fut = self._pending.pop(cid, None)
                if fut and not fut.done():
                    fut.set_result(P.BridgeResult(
                        cmd_id=cid, ok=False, error=f"写入下载分块失败: {e}"))

        elif mtype == P.MSG_PONG:
            pass

        elif mtype == P.MSG_EVENT:
            await self._dispatch_event(str(msg.get("name", "")), msg.get("data") or {})

        elif mtype == P.MSG_ERROR:
            logger.warning(f"扩展上报错误: {msg.get('error')}")

        else:
            logger.debug(f"忽略未知消息类型: {mtype}")

    async def _heartbeat_loop(self) -> None:
        """定期 ping，探测扩展是否还活着（MV3 的 Service Worker 会被回收）。

        ping 发不出去说明 socket 已经烂了：这里必须**主动**把连接判死，
        不能只 `return` —— 接收循环可能正卡在半开连接上永远等下去，
        那样 `connected` 会一直显示 True。
        """
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                ws = self._ws
                if ws is None:
                    return
                try:
                    await self._send({"type": P.MSG_PING, "ts": int(time.time())})
                except Exception as e:
                    logger.warning(f"心跳发送失败（{type(e).__name__}），判定连接已失效")
                    await self._force_close(ws, code=4002, reason="Heartbeat failed")
                    return
        except asyncio.CancelledError:
            return

    # ─── 命令下发 ────────────────────────────────────────────────────────

    async def send_command(self, name: str, params: Optional[dict] = None,
                           timeout: Optional[float] = None,
                           cmd_id: Optional[str] = None) -> Any:
        """下发一条命令并等待扩展返回结果。

        Args:
            name: 命令名，见 ``protocol.CMD_*``
            params: 命令参数
            timeout: 覆盖默认超时

        Returns:
            扩展返回的 ``data`` 字段

        Raises:
            BridgeNotConnected: 扩展没连上
            BridgeTimeout: 超时
            BridgeError: 扩展执行失败
        """
        if not self.connected:
            raise BridgeNotConnected(
                "浏览器扩展未连接。请确认已在浏览器中安装并启用了 Kira Browser Bridge 扩展"
            )

        if name not in P.ALL_COMMANDS:
            raise BridgeError(f"未知命令: {name}")

        cmd_id = cmd_id or uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[cmd_id] = fut

        wait = timeout if timeout is not None else self.command_timeout

        try:
            await self._send(P.BridgeCommand(cmd_id, name, params or {}).to_wire())
            self.commands_sent += 1
            result: P.BridgeResult = await asyncio.wait_for(fut, timeout=wait)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            # ⚠️ 超时/异常/失败三条路径都必须收掉 sink，否则下载文件句柄
            #    会一直挂到进程退出，磁盘上还留着半成品。
            self._abort_sink(cmd_id, BridgeTimeout(f"命令 {name} 超时"))
            self.commands_failed += 1
            raise BridgeTimeout(f"命令 {name} 超时（{wait}s），扩展没有响应") from None
        except Exception as e:
            self._pending.pop(cmd_id, None)
            self._abort_sink(cmd_id, e)
            self.commands_failed += 1
            raise

        if not result.ok:
            _err = BridgeError(result.error or f"命令 {name} 执行失败")
            # ⚠️ 把错误类别透传上去 —— 上层（extension_backend）据此判定
            #    "超时 = 不确定，禁止换后端重试"，不依赖文案匹配。
            _err.err_code = result.error_code
            self._abort_sink(cmd_id, _err)
            self.commands_failed += 1
            raise _err

        # 下载类命令：分块已经边收边写进了 sink，这里只把落盘结果报回去
        sink = self._finish_sink(cmd_id)
        if sink:
            data = dict(result.data or {}) if isinstance(result.data, dict) else {}
            data["bytes"] = sink["total"]
            data["path"] = sink["path"]
            return data
        return result.data

    # ─── 下载分块接收器 ──────────────────────────────────────────────

    def open_download_sink(self, cmd_id: str, path: str, limit: int = 0) -> None:
        """为某个下载命令开一个落盘接收器（调用方在 send_command 之前调）。

        ⚠️ 打不开**必须抛出去**，不能只记日志就 return ——
        那样 sink 没注册，后续所有 chunk 会被丢掉，`_finish_sink` 返回 None，
        `send_command` 就把扩展的原始 payload（ok:true, bytes:N）原样返回，
        调用方会**报告下载成功但磁盘上根本没有文件**。
        """
        h = open(path, "wb")     # 失败就让调用方看到
        self._sinks[cmd_id] = {"path": path, "handle": h, "total": 0, "limit": limit}

    def _finish_sink(self, cmd_id: str):
        sink = self._sinks.pop(cmd_id, None)
        if not sink:
            return None
        try:
            sink["handle"].close()
        except Exception:
            pass
        return sink

    def abort_download_sink(self, cmd_id: str) -> None:
        """公开的收尾入口（调用方在命令失败时用，不必碰私有方法）。"""
        self._abort_sink(cmd_id, RuntimeError("命令未成功完成"))

    def _abort_sink(self, cmd_id: str, exc: Exception) -> None:
        """出错/超限/断开时：关文件并删掉半成品，不留垃圾。"""
        sink = self._sinks.pop(cmd_id, None)
        if not sink:
            return
        try:
            sink["handle"].close()
        except Exception:
            pass
        try:
            import os as _os
            if _os.path.exists(sink["path"]):
                _os.remove(sink["path"])
        except Exception:
            pass
        logger.warning(f"下载已中止并清理临时文件: {sink['path']}")

    # ─── 事件订阅（"可感知"） ────────────────────────────────────────────

    def on_event(self, name: str, callback: Callable[[dict], Awaitable[None]]) -> None:
        """注册某个扩展事件的异步回调。``name`` 传 ``"*"`` 表示监听全部。"""
        if name == "*":
            self._any_listener.append(callback)
        else:
            self._event_listeners.setdefault(name, []).append(callback)

    def clear_event_listeners(self) -> None:
        """清空所有已注册的事件回调。

        为什么需要：框架**热重载插件**时会重新走一遍 ``initialize()``，
        而 ``on_event`` 是**追加**语义 —— 不清的话回调会一次次累积，
        同一个事件被重复处理（`_on_user_confirmed` 这类还会重复推进状态）。

        ⚠️ 这个方法名是被 `main.py` 的 ``initialize()`` 调用的：写漏了就是
        **插件直接起不来**（``AttributeError``，日志里只有一行
        "Failed to initialize plugin"）。而它是在**协作者对象**上调用
        （``self.bridge.xxx()``），当时的调用图检查只扫 ``self.xxx()``，
        所以没抓到 —— 现在检查已经补上那一类（callgraph B1）。
        """
        self._event_listeners.clear()
        self._any_listener.clear()

    async def _dispatch_event(self, name: str, data: dict) -> None:
        callbacks = list(self._event_listeners.get(name, [])) + list(self._any_listener)
        for cb in callbacks:
            try:
                await cb(data)
            except Exception as e:
                logger.error(f"事件回调 {name} 执行失败: {e}")
