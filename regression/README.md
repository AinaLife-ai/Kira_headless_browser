# 回归测试

这个目录是插件的**回归测试套件**。改动插件后跑一遍，能挡住大部分"改一处坏一处"。

```bash
python3 regression/run_all.py            # 跑全部
python3 regression/run_all.py 安全        # 只跑名称含「安全」的
python3 regression/run_all.py --list     # 看有哪些检查

# 对别的副本跑同一套（比如对比修改前后）
KIRA_PLUGIN_DIR=/path/to/other-copy python3 regression/run_all.py
```

退出码：全过 `0`，有失败 `1`（可直接接进 CI）。

---

## 为什么要有它

这个项目历史上踩过的坑，基本都属于"改完看着没问题、跑起来就炸"：

| 事故 | 症状 | 现在被哪条挡住 |
|---|---|---|
| **ES 模块之间不共享作用域** | `capabilities.js` 调用 `background.js` 的函数 → 运行时 ReferenceError，一大批命令全废 | `静态一致性审计 A13` |
| **MV3 里用了 `FileReader`** | Service Worker 没有这个 DOM API → 每次上传第一步就挂 | `静态一致性审计 C15` |
| **schema 加了配置项但代码不读** | 用户改了没反应 / 甚至 `AttributeError` | `静态一致性审计 A1` |
| **两个后端接口不对称** | 同一工具随路由切换而行为不同 | `静态一致性审计 A5` / `工具合并 C3/C4` |
| **工具名重复** | 框架静默覆盖，模型调错 | `静态一致性审计 A11` |
| **反向 CPU 参数被加回来** | 后台标签不再降频，CPU 飙升 | `静态一致性审计 C1` / `运行时 R6/R7` |
| **`plugin_id` 被改** | 升级后出现两个插件、工具冲突 | `静态一致性审计 E3` |
| **点击派发两次** | 下单/发帖重复提交 | `扩展点击行为 D` |
| **下载把整份文件攒内存** | 大文件占满内存 | `扩展桥 E6` |
| **配置项没接线**（`require_confirm` 从不下发） | 用户开了没反应 | `接线完整性 B1/B2` |
| **渲染层丢数据**（`cookie_get` 落到默认分支） | 导出 cookie 回一句「完成」，数据没了 | `接线完整性 A1/A2` |
| **用户拒绝被当成失败** | 扩展拒绝后路由换后端把事做了 | `接线完整性 E1–E4` |
| **并发调工具互相踩** | `goto` 没完就 click 同一张页面 | `运行时 R9` |
| **`scroll` 报成功但没动** | smooth 是异步的，截图拿到旧位置 | `运行时 R10` |
| **扩展 async 回调没兜异常** | unhandled rejection 会让 worker 掉线 | `接线完整性 F1/F2` |
| **两后端返回字段不一致** | 同一工具随路由切换给出不同形状的结果 | `返回契约一致性 F1` |
| **方法被调用但没定义** | 截图/文件发送直接 `AttributeError`，而语法检查全绿 | `调用图完整性 A1` |
| **面板 API 路径用了旧插件 id** | 面板 404，令牌读不出来 | `静态一致性 A14` |

---

## 目录结构

```
regression/
├── run_all.py              入口（跑全部 / 筛选）
├── harness.py              共享夹具：路径、桩注入、断言收集、常用解析
├── checks/                 各检查脚本（每个都独立，互不依赖）
│   ├── static_audit.py     静态一致性 / README / 历史回归 / 运行时坑 / 打包
│   ├── tool_merge.py       工具合并零丢失 + 协议两端一致
│   ├── security_rules.py   域名匹配 / 本机地址识别（真实用例表）
│   ├── file_hygiene.py     文件冗余与缺失清点
│   ├── runtime_behavior.py 生命周期 / 路由 / 内存（假 Playwright）
│   ├── bridge_e2e.py       真实 WebSocket 端到端
│   ├── content_dom.py      扩展点击行为（真实 DOM）
│   ├── wiring.py           接线完整性（配置接通 / 数据透传）
│   ├── contract.py         两后端返回契约一致性
│   └── callgraph.py        调用图完整性（未定义方法 / 签名合规）
├── stubs/                  让插件能被 import 的最小替身（不需要真的 KiraAI）
│   ├── core/               框架接口的最小实现
│   └── playwright/         **语义忠实的**假 Playwright（见下）
└── js/                     DOM 检查用的 JS 依赖（jsdom）
```

---

## 设计要点

### 1. 假 Playwright 是「语义忠实」的，不是空壳

`stubs/playwright/` 不是随便返回 `Mock` —— 它模拟了真正影响行为的部分：

- `context.pages` 是**活列表**（新页面会 append）
- 打开弹窗会**真的派发** `context.on("page")` 事件
- 页面可以被"用户关掉"（`_die_by_itself()`），之后所有操作抛和真实 Playwright 一样的错
- 每次创建/关闭都有计数（`STATS`），方便断言泄漏

所以「页面失效后能不能自愈」「弹窗会不会堆积」这类问题能**真断言**，
而不需要装 Chromium（沙箱里装不了）。

### 2. 依赖缺失时跳过而不是失败

`websockets` 和 `jsdom` 是可选依赖。没装就 `WARN` 跳过，不算失败 ——
这样别人 clone 下来不装任何东西也能跑通大部分检查。

```bash
pip install websockets                            # 端到端检查
cd regression/js && npm install                    # DOM 检查
```

### 3. 断言的是「期望行为」，不是「当前行为」

比如 `runtime_behavior` 里的 R1 断言的是"页面被关掉后**应该**自愈"。
如果哪天这个能力被改坏了，检查会红 —— 而不是跟着代码一起"变绿"。

---

## 怎么加一个新检查

1. 在 `checks/` 下新建模块：

```python
from ..harness import section, src

TITLE = "我的新检查"

def run(r) -> None:
    section("A. 第一部分")
    r.ok("A1 某件事成立", condition, "细节")
    r.metric("某个指标", value)      # 可选：数值会汇总在报告里
```

2. 在 `checks/__init__.py` 的 `ALL_CHECKS` 里登记一行。

3. `python3 regression/run_all.py 我的新检查` 单独跑它。

可用工具（都在 `harness.py` 里）：

| 函数 | 用途 |
|---|---|
| `src(rel)` / `main_src()` / `headless_src()` … | 读插件文件 |
| `schema()` / `manifest()` / `ext_manifest()` | 读 JSON |
| `tool_names(text)` | 抽注册的工具名 |
| `backend_methods(text)` | 抽后端的**能力接口**方法 |
| `called_backend_methods(main_text)` | 抽插件调用的后端方法 |
| `load_module(...)` | 按包名加载插件模块（处理相对导入） |
| `fake_playwright()` | 拿假 Playwright 的 STATS |
| `Report.ok/warn/metric/note` | 记断言 / 提示 / 指标 |

---

## 已知限制

- **装不了 Chromium**：CPU 实际降幅、`userScripts` 的 `world` 参数差异、
  扩展 fetch 带 Cookie 能否拿到登录态资源 —— 这几项需要真机验证，
  套件里覆盖不到。
- **端到端用的是模拟扩展**：`bridge_e2e` 里对面是一个按协议实现的 Node 客户端，
  传输层是真的，但"点击有没有真的点到按钮"这类要真浏览器才能确认
  （`content_dom` 用 jsdom 补上了 DOM 层面的验证）。
