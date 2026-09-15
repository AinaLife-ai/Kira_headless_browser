# 浏览器插件 (Browser Plugin) 2.1.3

> 让 KiraAI 拥有**完全真实、全能**的浏览器操作能力。

一个插件，**两个能力完全对称的后端**，自动挑选：

```
                ┌──────────────────────────────────────┐
                │  BrowserPlugin（12 个动作式工具）      │
                └───────────────┬──────────────────────┘
                                │  BackendRouter（自动挑）
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
    扩展桥（你自己的浏览器）            无头后端（插件自己的浏览器）
    · 扩展主动连出，零冲突              · Playwright 拉起，独立 profile
    · 登录态天然可用                    · 空闲自动关闭，释放 CPU/内存
                 └──── 没连上就自动回退 ─────┘
```

**为什么这么设计**：原版无头插件默认去接管你真实浏览器的数据目录，而 Playwright
会**独占**这个目录（ProcessSingleton 锁）——结果就是**你开着浏览器插件用不了，
插件开着你的浏览器打不开**。现在两条路彻底分开：扩展桥**根本不启动浏览器**
（用的是你已经开的那个），无头后端**只用自己的 profile**。

---

## 功能特性

- 🖥️ **用你自己的浏览器**：装配套扩展后，直接操作用户眼前那个浏览器，登录态、
  已打开的标签页全都可用，**且不需要用户关闭浏览器**
- 🧬 **真实数据可选继承**：无头后端可以把真实浏览器的用户数据**复制**一份来用
  （登录态/Cookie/书签都在），因为是副本所以**不占用**原目录
- 🧠 **两个后端能力完全对称**：读/点击/输入/执行 JS/上传/下载/Cookie/键盘鼠标，两边都有
- 🛡️ **CPU / 内存防护**：保留 Chromium 的后台节流、补齐防崩溃参数、空闲自动关闭
- 🔁 **页面自愈**：标签页被关掉或页面崩溃后自动恢复，不会永久卡死
- 🪟 **有头 / 无头都支持**：可视模式下窗口参数齐备
- 📄 **内容可分页续读**：单次返回长度不是硬上限，AI 自己能决定读多少
- 🍪 **Cookie 跨后端打通**：一键把浏览器的登录态带到无头后端
- ⚙️ **动作式工具接口**：12 个工具名覆盖 30+ 种能力，模型不用记一堆近义名字

---

## 安装

### 1. 安装插件

把本仓库整个目录放到 KiraAI 的 `data/plugins/` 下（或从 WebUI 上传 zip）。

依赖会自动装（`requirements.txt`：`playwright` + `aiohttp`）。浏览器本体也会在
需要时自动下载（见下方「浏览器来源」）。

### 2. 装浏览器扩展（**推荐**，不装也能用）

扩展已经**随插件打包**在 `browser-bridge/` 目录里，不用另外下载。

不装扩展也能正常使用（走无头后端），**装了之后 AI 才能直接操作你正在用的浏览器**。

插件首次用到浏览器工具时，会**自动把下面的引导交给 AI**，让它转述给你——
你不需要自己去翻插件目录：

```
📁 扩展就在插件目录里：<插件目录>/browser-bridge

📋 安装步骤：
  1. 在 Chrome 地址栏打开：chrome://extensions
  2. 打开右上角的「开发者模式 / Developer mode」开关
  3. 点「加载已解压的扩展程序 / Load unpacked」
  4. 选择 <插件目录>/browser-bridge
  5. 点扩展图标，把「接入令牌」粘进去，点连接
```

也可以随时问 AI：「**怎么让你看我现在的浏览器？**」→ 它会调用
`browser_extension_help` 给出当前机器上检测到的浏览器、对应地址、绝对路径和令牌。

#### 支持的浏览器

| 浏览器 | 支持 | 说明 |
|---|---|---|
| Chrome | ✅ 116+（**执行 JS 需 120+**） | MV3 本身 116 起可用；`chrome.userScripts` 从 120 起提供，所以 `browser_script` 在 116–119 上不可用 |
| Edge | ✅ 116+（**执行 JS 需 120+**） | 同为 Chromium 内核，扩展机制一致；打开的是 `edge://extensions` |
| Brave / Vivaldi / Opera | ✅ | Chromium 系，`chrome.*` API 一致 |
| Firefox | ❌ | Firefox 的 MV3 用 event page，**不接受** `background.service_worker` |
| Safari | ❌ | 扩展格式完全不同 |

