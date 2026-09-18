"""零配置接入端到端：**新装扩展**能不能自己连上。

为什么单独做这一条：

`bridge_e2e` 用的是**手写的假扩展客户端**，验证的是**桥协议**本身
（握手/命令/并发/重传）。但用户报的那类问题（"我明明装了扩展，插件还说没装"）
出在**另一段路**上 ——

    扩展首次安装（chrome.storage 全空）
        → bootstrap → connect()
        → discover() 扫候选端口
        → GET /api/plugin/headless_browser/pair
        → applyPair() 存下来
        → 连 WebSocket → 发 hello
        → 插件侧 bridge.connected 才变成 True

这条路以前没有任何检查覆盖，所以出过的两个真 bug（SW 启动即崩、
端口采信错）都是用户撞出来的。

这里做的是**跨语言**验证：Python 起**真实的 bridge.py**，Node 跑**扩展的真实
background.js**（stub 掉 chrome API），两边真连一次。
"""
from __future__ import annotations

import asyncio
import json
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from ..harness import PLUGIN_DIR, src_safe, strip_comments_only

HERE = Path(__file__).resolve().parent

#: 假 KiraAI 服务端发出去的令牌（与 bridge_e2e 保持同一个值）
TOKEN = "tok"

TITLE = "零配置接入（真 bridge + 扩展真代码）"

#: 扩展里写死的配对路径（和 protocol.js 的 PAIR_PATH 必须一致 —— 下面 P0 会核对）
PAIR_PATH = "/api/plugin/headless_browser/pair"
WS_PATH = "/ws/plugin/headless_browser/bridge"
#: 扩展的候选端口第一位。测试服务器就起在这儿 ——
#: 因为真实的"安装即用"就是靠扫这些端口才找到 KiraAI 的。
CANDIDATE = [5267, 8080, 8000, 3000, 5000]


#: Node 侧：跑扩展的真实代码。
#: ⚠️ 必须 Node 22+（background.js 用全局 WebSocket）。
CLIENT_JS = r"""
const P = process.env.KIRA_PLUGIN_DIR + "/browser-bridge/";
const scenario = JSON.parse(process.env.KB_SCENARIO);

// ── chrome API 桩 ──────────────────────────────────────────────
const store = Object.assign({}, scenario.storage || {});
// ⚠️ onMessage 要**真的能捕获**监听器 —— 场景 3 要靠它模拟
//    "面板页(content script) 把接入信息递给扩展后台"这条路。
const msgListeners = [];
function listener() {
  return { addListener(fn) { msgListeners.push(fn); }, removeListener() {} };
}
globalThis.chrome = {
  storage: { local: {
    async get(keys) {
      const arr = Array.isArray(keys) ? keys : Object.keys(keys || {});
      const out = {};
      for (const k of arr) out[k] = store[k];
      return out;
    },
    async set(obj) { Object.assign(store, obj); },
  } },
  action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
  alarms: { get: async () => null, create() {}, onAlarm: listener() },
  runtime: { getManifest: () => ({ version: "test" }),
             onInstalled: listener(), onStartup: listener(), onMessage: listener(),
             sendMessage: async () => ({}) },
  tabs: { onActivated: listener(), onRemoved: listener(), onUpdated: listener(),
          query: async () => [], get: async () => null, create: async () => ({}),
          remove: async () => {}, update: async () => ({}),
          captureVisibleTab: async () => "" },
  notifications: { onClicked: listener(), onButtonClicked: listener() },
  scripting: { executeScript: async () => [] },
  webNavigation: { onCompleted: listener() },
  windows: { update: async () => {} },
};

const out = { errors: [] };
const realErr = console.error;
console.error = (...a) => { out.errors.push(a.map(String).join(" ")); };

try {
  const mod = await import(P + "background.js");

  if (scenario.action === "connect") {
    out.result = await mod.connect({ manual: true });
  } else if (scenario.action === "auto_only") {
    // ⚠️ **什么都不调** —— 只加载模块，让顶层的 bootstrap 自己跑。
    //    这才是"安装即用"的真路径：真实用户装上扩展后，没有任何人
    //    会去调 connect({manual:true})。
    //    之前的场景全都显式调了 connect({manual:true})，而 manual
    //    会**强制清掉 userDisconnected 闸门**再连 —— 等于把自动路径的
    //    前置条件全绕过去了。这是个假的绿灯。
  } else if (scenario.action === "discover") {
    out.result = await mod.discover({ timeoutMs: 400 });
  } else if (scenario.action === "alive") {
    // 模拟"保活闹钟把 Service Worker 唤醒"之后跑的那一步。
    // 场景 4 靠它验证：首次发现失败之后，后台会不会**再试**。
    await mod.ensureAlive();
    await new Promise((r) => setTimeout(r, 2500));
  } else if (scenario.action === "page_pair") {
    // 模拟 KiraAI 面板页那一路：content script(`pairing-page.js`) 收到
    // 页面的 postMessage 后，用 runtime.sendMessage 把接入信息递给后台。
    out.result = await new Promise((resolve) => {
      let done = false;
      const send = (r) => { if (!done) { done = true; resolve(r); } };
      for (const fn of msgListeners) {
        try { fn({ action: "page_pair", payload: scenario.payload }, {}, send); }
        catch (e) { send({ ok: false, error: String(e) }); }
      }
      setTimeout(() => send({ ok: false, error: "没人在听 page_pair" }), 3000);
    });
  }

  // 给 WS 握手一点时间
  await new Promise((r) => setTimeout(r, scenario.settle_ms || 800));

  out.status = store["kb_last_status"] || null;
  out.instances = store["kb_instances"] || null;
  out.legacy = { host: store["kb_host"], port: store["kb_port"],
                 token: store["kb_token"] ? "(set)" : null };
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
console.error = realErr;
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n");
// ⚠️ 必须**强制退出**：扩展连上之后还有心跳/重连定时器挂着，
//    进程不会自己结束 —— 不加这句，测试会一直挂到超时。
process.exit(0);
"""


