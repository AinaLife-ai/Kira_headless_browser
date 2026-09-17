"""扩展桥：真实 WebSocket 端到端（真的服务器 + 真的 JS 客户端）。

这里**不模拟传输层** —— 起一个真的 WebSocket 服务跑插件真实的 ``bridge.py``，
对面用 Node 的原生 ``WebSocket`` 按扩展协议实现一个客户端。
所以握手、命令往返、并发、分块、取消恢复、断线重连都是真实验证的。

需要 ``websockets``（pip）。没装就跳过这一组。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import stat as _stat
import subprocess
import sys
import time
from pathlib import Path

from ..harness import HERE, PLUGIN_DIR, install_stubs, section

TITLE = "扩展桥端到端（真实 WebSocket）"

CLIENT_JS = r"""
const PORT = Number(process.argv[2]);
const url = `ws://127.0.0.1:${PORT}/?token=test`;
let ws;

function connect() {
  return new Promise((resolve) => {
    ws = new WebSocket(url);
    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: "hello", protocol: 1, extension_version: "test",
        browser: "Chrome",
      }));
      resolve();
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "ping") {
        ws.send(JSON.stringify({type: "pong", ts: Date.now()})); return;
      }
      if (msg.type === "welcome") return;
      if (msg.type === "cmd") {
        if (msg.name === "download") {
          // 分块回传（模拟扩展下载）
          const CHUNK = Buffer.alloc(256 * 1024, 65).toString("base64");
          for (let i = 0; i < 4; i++) {
            ws.send(JSON.stringify({type: "chunk", id: msg.id, data: CHUNK}));
          }
          ws.send(JSON.stringify({type: "result", id: msg.id, ok: true,
            data: {ok: true, url: msg.params.url, mime: "application/octet-stream"}}));
          return;
        }
        // ⚠️ wait_for **故意不回应**：上面那个"取消泄漏"用例靠的就是
        //    命令超时，若这里回一条立即结果，超时路径就永远走不到，
        //    用例变成"看起来在测超时、其实每次都被立刻满足"。
        if (msg.name === "wait_for") return;
        const data =
          msg.name === "list_tabs"
            ? {tabs: [
                {id: 1, title: "GitHub", url: "https://github.com/x/y", active: true},
                {id: 2, title: "docs", url: "https://docs.example/a", active: false},
              ], tab_count: 2}
          : msg.name === "get_page"
            ? {url: "https://github.com/x/y", title: "GitHub",
               content: "x".repeat(12000)}
            : {ok: true};
        ws.send(JSON.stringify({type: "result", id: msg.id, ok: true, data}));
      }
    };
    ws.onclose = () => {};
  });
}