#### 为什么不能自动安装扩展

Chromium 没有给本地程序留"静默安装扩展"的正规接口：

- `--load-extension` 命令行参数只对**我们自己拉起的那个 Chromium 实例**有效，
  管不到用户已经在用的浏览器；且 Google 从 2025 起在收紧它。
- `ExtensionInstallForcelist` 企业策略确实能静默安装，但要改注册表/组策略——
  那是管理员操作，插件不该去动用户的系统。

所以这里选择了「**把它做到一键可复制**」：扩展随插件打包 + 首次运行主动告诉你在哪、
怎么装、令牌是什么。**不会去做看起来能自动装、实际偷偷改你注册表的事。**

---

## 浏览器来源与后端策略

### `backend_strategy`（默认 `auto`）

| 值 | 行为 |
|---|---|
| `auto` | 优先扩展桥（你自己的浏览器）；扩展没连上就自动回退到无头后端 |
| `extension` | **只用**你的浏览器，连不上就报错（不偷偷降级） |
| `headless` | **只用**插件自己的无头浏览器 |

> 为什么默认 `auto`：装了扩展就享受真实登录态，没装也照样能用。
> 而无论哪条路，无头后端都不会碰你的真实 profile。

### 无头后端的浏览器来源（`browser_channel`）

按优先级依次尝试，全部失败会自动下载内置 Chromium：

1. `auto` —— 系统默认浏览器 → Chrome → Edge → Chromium → 内置
2. `chrome` / `msedge` / `chromium` —— 指定某一个
3. `bundled` —— 只用 Playwright 自带的 Chromium

### 无头后端的 profile 模式（`headless_profile_mode`，默认 `inherit`）

| 值 | 行为 |
|---|---|
| `inherit` | **复制**你真实浏览器的用户数据到插件目录，跑在那个副本上 |
| `persistent` | 插件自己的 profile（与真实浏览器无关） |
| `temp` | 临时目录，用完即弃 |

**关于 `inherit`**：登录态、Cookie、书签全都在。为什么复制出来的 Cookie 还能用？
Chrome 80+ 的 Cookie 用 AES-GCM 加密，密钥由 DPAPI（Windows）/ Keychain（macOS）/
OSCrypt（Linux）包裹，这三者都是**用户级、与路径无关**的——同一台机器同一个用户，
副本照样能解密。

复制时会跳过 `Cache` / `GPUCache` / `Service Worker` 等大目录（否则要复制好几个 GB），
并删掉副本里的 `SingletonLock` 等锁文件（否则 Playwright 会以为"profile 正在运行"）。

> ⚠️ 与旧版的区别：旧版那个「直接指向真实目录」的开关（v1.x 的 `use-real-browser-profile`，已移除）
是**直接指向**真实目录，
> 会持有锁、导致你和插件互相排斥。现在改为复制，**你自己的浏览器照常能用**。

---