def _have_node() -> tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "没有 node"
    try:
        v = subprocess.run([node, "--version"], capture_output=True, text=True,
                           timeout=15).stdout.strip().lstrip("v")
        major = int(v.split(".")[0])
    except Exception:
        return False, "node 版本判不出来"
    if major < 22:
        return False, f"node v{v} 过低（需要 22+，background.js 用全局 WebSocket）"
    return True, node


def run(r) -> None:
    import core.logging_manager  # noqa: F401  (stub)

    # ── P0：测试里写死的路径必须和扩展里的一致 ────────────────────────
    #    否则这个检查测的就是"我编的服务器"而不是"真实的扩展路径"。
    proto = src_safe("browser-bridge/protocol.js")
    bad0 = []
    if PAIR_PATH not in proto:
        bad0.append(f"PAIR_PATH {PAIR_PATH} 不在 protocol.js 里")
    if WS_PATH not in proto:
        bad0.append(f"WS 路径 {WS_PATH} 不在 protocol.js 里")
    if 'buildWsUrl' not in proto:
        bad0.append("protocol.js 里没有 buildWsUrl")
    r.ok("P0 测试用的路径与扩展里写的一致（否则测的不是真东西）",
         not bad0, f"问题={bad0 or '无'}")

    ok_node, node = _have_node()
    if not ok_node:
        r.warn("没有可用的 Node 22+，跳过零配置接入端到端", node)
        return

    # ── 加载真实的 bridge.py / protocol.py ───────────────────────────
    pkg = "kirabrowser_e2e"
    m = type(sys)(pkg)
    m.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg] = m

    def load(name, path):
        spec = importlib.util.spec_from_file_location(f"{pkg}.{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg}.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    try:
        P = load("protocol", PLUGIN_DIR / "protocol.py")
        B = load("bridge", PLUGIN_DIR / "bridge.py")
    except Exception as e:
        r.ok("P1 能加载 bridge.py / protocol.py", False, f"{type(e).__name__}: {e}")
        return

    _v = "?"
    try:
        _v = subprocess.run([node, "--version"], capture_output=True, text=True,
                            timeout=15).stdout.strip().lstrip("v")
        _major = int(_v.split(".")[0])
    except Exception:
        _major = 0
        _v = "?"
    if _major < 22:
        r.warn(f"node 版本过低（v{_v}），跳过零配置接入端到端",
               "需要 Node.js 22+")
        return

    _fd = tempfile.NamedTemporaryFile(mode="w", suffix=".mjs",
                                      prefix="kira_pair_e2e_", dir=str(HERE),
                                      delete=False, encoding="utf-8")
    _fd.write(CLIENT_JS)
    _fd.close()
    client_path = Path(_fd.name)

    holder = {}
    state = {"reported_port": None}       # 服务器"自称"的端口（可故意报错）

    async def main():
        import websockets
        from websockets.http11 import Response
        from websockets.datastructures import Headers

        bridge = B.BrowserBridge(command_timeout=15.0)
        holder["bridge"] = bridge

        class StarletteLike:
            """把 websockets 连接适配成 Starlette 接口（bridge.py 按后者写）。"""

            def __init__(self, ws, token):
                self._ws = ws
                self.query_params = {"token": token}

            async def accept(self):
                return None

            async def receive_text(self):
                return await self._ws.recv()

            async def send_text(self, s):
                await self._ws.send(s)

            async def close(self, code=1000, reason=""):
                await self._ws.close(code, reason)

        async def handler(ws):
            try:
                await bridge.handle_connection(StarletteLike(ws, "tok"))
            except Exception:
                pass

        def process_request(connection, request):
            """HTTP 探测走这儿；其余（WS 升级）返回 None 交给 handler。"""
            if not request.path.startswith("/"):
                return None
            path = request.path.split("?")[0]
            if path == PAIR_PATH:
                body = json.dumps({
                    "ok": True,
                    "plugin": "headless_browser",
                    "host": "127.0.0.1",
                    # ⚠️ 这里**故意**报一个错误端口：扩展必须采信"探测到的"
                    #    那个（它刚在这个端口上说上话），否则就会去连一个
                    #    根本不存在的端口 —— 那正是出过的 bug。
                    "port": state["reported_port"],
                    "token": TOKEN,
                    "ws_path": WS_PATH,
                    "instance": "测试实例",
                    "data_dir": "/tmp/kira_test",
                }, ensure_ascii=False).encode("utf-8")
                return Response(200, "OK", Headers({
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Access-Control-Allow-Origin": "*",
                }), body)
            return None

        # 场景 3 要在**另一个端口**上再起一次服务器，所以把这两个函数
        # 也放进 holder —— 否则 runner() 里拿不到（会 NameError）。
        # ⚠️ 必须放在**两个函数都定义完之后**：早一行就会 UnboundLocalError。
        holder["handler"] = handler
        holder["process_request"] = process_request

        for port in CANDIDATE:
            try:
                # ⚠️ `websockets.serve()` **必须 await**：它返回的是可等待对象，
                #    不 await 的话**不会真正开始监听**（表现为 connection refused）。
                #    （`_run_node` 才是那个不能 await 的 —— 它是同步函数。）
                server = await websockets.serve(
                    handler, "127.0.0.1", port, process_request=process_request)
            except OSError:
                continue
            holder["server"] = server
            holder["port"] = port
            return
        raise RuntimeError("候选端口全被占用，起不了测试服务器")

    async def runner():
        await main()
        port = holder.get("port")
        if not port:
            return

        results = {}
        bridge = holder["bridge"]

        # ⚠️ 桥的 connected 只在**扩展还连着的时候**才是 True，
        #    而 Node 客户端跑完就 process.exit 了（连接随之断掉）。
        #    所以要在它跑的过程中**采样**，不能在跑完之后才读 ——
        #    第一版就是这么误报的（读的时候连接已经没了）。
        async def _sample(out, stop):
            while not stop.is_set():
                if bridge.connected:
                    out.append(True)
                await asyncio.sleep(0.05)

        # ── 场景 0：**纯自动**（真·安装即用）──────────────────────────
        #    只加载模块、什么都不调 —— 顶层 bootstrap 自己应当完成
        #    "发现 → 配对 → 连上"。前面所有场景都显式调了
        #    `connect({manual:true})`，而 manual 会**强制清掉 userDisconnected
        #    闸门**再连，等于把自动路径的前置条件绕过去了 —— 那是假绿灯。
        _seen0, _stop0 = [], asyncio.Event()
        _task0 = asyncio.create_task(_sample(_seen0, _stop0))
        results["auto"] = await _run_node(node, client_path, {
            "action": "auto_only", "storage": {}, "settle_ms": 4000,
        })
        _stop0.set()
        _task0.cancel()
        results["auto_bridge_connected"] = bool(_seen0)
        await bridge.close()

        # ── 场景 1：全新安装（chrome.storage 全空）→ 自动发现 → 连上 ──
        state["reported_port"] = port          # 第一次：正常报端口
        _seen, _stop = [], asyncio.Event()
        _task = asyncio.create_task(_sample(_seen, _stop))
        results["fresh"] = await _run_node(node, client_path, {
            "action": "connect", "storage": {}, "settle_ms": 1500,
        })
        _stop.set()
        _task.cancel()
        results["fresh_bridge_connected"] = bool(_seen)
        # 断开，方便下一场景（方法名是 close，不是 disconnect）
        await bridge.close()

        # ── 场景 2：已有实例时点「自动检测」─────────────────────────────
        #    ⚠️ 这里要抓的是一个真实存在的坑：`applyPair()` 原来只写旧版
        #       单实例键（kb_host/kb_port/kb_token），**从不写 kb_instances**；
        #       而 `getConfig()` 只在 `kb_instances` **不是数组**时才做迁移。
        #       于是"已经有实例"的用户再配一个新的，新实例会被静默丢掉。
        #    ⚠️ 种子实例故意用**不同的端口**（9999）：如果和探测到的端口一样，
        #       "列表里有没有新实例"就区分不出来，这条会假过。
        state["reported_port"] = port
        results["second"] = await _run_node(node, client_path, {
            "action": "discover",
            "storage": {
                "kb_instances": [{"host": "127.0.0.1", "port": 9999,
                                  "token": "old-token", "label": "旧实例"}],
            },
            "settle_ms": 600,
        })

        # ── 场景 3：端口**不在候选列表**里 ─────────────────────────────
        #    用户问的就是这个："我的实例不是 5267，还算不算安装即用？"
        #    这里要证明两件事：
        #      ① 自动发现**确实探不到**（那不是 bug，是候选列表的边界）
        #      ② 但"面板带入"这条路能把非候选端口配上去并连上 ——
        #         也就是他从 127.0.0.1 打开一次插件面板就完事
        NONCAND = 7979                      # 故意不放进 CANDIDATE_PORTS

        # ⚠️ 顺序要紧：**先把服务器挪到非候选端口**，再去跑"自动发现" ——
        #    P7 的前提就是"候选端口上没有 KiraAI"。反过来的话服务器还在 5267，
        #    发现当然成功，P7 会误报（第一版就是写反了）。
        holder["server"].close()
        await holder["server"].wait_closed()
        state["reported_port"] = NONCAND
        server2 = await websockets.serve(
            holder["handler"], "127.0.0.1", NONCAND,
            process_request=holder["process_request"])

        results["noncand_discover"] = await _run_node(node, client_path, {
            "action": "discover", "storage": {}, "settle_ms": 400,
        })
        try:
            _seen2, _stop2 = [], asyncio.Event()
            _task2 = asyncio.create_task(_sample(_seen2, _stop2))
            results["panel"] = await _run_node(node, client_path, {
                "action": "page_pair", "storage": {}, "settle_ms": 1500,
                "payload": {"host": "127.0.0.1", "port": NONCAND,
                            "token": TOKEN, "instance": "main"},
            })
            _stop2.set()
            _task2.cancel()
            results["panel_bridge_connected"] = bool(_seen2)
        finally:
            server2.close()
            await server2.wait_closed()

        # ── 场景 4：**首次发现失败之后会不会再试** ──────────────────────
        #    这是用户实际撞到的那个死局：
        #      扩展装好那一刻 KiraAI 还没起来（或端口不常见）→ 发现失败 →
        #      什么都没记住 → 之后保活闹钟醒来时 `!instances.length` 直接
        #      return → **永远不会再试**。表现就是"扩展装了、也启用了，
        #      但一直未连接"，而 edge://extensions 里服务工作进程显示"(不活动)"
        #      （它确实醒来过，只是每次都原样睡回去了）。
        #    这里：先把退避状态设成"上次尝试是很久以前"，让 ensureAlive
        #    该重试；服务器在这一步是**开着**的（模拟"KiraAI 后来起来了"）。
        state["reported_port"] = port
        server4 = await websockets.serve(
            holder["handler"], "127.0.0.1", port,
            process_request=holder["process_request"])
        try:
            _seen4, _stop4 = [], asyncio.Event()
            _task4 = asyncio.create_task(_sample(_seen4, _stop4))
            results["retry"] = await _run_node(node, client_path, {
                "action": "alive", "settle_ms": 2500,
                "storage": {
                    # 模拟"首次发现失败过一次、而且已经过了退避时间"
                    "kb_discover_tries": 1, "kb_discover_at": 1,
                },
            })
            _stop4.set()
            _task4.cancel()
            results["retry_bridge_connected"] = bool(_seen4)
        finally:
            server4.close()
            await server4.wait_closed()

        holder["results"] = results

    try:
        asyncio.run(runner())
        results = holder.get("results") or {}
    except Exception as e:
        r.ok("P1 端到端可以跑起来", False, f"{type(e).__name__}: {e}")
        return
    finally:
        srv = holder.get("server")
        if srv is not None:
            try:
                srv.close()
                asyncio.run(srv.wait_closed())
            except Exception:
                pass
        try:
            client_path.unlink()
        except Exception:
            pass

    run_results(r, holder, results)


async def _run_node(node: str, script: Path, scenario: dict) -> dict:
    """跑一次扩展真代码。

    ⚠️ **必须用 asyncio 的子进程**，不能用 subprocess.run ——
       subprocess.run 是**阻塞**的，会把事件循环卡住，
       而此时测试服务器**还没法处理任何请求**（它就跑在同一个循环上）。
       表现是扩展探测全部超时、报"没有在本机找到 KiraAI 实例" ——
       看起来像扩展的 bug，其实是测试自己把服务器憋死了。
       （第一版就是这么误报的。）
    """
    import os
    env = dict(os.environ)
    env.update({"KIRA_PLUGIN_DIR": str(PLUGIN_DIR),
                "KB_SCENARIO": json.dumps(scenario)})
    proc = await asyncio.create_subprocess_exec(
        node, str(script),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=90)
    except asyncio.TimeoutError:
        proc.kill()
        return {"fatal": "扩展客户端 90 秒没结束（有多余定时器没退？）"}
    text = (out or b"").decode("utf-8", "replace")
    errt = (err or b"").decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith("RESULT:"):
            try:
                return json.loads(line[len("RESULT:"):])
            except Exception:
                pass
    return {"fatal": f"没有结果输出。stdout={text[-400:]} stderr={errt[-400:]}"}


def run_results(r, holder, results) -> None:
    port = holder.get("port")

    # ── 场景 0：**纯自动**（真·安装即用）────────────────────────────
    #    这条是这一组里最重要的：真实用户装上扩展后，**没有任何人**会去调
    #    `connect({manual:true})`。而必须靠顶层 bootstrap 自己完成
    #    "自动发现 → 配对 → 连上"。
    #    ⚠️ 之前的场景全都显式调了 `connect({manual:true})`，而 manual 会
    #       **强制清掉 userDisconnected 闸门**再连 —— 把自动路径的前置条件
    #       全绕过去了，属于假绿灯。这条补上。
    auto = results.get("auto") or {}
    if auto.get("fatal"):
        r.ok("P1 纯自动：装载扩展后无需任何调用即可连上", False, auto["fatal"][:300])
    else:
        a_st = auto.get("status") or {}
        r.ok("P1 纯自动路径：装载后无人调用 connect()，扩展自己完成发现→配对→连上",
             bool(a_st.get("connected")) and bool(results.get("auto_bridge_connected")),
             f"状态={a_st} 桥侧 connected={results.get('auto_bridge_connected')}")

    # ── 场景 1：全新安装 ──────────────────────────────────────────────
    fresh = results.get("fresh") or {}
    if fresh.get("fatal"):
        r.ok("P1 扩展能加载并跑起来（全新安装）", False, fresh["fatal"][:300])
        return

    inst = fresh.get("instances")
    status = fresh.get("status") or {}
    r.ok("P1 全新安装（storage 全空）时扩展不报错", not fresh.get("errors"),
         f"errors={fresh.get('errors')}")
    r.ok("P2 自动发现了实例并存进 kb_instances",
         isinstance(inst, list) and len(inst) == 1,
         f"instances={inst}")
    r.ok("P3 存的是**探测到的**端口（响应里报错端口也不会被带偏）",
         isinstance(inst, list) and len(inst) == 1
         and int(inst[0].get("port", 0)) == int(port or 0),
         f"存了 port={inst[0].get('port') if isinstance(inst, list) and inst else '?'}"
         f"，探测到的是 {port}")
    r.ok("P4 状态显示已连接", bool(status.get("connected")),
         f"status={status}")
    r.ok("P5 插件侧 bridge.connected 变成 True（=“装了扩展”的判据）",
         bool(results.get("fresh_bridge_connected")),
         "扩展连上了 WS 并完成握手后，桥的 connected 必须为真")

    # ── 场景 2：已有实例时再配一个 ────────────────────────────────────
    second = results.get("second") or {}
    if second.get("fatal"):
        r.ok("P6 已有实例时「自动检测」也能加上新实例", False,
             second["fatal"][:300])
        return
    inst2 = second.get("instances") or []
    r.ok("P6 已有实例时「自动检测」会把新实例加进列表（不是静默丢弃）",
         len(inst2) >= 1 and any(int(x.get("port", 0)) == int(port or 0)
                                 for x in inst2 if isinstance(x, dict)),
         f"探测到 {port}，但列表里是 {inst2}")

    # ── 场景 3：端口**不在候选列表**里 ────────────────────────────────
    #    回答"我的实例不是 5267，还算安装即用吗"。
    nc = results.get("noncand_discover") or {}
    nc_hit = nc.get("result") or {}
    if nc.get("fatal"):
        r.ok("P7 非候选端口下自动发现探不到（边界，不是 bug）", False, nc["fatal"][:200])
    else:
        # 这条**必须**是"探不到"——探到了说明候选列表被改过，P7 的前提就不成立
        r.ok("P7 非候选端口下自动发现探不到（候选列表的边界，非 bug）",
             not nc_hit.get("ok"),
             f"非候选端口竟被发现了：{str(nc_hit)[:160]}")

    pn = results.get("panel") or {}
    if pn.get("fatal"):
        r.ok("P8 面板带入能把非候选端口配上", False, pn["fatal"][:200])
    else:
        inst3 = pn.get("instances") or []
        port3 = next((x.get("port") for x in inst3
                      if isinstance(x, dict) and x.get("port")), None)
        r.ok("P8 面板带入（page_pair）能把非候选端口 7979 配上并连上",
             bool(pn.get("result", {}).get("ok")) and int(port3 or 0) == 7979
             and bool((pn.get("status") or {}).get("connected")),
             f"result={pn.get('result')} 存的端口={port3} 状态={pn.get('status')}")
        r.ok("P9 面板带入之后，插件侧 bridge.connected 也为真",
             bool(results.get("panel_bridge_connected")),
             "扩展完成 WS 握手才算真的连上")

    # ── P10：给用户看的提示，不能指到**不存在**的 UI 上 ──────────────
    #    ⚠️ 真实踩过的坑：扩展报"没有找到 KiraAI"时，让用户"去插件面板点
    #       「复制接入配置」" —— 但面板上**根本没有那个按钮**（它只做
    #       postMessage 自动配对），弹窗里也没有粘贴解析。照做找不到，
    #       等于把人指到死路上。这类文案没法靠"读起来对不对"发现，
    #       只能拿它提到的名字去 UI 里查。
    # ⚠️ 必须**先剥注释**：注释里往往会提到"以前那句错话"，
    #    不剥的话判据会被自己的注释命中（首跑就是这么误报的）。
    #    `strip_comments_only` 保留字符串字面量 —— 而"给用户看的字"
    #    全都住在字符串里，正是要看的部分。
    _bg = strip_comments_only(src_safe("browser-bridge/background.js"))
    _pop = strip_comments_only(src_safe("browser-bridge/popup.html"))
    _panel = strip_comments_only(src_safe("web/index.html"))
    _phantom = []
    for _name in ("复制接入配置",):
        if _name in _bg or _name in _pop:
            # 提到就必须真的存在（按钮/处理函数里能找到）
            if _name not in _panel and _name not in _pop:
                _phantom.append(_name)
    r.ok("P10 提示文案不引用不存在的 UI（如「复制接入配置」）", not _phantom,
         f"不存在的按钮={_phantom} —— 用户照着找不到")

    # ── P11：探不到时给的两条路**都真的存在** ────────────────────────
    _err = _bg[_bg.find("没有在本机找到 KiraAI 实例"):][:260] \
        if "没有在本机找到 KiraAI 实例" in _bg else ""
    # ── P12：插件侧引导文案不能停在**手动粘贴令牌**的老流程上 ─────────
    #    ⚠️ 老文案是"点扩展图标 → 把「接入令牌」粘进去 → 点连接" ——
    #       那是手动流程，早就不是主路径了（现在装完自己连；扫不到也能靠
    #       "打开面板即配对"）。照老文案做，用户会以为"必须手填令牌"，
    #       还跟扩展弹窗里写的"点「自动检测」即可，不用手填"**自相矛盾**。
    #    ⚠️ 要查**渲染出来的文本**，不是源码 —— `strip_comments_only` 是给
    #       JS 写的（认 // 和 /*），剥不掉 Python 的 `#` 注释，
    #       于是判据会被注释里"以前那句老话"命中（首跑就是这么误报的）。
    #       而渲染后的文本正是用户真正看到的东西，比对源码更贴近事实。
    _bad12 = []
    try:
        # runtime_behavior 已经用同一套桩把 setup_guide 载进 sys.modules 了
        from . import runtime_behavior as _rb
        _rb._load_plugin()
        _sg_mod = sys.modules.get("kirabrowser_rt.setup_guide")
        if _sg_mod is None:
            raise RuntimeError("setup_guide 没被加载进 sys.modules")
        _sg = _sg_mod.first_run_notice(
            PLUGIN_DIR, connected=False,
            browsers=[{"name": "edge", "path": "x"}]) or ""
    except Exception as _e:
        _sg = ""
        _bad12.append(f"渲染引导失败: {type(_e).__name__}: {_e}")
    if "粘进去" in _sg or "复制接入令牌" in _sg:
        _bad12.append("还在教用户手动粘令牌")
    if "打开就会自动配对" not in _sg:
        _bad12.append("没提'打开面板即自动配对'这条主路径")
    if "自动检测" not in _sg:
        _bad12.append("没提弹窗的「自动检测」")
    r.ok("P12 安装引导与当前流程一致（不是老的手动粘贴流程）", not _bad12,
         f"问题={_bad12 or '无'}")

    # ── P13：扩展弹窗要显示版本号（排查"你装的是哪一版"）────────────
    r.ok("P13 扩展弹窗显示版本号",
         'id="ver"' in src_safe("browser-bridge/popup.html")
         and "getManifest().version" in src_safe("browser-bridge/popup.js"),
         "没有版本号的话，用户报问题时分不清是新版还是没更新")

    # ── P14b：发现失败时要把"试过哪些端口"带出来（否则用户只能干猜）──
    #    最常见的失败原因就是"KiraAI 用了候选列表之外的端口"，
    #    把试过的端口列出来，用户一眼就知道该去填端口。
    _nc = nc.get("result") or {}
    r.ok("P14b 发现失败时带回 triedPorts（让用户看得出是不是端口不在列表里）",
         isinstance(_nc.get("triedPorts"), list) and len(_nc.get("triedPorts") or []) > 0,
         f"triedPorts={_nc.get('triedPorts')}")
    r.ok("P14c 弹窗会把试过的端口显示出来",
         'triedPorts' in src_safe("browser-bridge/popup.js")
         and "webui.json" in src_safe("browser-bridge/popup.js"),
         "要顺便告诉用户去哪儿看真实端口（data/webui.json 的 port）")

    # ── P14：保活路径里，没有实例时也要**主动重试发现** ──────────────
    #    ⚠️ 先说清楚**旧代码到底哪里有问题**，免得判据名不副实：
    #       顶层 bootstrap 在每次 SW 唤醒时都会重跑，里面也会 connect() →
    #       discover()，所以"重试"其实一直有 ✗ 不存在"永远不再试"。
    #       旧代码真正的毛病是**节奏**：每次 30 秒闹钟都把 15 个候选端口
    #       扫一遍，"这台机器上根本没有 KiraAI"时就是一直白扫。
    #       这条判据验的是：保活路径自己也能（按退避）重试并连上。
    rt = results.get("retry") or {}
    if rt.get("fatal"):
        r.ok("P14 首次发现失败后，后台还会再试（不是死局）", False, rt["fatal"][:300])
    else:
        rt_st = rt.get("status") or {}
        r.ok("P14 保活路径在没有实例时会按退避重试发现并连上",
             bool(rt_st.get("connected")) and bool(results.get("retry_bridge_connected")),
             f"状态={rt_st} 桥侧={results.get('retry_bridge_connected')}")

    r.ok("P11 探不到时给的指引和真实功能对得上",
         "插件面板" in _err and "手动填" in _err,
         f"当前指引={_err[:150]}")