(async () => { await connect(); })();
"""


def _have(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def run(r) -> None:
    if not _have("websockets"):
        r.warn("未安装 websockets，跳过端到端检查",
               "pip install websockets")
        return

    install_stubs()
    import types
    pkg = "kirabridge_e2e"
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg] = m

    def load(name, path):
        spec = importlib.util.spec_from_file_location(f"{pkg}.{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg}.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    import core.logging_manager  # noqa: F401  (stub)
    P = load("protocol", PLUGIN_DIR / "protocol.py")
    B = load("bridge", PLUGIN_DIR / "bridge.py")

    # 需要 node 才能跑假扩展客户端；没有就跳过（不算失败）
    import shutil as _sh
    import subprocess as _sp
    # ⚠️ 不能只看"有没有 node"：CLIENT_JS 用了 **Node 22+** 才有的 API
    #    （全局 WebSocket 等），Node 20/21 上会在调用到 bridge 之后
    #    才以难懂的方式炸掉。这里预先判版本，不支持就走跳过路径。
    _node = _sh.which("node")
    if not _node:
        r.warn("没有 node，跳过端到端检查", "安装 Node.js 22+ 后可启用")
        return
    # ⚠️ _v 必须先给默认值：subprocess 本身抛异常（超时/权限）时，
    #    下面的 f-string 会去读未定义的 _v → NameError，
    #    把"node 版本判不出来"变成一句与版本无关的崩溃。
    _v = "?"
    try:
        _v = _sp.run([_node, "--version"], capture_output=True, text=True,
                     timeout=15).stdout.strip().lstrip("v")
        _major = int(_v.split(".")[0])
    except Exception:
        _major = 0
        _v = "?"
    if _major < 22:
        r.warn(f"node 版本过低（v{_v}），跳过端到端检查",
               "需要 Node.js 22+（CLIENT_JS 用到其全局 WebSocket）")
        return

    # ⚠️ 用**每次唯一**的临时文件名：写死在 HERE 下的话，
    #    两个回归进程同时跑会互相覆盖、甚至在 finally 里删掉对方的脚本。
    import tempfile as _tf
    _cfd = _tf.NamedTemporaryFile(
        mode="w", suffix=".mjs", prefix="kira_ext_client_",
        dir=str(HERE), delete=False, encoding="utf-8")
    _cfd.write(CLIENT_JS)
    _cfd.close()
    client_path = Path(_cfd.name)

    results = {}
    _procs = []          # 持有子进程引用，避免被 GC 提前回收
    _server_holder = {}  # 供外层 finally 关服务

    async def main():
        import websockets

        bridge = B.BrowserBridge(command_timeout=15.0)

        class StarletteLike:
            """把 websockets 的连接适配成 Starlette 接口（bridge.py 是按后者写的）。"""

            def __init__(self, ws):
                self._ws = ws
                self.query_params = {"token": "test"}

            async def accept(self):
                return None

            async def receive_text(self):
                return await self._ws.recv()

            async def send_text(self, s):
                return await self._ws.send(s)

            async def close(self, code=1000, reason=""):
                try:
                    await self._ws.close(code, reason)
                except Exception:
                    pass

        async def handler(ws):
            await bridge.handle_connection(StarletteLike(ws))

        # ⚠️ 端口写死会撞车（并行跑/被别的程序占用）→ E0 直接失败。
        #    绑 0 让系统分配空闲端口，再从 server.sockets 读回来。
        # 整个"起服务 + 起子进程"的过程包在 try/finally 里：
        # 中途任何断言抛错都能把 node 进程与服务清干净，不留孤儿。
        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _server_holder["server"] = server
        # 持有引用直到结束：Popen 被 GC 回收会提前杀掉子进程
        proc = subprocess.Popen(["node", str(client_path), str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _procs.append(proc)

        t0 = time.time()
        for _ in range(120):
            if bridge.connected and getattr(bridge, "_hello", None):
                break
            await asyncio.sleep(0.05)
        results["handshake_ms"] = round((time.time() - t0) * 1000)
        results["connected"] = bridge.connected
        # ⚠️ 光有 connected 不够 —— 上面那个等待循环是
        #    "connected **且** 收到 hello"才 break 的，
        #    断言里只查 connected 的话，"连上了但从没握手成功"
        #    （协议字段全空、扩展版本报不出来）会照样通过。
        results["hello_received"] = getattr(bridge, "_hello", None) is not None

        # 命令往返
        lat = []
        for _ in range(10):
            s = time.perf_counter()
            await bridge.send_command(P.CMD_LIST_TABS)
            lat.append((time.perf_counter() - s) * 1000)
        lat.sort()
        results["cmd_p50"] = round(lat[len(lat) // 2], 1)

        # 并发扇出
        t = time.perf_counter()
        got = await asyncio.gather(*[bridge.send_command(P.CMD_LIST_TABS)
                                     for _ in range(10)])
        results["fan10_ms"] = round((time.perf_counter() - t) * 1000, 1)
        results["fan_ok"] = all(x and x.get("tab_count") == 2 for x in got)

        # 大内容
        pg = await bridge.send_command(P.CMD_GET_PAGE, {"detail": "text"})
        results["big_chars"] = len((pg or {}).get("content") or "")

        # 取消泄漏（历史 bug）
        for _ in range(30):
            t_ = asyncio.ensure_future(
                bridge.send_command(P.CMD_WAIT_FOR, {"selector": "#x"},
                                    timeout=0.001))
            try:
                await t_
            except Exception:
                pass
        results["pending_leak"] = len(bridge._pending)

        # 下载分块 → 流式落盘
        import tempfile
        # ⚠️ 用 TemporaryDirectory：mkdtemp 不会清理，
        #    每跑一次就留下一个含 ~1MiB dl.bin 的目录。
        with tempfile.TemporaryDirectory(prefix="kira_dl_") as _dl_dir:
            out = Path(_dl_dir) / "dl.bin"
            cid = "testdl"
            bridge.open_download_sink(cid, str(out), limit=10 * 1024 * 1024)
            try:
                await bridge.send_command(P.CMD_DOWNLOAD,
                                          {"url": "https://example.com/f"},
                                          timeout=15, cmd_id=cid)
            except Exception as e:
                results["dl_err"] = str(e)
            # 读数值要在**退出 with 之前**（目录一关文件就没了）
            results["dl_size"] = out.stat().st_size if out.exists() else 0
            results["dl_sink_cleared"] = cid not in bridge._sinks

        # 断线感知 + 重连
        proc.terminate()
        proc.wait(timeout=5)
        # ⚠️ 不要写死 `sleep(0.4)` 就断言"已断开" —— 进程死了之后，
        #    socket 关闭要经事件循环投递到 bridge 那边，
        #    机器负载高时 400ms 可能不够 → 偶发假失败（flaky）。
        #    改成**轮询到超时**：尽快通过，慢的机器也给足时间。
        t_off = time.time()
        for _ in range(60):          # 最多 3 秒
            if not bridge.connected:
                break
            await asyncio.sleep(0.05)
        results["offline_ms"] = round((time.time() - t_off) * 1000)
        results["offline_detected"] = not bridge.connected

        proc2 = subprocess.Popen(["node", str(client_path), str(port)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _procs.append(proc2)
        for _ in range(120):
            if bridge.connected:
                break
            await asyncio.sleep(0.05)
        results["reconnected"] = bridge.connected
        if bridge.connected:
            rr = await bridge.send_command(P.CMD_LIST_TABS)
            results["reconnect_ok"] = rr and rr.get("tab_count") == 2

        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except Exception:
            proc2.kill()
        server.close()
        await server.wait_closed()

    try:
        asyncio.run(main())
    except Exception as e:
        r.ok("E0 端到端脚本可运行", False, f"{type(e).__name__}: {e}")
        return
    finally:
        # ⚠️ 任何失败路径都要收干净：断言抛错时 main() 会提前退出，
        #    node 子进程可能还活着、WebSocket 还开着。
        for _p in _procs:
            if _p.poll() is None:
                try:
                    _p.terminate()
                    _p.wait(timeout=3)
                except Exception:
                    try:
                        _p.kill()
                    except Exception:
                        pass
        _srv = _server_holder.get("server")
        if _srv is not None:
            try:
                _srv.close()
            except Exception:
                pass
        # 清掉临时客户端脚本，避免污染文件清点
        try:
            client_path.unlink()
        except OSError:
            pass

    r.ok("E1 真实握手完成（连接 + hello 都到位）",
         results.get("connected") and results.get("hello_received"),
         f"耗时 {results.get('handshake_ms')}ms；"
         f"connected={results.get('connected')}、"
         f"hello={results.get('hello_received')}")
    r.ok("E2 命令往返正常", results.get("cmd_p50", 9999) < 500,
         f"P50={results.get('cmd_p50')}ms")
    r.ok("E3 10 条并发命令 id 配对正确", results.get("fan_ok"),
         f"总耗时 {results.get('fan10_ms')}ms")
    r.ok("E4 12KB 正文完整传输", results.get("big_chars") == 12000,
         f"收到 {results.get('big_chars')} 字符")
    r.ok("E5 30 次超时后 _pending 无残留", results.get("pending_leak") == 0,
         f"残留 {results.get('pending_leak')} 个")
    r.ok("E6 下载分块流式落盘（不攒内存）",
         results.get("dl_size") == 4 * 256 * 1024
         and results.get("dl_sink_cleared"),
         f"落盘 {results.get('dl_size')} 字节；sink 已清理="
         f"{results.get('dl_sink_cleared')}")
    r.ok("E7 扩展断开后立刻感知", results.get("offline_detected"))
    r.ok("E8 扩展重连后立即可用",
         results.get("reconnected") and results.get("reconnect_ok"))

    for k in ("handshake_ms", "cmd_p50", "fan10_ms", "big_chars"):
        if k in results:
            r.metric(k, results[k])