## 配置说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | switch | `true` | 是否启用插件 |
| `backend_strategy` | enum | `auto` | 后端策略：`auto`/`extension`/`headless` |
| `extension_enabled` | switch | `true` | 是否启用扩展桥后端 |
| `headless_enabled` | switch | `true` | 是否允许回退到无头后端 |
| `read_only` | switch | `false` | 只读模式：打开后写工具会从模型可见的工具表里整个摘掉 |
| `require_confirm` | switch | `false` | 写操作前在浏览器弹通知，需用户点「允许」（仅扩展桥支持） |
| `allowed_domains` | list | `[]` | 域名白名单。留空=不限制写操作；填写后写操作只允许命中规则的域名 |
| `blocked_domains` | list | `["127.0.0.1","localhost","*.bank*","*.pay*"]` | 域名黑名单，读写都拦 |
| `max_content_chars` | integer | `8000` | 单次返回正文的字符上限（配合 `content_page_size` 使用） |
| `content_page_size` | integer | `8000` | `browser_page` 单次默认返回多少字符。**不是硬上限**，可用 offset 续读 |
| `inject_page_state` | switch | `true` | 是否把当前浏览状态注入提示词 |
| `panel_auth_required` | switch | `true` | 配置面板需要登录 |
| `command_timeout` | number | `20` | 扩展命令超时（秒）。开了二次确认会自动抬高 |
| `headless` | switch | `true` | 无头模式；关闭则显示窗口 |
| `browser_channel` | enum | `auto` | 浏览器来源（见上） |
| `headless_profile_mode` | enum | `inherit` | profile 模式（见上） |
| `custom_user_data_dir` | string | - | 自定义 profile 目录。⚠️ **不要填你真实浏览器的 User Data**，那会导致互相抢锁 |
| `default_viewport` | string | `1920x1080` | 视口大小（无头模式生效） |
| `user_agent` | string | - | 自定义 User-Agent |
| `timeout` | integer | `45` | 页面加载超时（秒） |
| `default_wait_until` | enum | `domcontentloaded` | 等待策略。**不推荐** `networkidle`：现代页面有轮询/长连接，可能永远不空闲，只会白等到超时、期间还在烧 CPU |
| `op_timeout` | integer | `120` | 页面操作超时（秒）。**默认与框架工具超时脱钩**——框架是到点直接取消我们的协程，会留下状态不明的页面 |
| `op_timeout_follows_framework` | switch | `false` | 打开后把 `op_timeout` 限制在「框架工具超时 × 比例」内 |
| `op_timeout_ratio` | number | `0.8` | 仅在上项打开时生效 |
| `action_timeout` | integer | `20` | 点击/输入等交互的超时（秒） |
| `idle_close_seconds` | integer | `300` | 空闲这么久后自动关闭无头浏览器，释放 CPU 和内存。`0`=不关 |
| `screenshot_dir` | string | 插件数据目录/screenshots | 截图保存路径 |
| `download_dir` | string | 插件数据目录/downloads | 下载保存路径 |
| `screenshot_max_count` | integer | `50` | 截图最多保留张数（元素截图也计入清理） |
| `screenshot_auto_clean` | switch | `true` | 自动清理旧截图 |
| `download_auto_clean` | switch | `true` | 自动清理下载目录 |
| `download_max_count` | integer | `100` | 下载目录最多保留文件数 |
| `download_max_bytes` | integer | `2147483648` | 单个下载大小上限（2GB）。下载是**流式落盘**，无论多大都不占内存 |
| `upload_max_bytes` | integer | `209715200` | 上传大小上限（200MB）。内容要经 WebSocket 传输，别设太大 |

---

## 可用工具（12 个）

工具按「**动作 + 传参**」组织：同类操作合并成一个工具，用 `action` / `mode` 选做什么。
能力一个没少，但模型不用在一堆近义名字里挑。

### 📄 `browser_page` —— 读页面

| 参数 | 说明 |
|---|---|
| `mode` | `info`（只要标题网址）· `text`（正文，默认）· `outline`（标题结构+可交互元素+链接）· `html`（原始 HTML）· `selector`（取某个选择器内文本）· `extract`（抽多条结构化数据） |
| `selector` | `mode=selector/extract` 时的 CSS 选择器 |
| `attr` | `mode=extract`：取这个属性（如 `href`），省略取文本 |
| `limit` | `mode=extract`：最多几条，默认 50 |
| `offset` | 从正文第几个字符开始读（长页面续读用） |
| `max_chars` | 本次最多返回多少字符 |

> **内容不受长度限制**：单次返回 8000 字符只是「一次给多少」。返回值里会带
> `has_more` / `next_offset` / `total_chars`，AI 看到「还有 26512 字符未读」
> 就能自己带 `offset` 接着读。

### 🖱️ `browser_interact` —— 所有交互（18 个动作）

用 `action` 选：

| 分类 | action |
|---|---|
| 元素类 | `click` · `fill` · `type` · `hover` · `scroll` · `upload` |
| 导航类 | `go_back` · `refresh` |
| 键盘 | `key_press` · `key_down` · `key_up` · `key_type` |
| 鼠标 | `mouse_click` · `mouse_move` · `mouse_down` · `mouse_up` · `mouse_wheel` · `mouse_drag` |

常用参数：`selector` / `text` / `index`（定位）、`value`（填写内容）、
`key`（按键，如 `Control+a`）、`direction`、`file_path`、`x`/`y`。

### 🌐 `browser_navigate` —— 打开网址
`url`、`new_tab`

### 📑 `browser_tabs` —— 列出所有标签页

### 📸 `browser_screenshot` —— 截图
`full_page`、`selector`（只截某元素）、`send`（是否发给用户，默认 true）

### ⏱️ `browser_wait` —— 等待
给了 `selector`/`text` 就等它们出现（更准）；否则等 `seconds` 秒。

### ⚡ `browser_script` —— 执行 JavaScript
`script`。返回表达式的结果。

> 扩展桥后端执行任意 JS 需要用户在扩展详情页打开「**允许用户脚本 / Allow User Scripts**」
> 开关（Chrome 138+ 的安全要求）。这是 Chrome 强制的，扩展无法代劳——
> 插件会把这个情况翻译成一句能照做的话。**只有这一个能力需要该开关**。

### 📁 `browser_file` —— 文件
| mode | 说明 |
|---|---|
| `download` | 下载 URL 到本地并发给用户（**会带上浏览器的登录态**） |
| `list` | 列出已下载/截图目录里的文件 |

上传文件用 `browser_interact(action="upload", selector=..., file_path=...)`。

### 🍪 `browser_cookie` —— 打通登录态
| action | 说明 |
|---|---|
| `export` | 导出当前站点（或指定 url）的 cookie |
| `import` | 把 cookie 写进当前后端 |

典型用法：先从扩展桥导出，再写入无头后端——这样降级到无头时也不用重新登录。

### 🔧 `browser_debug` —— 后端状态
当前用哪个后端、profile 模式、超时设置、空闲多久、开着几张页面。排障用。

### 🖥️ `browser_test_visible` —— 确认窗口可见
打开一个测试页，用来确认可视模式下浏览器窗口是否真的显示出来了。

### 🔌 `browser_extension_help` —— 扩展安装引导
给出扩展的绝对路径、按浏览器区分的安装步骤、接入令牌、兼容性说明。

---

## 两个后端的能力对照

| 能力 | 扩展桥 | 无头 |
|---|---|---|
| 读页面 / 提取 / 列表签 | ✅ | ✅ |
| 跳转 / 点击 / 输入 / 滚动 | ✅ | ✅ |
| 截图 | ✅ | ✅ |
| 执行 JavaScript | ✅（需开 userScripts 开关） | ✅ |
| 上传文件 | ✅ | ✅ |
| 下载（带登录态） | ✅ | ✅ |
| Cookie 导出 / 写入 | ✅ | ✅ |
| 键盘 / 鼠标精细控制 | ✅ | ✅ |
| 返回 / 刷新 | ✅ | ✅ |
| 用你现有的登录态 | ✅ 天然 | ✅（`inherit` 复制） |

**两个后端接口完全对称** —— 换后端不丢能力，只是"在谁的浏览器里做"不同。

> 唯一的版本例外：**执行 JavaScript** 依赖 `chrome.userScripts`（Chrome/Edge 120+）。
> 在 116–119 上该能力不可用，插件会明确告诉你原因，其它能力不受影响。

---

## 使用示例

### 示例 1：看用户正在浏览的页面

```
（用户）"帮我看看我当前这个页面"
→ browser_page(mode="outline")     # 先看结构，找下一步点哪里
→ browser_page(mode="text")        # 再读正文
```

### 示例 2：搜索并点进结果

```
browser_navigate(url="https://www.baidu.com")
browser_interact(action="fill", selector="#kw", value="Python 教程")
browser_interact(action="key_press", key="Enter")
browser_wait(selector="#content_left", timeout=10)
browser_page(mode="outline")
browser_interact(action="click", text="Python 官方教程")
```

### 示例 3：长文档分页读完

```
r = browser_page(mode="text")                  # 返回 has_more=true, next_offset=8000
r = browser_page(mode="text", offset=8000)     # 接着读
r = browser_page(mode="text", offset=16000)
```

### 示例 4：下载需要登录的文件

```
browser_file(mode="download", url="https://example.com/private/report.pdf")
# 扩展桥会用你浏览器的会话去抓 —— 登录态资源也能下
```

### 示例 5：降级时不丢登录态

```
browser_cookie(action="export")                          # 从浏览器导出
browser_cookie(action="import", cookies=[...])           # 写进无头后端
```

### 示例 6：上传文件

```
browser_interact(action="upload", selector="input[type=file]",
                 file_path="/path/to/resume.pdf")
```

### 示例 7：鼠标拖拽（滑块 / 拖动排序）

```
browser_interact(action="mouse_drag",
                 start_x=100, start_y=200, end_x=400, end_y=200, steps=15)
```

### 示例 8：执行 JS 取数据

```
browser_script(script="[...document.querySelectorAll('.item a')].map(a => a.href)")
```

---

## 常见问题

### 为什么装了扩展之后，我的浏览器还能正常开？

因为扩展桥这条路**根本不启动浏览器** —— 它让扩展作为 WebSocket 客户端主动连出，
插件借用的是**你已经开着的那个**浏览器。没有第二个进程，也就没有目录占用。

（旧版会**直接指向**你的 User Data 目录，Playwright 一启动就持有
`ProcessSingleton` 锁，于是你和插件互相排斥。现在改成复制副本。）

### 执行 JavaScript 报「需要打开 Allow User Scripts」？

这是 Chrome 138+ 的安全要求。打开 `chrome://extensions` → 找到
Kira Browser Bridge → 详情 → 打开「允许用户脚本」。扩展无法代劳。

**只有执行 JS 需要这个开关**，其它能力（点击/输入/上传/下载/读页面）都不受影响。

### 页面看起来没问题，但工具一直报「页面已关闭」？

这是旧版的老问题：用户关掉标签页后插件不会自愈，会永久失败。现在已经有
页面自愈（`_page_alive` / `_ensure_page`）——原页面失效时会自动换一张，
连错两次直接重建浏览器。

### CPU 占用高？

三个已知原因，都在 2.x 里修了：

1. **旧版主动关掉了 Chromium 的后台节流**（`--disable-background-timer-throttling`
   等三个参数）。Chromium M87 起后台标签的 JS 定时器被节流到每分钟一次，
   官方称**CPU 最多降低 5 倍**——而 bot 的浏览器窗口常年被挡在后面，正好全命中。
   现在这三个参数**已从代码里删除**，并有双重过滤防止被重新塞回。
2. **缺少防崩溃参数**：`/dev/shm` 写满会让渲染进程崩溃、反复重载，
   于是 CPU 飙升。现在补齐了 `--disable-dev-shm-usage`、`--disable-gpu`、
   `--js-flags=--max-old-space-size=512`、`--renderer-process-limit=N`。
3. **默认等待 `networkidle`**：Playwright 官方标注 **DISCOURAGED**，
   现代页面可能永远不空闲，导致每次都硬等到超时、期间页面还在跑。
   现在默认是 `domcontentloaded`。

另外 `idle_close_seconds`（默认 300）会在空闲后自动关掉无头浏览器，
把 CPU 和内存一起释放。

### 磁盘占用一直在涨？

旧版有三个泄漏点，都已修：

- 元素截图存的是 `element_*.png`，而清理只扫 `screenshot_*.png`
  → 元素截图**一张都不会被清理**。现在两类一起清理。
- 下载目录完全没有清理机制 → 新增自动清理（保留最近 `download_max_count` 个）。
- 页面开出的弹窗（`target=_blank`）无人回收，会在后台堆积
  → 现在注册了 `context.on("page")`，非插件页面立即关闭。

### 下载大文件会撑爆内存吗？

不会。下载是**流式落盘**（64KB 一块），无论文件多大内存曲线都是平的。
`download_max_bytes`（默认 2GB）只用来拦住异常的超大文件。

### 页面内容太长，AI 只能看到一部分？

不是。单次返回长度（`content_page_size`，默认 8000 字符）只是「一次给多少」，
不是「最多能看到多少」。返回值会告诉 AI 还有多少没读、从哪个 offset 接着读，
它可以自己续读到底，也可以用 `browser_page(mode="extract")` 只取要的那部分。

### 工具调用总是卡在 60 秒然后失败？

那是框架的 `tool_call_timeout`（默认 60s）。它是**直接取消**我们的协程，
会让 Playwright 留下状态不明的页面。所以本插件的 `op_timeout`（默认 120s）
**默认与它脱钩**——由插件自己决定何时收手。如果你想让它不超过框架上限，
打开 `op_timeout_follows_framework`。

---

## 故障排除

### `ImportError: playwright`

```bash
pip install playwright aiohttp
```

插件带 `requirements.txt`，重新安装插件会自动执行。

### 浏览器启动失败 / 找不到浏览器

用 `browser_debug` 看当前状态。会自动依次尝试
系统默认浏览器 → Chrome → Edge → Chromium → 内置下载。
网络不通时内置下载会超时，可以手动执行：

```bash
python -m playwright install chromium
```

### 可视模式下看不到窗口

1. 先用 `browser_test_visible` 打开测试页
2. 用 `browser_debug` 看 `headless` 是不是 `false`
3. 检查窗口是否被其它窗口挡住（Windows 上会 `--start-maximized`）

### 扩展连不上

1. 确认扩展图标不是灰色（点一下看状态）
2. 确认令牌是最新的（插件面板 → 复制接入令牌）
3. 确认 KiraAI 的 WebUI 端口和扩展里填的一致
4. 用 `browser_debug` 看 `bridge.connected`

---

## 更新日志

### v2.1.3（2026-09-15）

**全量通读代码修出的 2 个严重问题**（都是"语法没问题、跑起来才炸"）：

- 🔴 **`_send_image` / `_send_file` 被调用但从未定义**：这两个方法在之前的
  工具层重构中丢了，只剩调用点 —— 截图发送、文件发送会直接
  `AttributeError` 崩掉。而所有语法检查（`py_compile`）和既有回归检查**全绿**，
  因为它们只看"名字有没有出现"，不看"定义有没有存在"。
  → 已补回，并新增检查组：**调用图完整性**（扫描 `self.xxx()` 是否有定义）。
- 🔴 **配置面板的 API 路径是旧的插件 id**：面板写死
  `/api/plugin/kira_browser_bridge`，而插件 id 早已是 `headless_browser`
  （框架把插件 API 挂在 `/api/plugin/{plugin_id}/...`）。
  → 结果就是面板一打开「令牌读取失败」，重载也不会有反应。
  → 已修正，并新增检查：**硬编码的插件 id 必须与 manifest 一致**
  （面板 API / WS 路径 / 扩展路径都依赖它）。

另外确认了**框架契约合规**：工具签名 `(self, event, …)`、hook 签名
`(self, event, req, …)`、`event.sid` 取会话标识 —— 与框架内置插件的写法一致。

### v2.1.2（2026-09-15）

**两后端返回契约完全对齐**（真正可互换）。

- 🔴 **两个后端返回的字段不一致（22 处差异）**：同一个工具因为路由到不同后端，
  返回的字段形状不同 —— 模型看到的信息时有时无，这类问题极难排查
  （"昨天还能读出标题，今天不行了"）。
  已逐条对齐：`click` 补 `navigated`/`changed`/`match`，`navigate` 补 `title`/`tab_id`，
  `type_text` 补 `submitted`，`upload_file` 补 `name`/`size`，
  `download` 补 `url`/`mime`，`cookie_set` 统一 `written`/`skipped`，
  `list_tabs` 补 `tab_count`，`execute_js`/`hover`/键盘鼠标补 `url`，
  `scroll`/`mouse_wheel` 去掉多余字段……
  **现在两个后端可以任意互换而模型无感。**
- ✅ 新增回归检查组：**两后端返回契约一致性**（真的把两个后端都调一遍，
  收集实际返回的字段，与渲染层要读的字段比对）

### v2.1.1（2026-09-15）

**全量复查修出的 5 个问题**（都是"看着实现了、实际不生效"那类）：

- 🔴 **二次确认完全没生效**：`require_confirm` 配置项存在、扩展也实现了，
  但**插件从不把开关下发给扩展** → 打开了也没有任何反应。
  现在所有写命令自动带上 `require_confirm` 与 `confirm_timeout`。
- 🔴 **用户拒绝后仍会执行**：扩展侧用户点了「拒绝」，却被当成"执行失败"，
  路由于是**换到无头后端把同一件事做了** —— 用户以为自己拦住了，其实没有。
  现在 `OpResult` 区分「被拒绝」与「失败」，拒绝即终止，绝不重试。
- 🟠 **`browser_cookie(export)` 返回空**：导出的是**数据**，但渲染层没处理它，
  落到默认分支只回一句「✅ 完成」，cookie 被静默丢掉。
  另外 15 个（键盘/鼠标/返回/刷新/文件列表/上传…）也是同样问题，一并补全。
- 🟠 **并发工具调用会互相踩**：`asyncio.Lock` 只保护"启动浏览器"，
  不保护页面操作。模型一轮里并发调 `navigate` + `click` 时，
  `goto` 还没完成就会有人在同一张页面上 click。现在页面操作有独立互斥锁。
- 🟠 **扩展 `scroll` 报"已滚动"但页面还没动**：用了 `behavior: "smooth"`
  （异步动画），函数立刻返回而页面还在慢慢滚 —— 紧接着截图会拿到旧位置；
  而且无条件回 `changed: true`，即使已经在底部也报告"变了"。
  现在改为瞬时滚动并返回**真实位移**。

另外给扩展的三个生命周期回调加了异常兜底：
MV3 的 unhandled rejection 是 worker 级错误，可能直接让扩展掉线。

### v2.1.0（2026-09-15）

**扩展桥能力补齐 + 工具合并。**

- ✨ **扩展桥补齐三项关键能力**：执行任意 JS（走 `chrome.userScripts`，
  绕开 MV3 下页面 CSP 对 eval 的拦截）、上传文件（插件分块送内容 +
  扩展用 `DataTransfer` 塞进 `input.files`）、下载（扩展用用户会话 fetch +
  分块回传，**能下登录态资源**）
- ✨ **Cookie 导出/导入**：把浏览器的登录态带到无头后端，降级时不用重新登录
- ✨ 扩展侧补齐 `getInfo`/`goBack`/`refresh`/`hover`/键盘 3 个/鼠标 6 个/
  `listFiles`/`debugInfo`，**两个后端接口完全对称**
- 🔀 **工具合并 31 → 12 个**，按「动作 + 传参」组织（`browser_interact` 用
  `action` 选 18 种动作，`browser_page` 用 `mode` 选读什么）。**能力零丢失**，
  逐条映射核对过
- ✨ **内容分页续读**：`browser_page` 支持 `offset`/`max_chars`，
  返回值带 `has_more`/`next_offset`，AI 自己决定读多少
- ⚙️ `op_timeout` **与框架工具超时脱钩**（默认），由插件自己管，避免被硬取消
- ⚙️ `headless_profile_mode=inherit`：**复制**真实浏览器数据（不抢锁），
  登录态完整保留
- ⚙️ `read_only` 默认改为 `false`（bot 有完整读写能力）
- ⚙️ `download_max_bytes` 默认 2GB（流式落盘，不占内存）
- 🐛 修复：`upload_max_bytes` 配置项加了但没接线

### v2.0.0（2026-09-15）

**双后端架构 + CPU/内存修复。**

- ✨ **双后端自动路由**：扩展桥（用户自己的浏览器，零冲突）↔ 无头后端
  （插件自己的浏览器），没连上自动回退；策略可固定为其中一种
- 🔴 **修复 CPU 问题（三条根因）**：
  - 删除旧版**反向**加上的 `--disable-background-timer-throttling` /
    `--disable-backgrounding-occluded-windows` / `--disable-renderer-backgrounding`
    （Chromium 的后台节流能把后台标签 CPU 降到 1/5，而 bot 的浏览器窗口
    常年被挡在后面，正好全命中）
  - 补齐 `--disable-dev-shm-usage`（`/dev/shm` 写满会让渲染进程崩溃→反复重载→CPU 飙升）、
    `--disable-gpu`、`--js-flags=--max-old-space-size=512`、`--renderer-process-limit=N`
  - 默认等待 `networkidle` → `domcontentloaded`（前者官方标注 DISCOURAGED）
- 🔴 **修复"卡死"真凶**：旧版 `_ensure_browser()` 从不检查页面死活，
  用户一关标签页插件就**永久失败且不自愈**。现在有页面自愈 + 致命错误重建
- 🔴 **修复"互相抢锁"**：不再直接占用真实 profile（见
  「为什么装了扩展之后，我的浏览器还能正常开」）
- 🐛 **修复内存持续增长**：弹窗页面无人回收、`element_*.png` 从不被清理、
  下载目录无清理 → 全部补上
- 🐛 **修复下载漏洞**：旧版把整个文件读进内存且无大小上限、不限协议
  → 改为流式落盘 + 大小上限 + 只允许 http/https
- ✨ **空闲回收**：`idle_close_seconds`（默认 300），空闲后自动关闭释放 CPU 内存
- ✨ 有头模式补回窗口可见性参数（`--start-maximized` / `--window-position` /
  `--force-device-scale-factor`）
- ✨ 首次运行会主动提示扩展的安装方法

### v1.2.1

- 浏览器来源四级回退（接管真实浏览器 / 插件持久 profile / 系统浏览器 / 内置 Chromium）
- 下载超时保护、cookie 按域隔离、CodeRabbit 审查修复

---

## 致谢

- 原始插件：nyx
- 扩展桥（Kira Browser Bridge）：随插件提供，Chrome / Edge / Brave 等 Chromium 系可用

## 许可

见仓库 LICENSE。
