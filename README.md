# 浏览器插件 (Browser Plugin) 2.1.10

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
| Chrome | ✅ **120+** | `chrome.userScripts`（执行 JS 依赖）从 120 起提供；扩展清单已声明 `minimum_chrome_version: 120` |
| Edge | ✅ **120+** | 同为 Chromium 内核，扩展机制一致；打开的是 `edge://extensions` |
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

**关于 `inherit`**：登录态、Cookie、书签一般都能用。Chrome 80+ 的 Cookie 用
AES-GCM 加密，密钥由 DPAPI（Windows）/ Keychain（macOS）/ OSCrypt（Linux）包裹 ——
这些保护是**用户级（多数情况下）与路径无关**的，同一台机器同一用户下副本通常能解密。

> ⚠️ **但 Windows 上有个例外**：Chrome 127 起引入了
> **App-Bound Encryption（应用绑定加密）**，密钥与**应用身份**绑定，
> 不再只是绑定到"用户+机器"。用另一个 Chromium 应用（比如 Playwright 拉起的
> 那个）去打开复制出来的 profile 时，**部分 Cookie 可能解密失败**。
>
> 遇到这种情况不必折腾 —— 用 `browser_cookie` 即可把登录态转过去：
>
> ```
> browser_cookie(action="export")     # 从你自己的浏览器里导出
> browser_cookie(action="import", …)  # 直接写进无头后端（按域写入，不依赖解密）
> ```
>
> 这也是为什么 `inherit` 复制失败时会**自动回退**到插件自己的 profile，
> 而不是直接报错——你仍可以用 cookie 导入把登录态补上。

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
| `upload_allow_any_path` | switch | `true` | 是否允许上传任意路径的文件（**默认开**，本插件定位是给 bot 完整浏览能力）。关掉后只允许 `upload_allowed_dirs` 里的目录 |
| `upload_allowed_dirs` | list | `["data/files","data/temp"]` | 上传目录白名单（仅在上项关闭时生效）。用 realpath 归一，可防 `../` 穿越 |

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

> 最低版本是 **Chrome/Edge 120**：扩展的 `browser_script`（执行任意 JS）依赖
> `chrome.userScripts`，它从 Chrome 120 起提供。低于 120 装不上扩展，
> 但不装扩展也能用（走无头后端）。

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

### v2.1.10（2026-09-15）

**按 CodeRabbit 第八轮审查修复 4 项**：

- 🔴 **弹窗会假报「已连接」**：`setStatus` 会把 `connected` 一起持久化进
  `STORE.LAST_STATUS`。`status` 分支里 `...st` 被放在 `connected` **之后**，
  于是**存下来的旧值覆盖了实时值**。MV3 的 service worker 被回收重启后
  `state.socket` 是 `null`，但 `st.connected` 还是 `true` →
  弹窗显示「已连接」，实际根本没有 socket。
  → 改成**先铺 stored、再盖上实时字段**。
- 🔴 **回归套件会自己生成 `__pycache__` 把自己判失败**：
  `security_rules` 先于 `file_hygiene` 执行，它通过 `load_module()` 加载
  `security.py`；那条路径会写出 `PLUGIN_DIR/__pycache__/*.pyc`，
  而 `file_hygiene` 的 `_all_files()` 会把 `__pycache__` 算进去 →
  **D1/D2 随机失败**（取决于有没有缓存残留）。
  → 在 `run_all.py` **导入任何检查模块之前**设 `sys.dont_write_bytecode = True`。
- **`bridge_e2e` 每次跑留下一个含 ~1MiB `dl.bin` 的临时目录**
  → 改用 `TemporaryDirectory`，并在退出上下文**之前**读取 `dl_size`。
- **`tool_merge` 的 C8 只检查三个独立子串**：即使 `credentials` 被改成
  无条件的 `"include"`（HTTP 也带会话 Cookie → 明文泄漏），只要那三个词
  还在，断言照样通过。→ 限定在 `downloadViaSession` 内，并要求凭据表达式
  **带 HTTPS 条件且有 `"omit"` 分支**。

### v2.1.9（2026-09-15）

**按 CodeRabbit 第七轮审查修复 11 项**：

- 🔴 **上一轮我改出来的 Regression**：`test_ping` 里调用了
  `probeServerRoundTrip(5000)`，但**这个函数从未定义** —— 每次点"测试"
  都会抛 `ReferenceError`，被 catch 后统一显示"测试失败"。
  → 补上实现（挂一次性监听等**服务端心跳 ping** 到达并回 pong）；
  并把超时从 5 秒改成 **30 秒** —— 心跳间隔是 25 秒，
  用 5 秒会把**正常链路也判超时**。
- 🔴 **上传分块大小不是 3 的倍数**：插件侧按 `256 * 1024` 分块，
  而 `256*1024 % 3 = 1` —— base64 每 3 字节编 4 字符，块长不是 3 的倍数时
  **每块末尾都带 padding**，独立编码后直接拼接就不再是合法 base64，
  `atob` 会抛 `InvalidCharacterError`。**大于一块的文件上传必挂。**
  → 改为 255KB（能被 3 整除），并在扩展侧拼接前**去掉各块 padding** 兜底。
- 🔴 **无头下载的 HTTPS→HTTP 重定向会带出非 Secure cookie**（CWE-319）：
  aiohttp 只过滤 `Secure` cookie，同域降级到 http 时**普通会话 cookie 照样发出去**。
  → 带 cookie 时**不自动跟随重定向**，自己跟且**每一跳都要求 HTTPS**；
  一旦要降级就丢掉 cookie jar 再继续（宁可匿名，不明文带凭据）。
- 🔴 **SSRF：完整写法的 IPv6 回环被漏判**：`0:0:0:0:0:0:0:1`（即 `::1` 的完整写法）
  被我上一轮的"拆内嵌 IPv4"逻辑误拆成 `1` → 归一成 `0.0.0.1` →
  `is_local_host` 反而**漏掉**回环地址。→ 先判断是否为合法 IPv6，
  只有**真正的 v4-mapped** 才拆。
- **`buildWsUrl` 会把裸 IPv6 的冒号当端口剥掉**：`::1` → 剥成 `::`
  → 判定为非回环 → 生成 `wss://:::5267/...`，连接直接失败。
  → 只在「带方括号的 IPv6」或「单冒号主机」时才剥端口。
- **`headless_profile_mode` 的代码兜底值与 schema 不一致**：
  schema 默认 `inherit`（复制真实数据），而 `main.py` 的 `cfg.get` 兜底写的是
  `persistent` —— 配置文件缺这一项时会**静默退回插件自己的 profile**，
  用户以为继承了登录态其实没有。→ 改为 `inherit`。

**回归测试套件 5 项**：
- `runtime_behavior` 每次跑都在系统临时目录留一个 `kira_reg_*` 目录
  → 改用 `TemporaryDirectory`（正常/异常都会清理）。
- `R4` 只断"element_ 剩几张" → 改成**三条一起断**：总数到上限、
  element_ 未被全删、留下的确实是较新的那批。
  顺带修了随机性：测试里显式区分 mtime（同一 tick 创建的文件 mtime 相同，
  排序结果取决于文件名，断言会忽过忽不过）。
- `R10` 在 `scroll` 处理器被删/改名时会 `IndexError` **中断整组检查**、
  把后面的断言全跳过（假绿）→ 先判断分隔符存在再解析。
- `security_rules` 的"下载不跟随重定向"用全文搜 → 改成只在
  `downloadViaSession` 内部找。
- `tool_merge` 的 C1 在取不到 enum 时**跳过校验** → 改为判 FAIL；
  但**非 action 式**的工具（`browser_wait` 等）本来就没 enum，不该要求。

### v2.1.8（2026-09-15）

**按 CodeRabbit 第六轮审查修复 15 项**：

- 🔴 **上一轮我把 wss 守卫写反了**（自己引入的回归）：`buildWsUrl()` 对**所有**
  非回环主机抛错 —— 而"远程主机默认走 wss"本身是正确且安全的路径，
  结果把正常用法也堵死了。→ 改为：能解析用户填的 `ws://`/`wss://`；
  默认本机 `ws`、其它主机 `wss`；**只有显式要求明文连非本机时才拒绝**。
- 🔴 **二次确认漏了三个写命令**（CWE-862）：`activate_tab` / `close_tab` /
  `mouse_move` 不在 `PRIVILEGED_COMMANDS` 里 —— 开了「写操作需确认」时，
  AI 仍能在用户未批准的情况下**切走/关掉标签页**。
  → 补齐，并让 Python 侧与 JS 侧的命令集合**逐字一致**（新增检查盯着）。
- 🔴 **`test_ping` 报"链路正常"却没做任何服务端往返**：它只检查
  `readyState` 再调**本地**的 `listTabs()`（后者跑 `chrome.tabs.query`，
  根本不经过 WebSocket）。socket 半开或插件侧路由坏了时照样显示正常。
  → 改为挂一次性监听，**等服务端心跳 ping 到达并回 pong** 才算通过；
  5 秒没等到就明确报"链路可能不通"。
- **上传在内存里重建整份文件**：base64 → 解码成字节 → 再编码回 base64，
  同一份数据存三份，200MB 上传峰值可达 1GB，足以打挂 MV3 Service Worker。
  → 内容脚本要的就是 base64，**按长度校验后原样拼接**即可，不再解码重编码。
- **`mouse_move` 声称按着左键**：`mouseInit` 无条件给 `buttons: 1`，
  等于说"移动时左键按住"——拖拽敏感页面会在纯 hover 上开始拖拽。
  → 不传 `button` 时 `buttons: 0`。
- **Chrome 最低版本 116 → 120**：扩展声明了 `userScripts` 权限，
  而该 API 从 Chrome 120 起提供，116–119 装上也用不了 `browser_script`。
  → manifest / README / `setup_guide` 三处统一为 120。
- 扩展 manifest 描述里的「默认只读」已过时（实际默认可读写）→ 改为按
  "权限由 KiraAI 插件配置控制"表述。

**回归测试套件 8 项**（CR 连检查本身也审了，这几条都挺准）：
- `file_hygiene` 把 `__pycache__` 从清单里**剔除**了 → D1/D2 永远查不到它们，
  等于形同虚设。→ 让它们进清单，由检查判红。
- `callgraph` 的 A13b/A13c 只看 `sym(` 调用形式 → 漏掉 `MSG.PING`、`CMD.DOWNLOAD`
  这类**成员访问**与当值传递（运行时同样是 ReferenceError，
  而 `node --check` 不查未定义标识符）→ 扩展覆盖面。
- `tool_merge` 的 C1 用**全文搜索**找 action → 目标工具删了某动作、
  但别处还有同名动作时照样 PASS。→ 改为解析**目标工具自己的 enum**
  （且只取 schema 段，不含函数体）。**反向验证**过。
- `runtime_behavior` 的 R5 用 `<= 6` → 同时放过"多留一个"和"删多了"。
  → 改为精确等于上限。
- `security_rules` 的 B2 用子串判据判"不回传绝对路径"→ 格式化一下就能绕过。
  → 改为解析返回对象的**键**。
- `bridge_e2e` 失败路径不收子进程与服务（断言抛错时 node 还活着）→ 包 `try/finally`。
- `content_dom` 的 runner 文件名固定 → 并行执行会互相删。→ 加 PID 后缀。
- 假 Playwright 的 `_die_by_itself()` 没计入 `pages_closed` → 与"每次关闭都计数"
  的契约不符。

### v2.1.7（2026-09-15）

**按 CodeRabbit 第五轮审查修复 9 项**：

- 🔴 **非本机地址会用明文 `ws://` 传令牌**（CWE-319）：`buildWsUrl()` 无条件拼
  `ws://`，而接入令牌就在 query string 里。host 可由用户配置 ——
  一旦填远程主机，网络上的任何人都能抓到令牌，然后**拿到整个浏览器桥权限**。
  → 回环地址用 `ws://`（不出网卡），其它地址**必须 `wss://`**；
  用户填远程主机却想用 ws:// 时**直接报错**，而不是悄悄发明文。
- 🔴 **旧连接的 `onclose` 会清掉新连接**：`disconnect()` 异步关旧 socket 后
  立即允许重连；若旧 socket 的 `onclose` 在新 socket 已写入 `state.socket`
  之后才执行，它会把**新连接引用清成 null**，还可能为旧连接起一次重连。
  → 每次 `connect()` 捕获局部引用，所有回调先确认"自己仍是当前连接"。
- 🔴 **替换旧连接时没清在途命令与下载 sink**：`_close_ws()` 只关 socket，
  不清 `_pending` 也不收 `_sinks`；而旧连接的 `_cleanup` 又会因 session 已换
  而直接返回 —— 旧命令一直等到超时，下载句柄也一直开着。
  → 改用 `_force_close()`（一次做完 cancel 心跳 + 失败在途命令 + 收 sink）。
- **扩展下载无条件带凭据**（CWE-319）：`credentials: "include"` 对 HTTP 目标
  或 HTTPS→HTTP 重定向会送出非 Secure 的 Cookie。
  → 仅 HTTPS 带凭据，并把 `redirect` 设为 `error`（**根治**跨协议泄漏，
  而不是"跟随后再检查"——那时已经发出去了）。
- **下载列表回传本机绝对路径**（CWE-200）：`chrome.downloads.search()` 的
  `filename` 通常含用户名与本地目录结构，而扩展后端本来也用不了该路径。
  → 只回文件名与必要元数据。
- 回归检查 3 项：`bridge_e2e` 端口写死会撞车 → 改绑 `0` 让系统分配；
  `callgraph` 的豁免"文件里有一处合法就跳过整个文件" →
  改成按 AST 定位所属函数，只豁免 `_sid_of` 内部那处（并**反向验证**过：
  同文件里再插一处不安全用法能报出来）。
- `setup_guide` 的 Windows 浏览器路径只查一个环境变量 → 补
  `%PROGRAMFILES(X86)%` 等常见位置（Edge 在 64 位系统上装在 x86 目录），
  并跳过空环境变量（`Path("") / "x"` 会得到相对路径、误判本机存在）。

**`upload_allow_any_path` 保持默认 `true`**（按需求）：本插件的定位是给 bot
完整的浏览器能力，上传任意本机文件是预期功能。想收紧就把这项关掉，
白名单在 `upload_allowed_dirs`。README 配置表已与代码/schema 对齐。

### v2.1.6（2026-09-15）

**按 CodeRabbit 第四轮审查修复 10 项**（1 个 Critical + 9 个真实问题）：

- 🔴 **扩展模块直接语法错误**：`capabilities.js` 既 `import { sendChunk }`
  又在本地 `function sendChunk()` 声明了一遍 → `SyntaxError: Identifier
  'sendChunk' has already been declared` → **整个扩展加载不了**。
  → 删掉本地重复声明。
  → 新增检查 **A13c：用 module 模式检查 ES 模块语法**。
  这之前一直是盲区：`node --check xxx.js` 会把 `.js` 当 **CommonJS** 解析，
  "import 与本地声明重名"这类**模块级**语法错误根本查不出来。
- **`upload_allow_any_path` 的默认值分裂**：schema 写 `true`、代码也写 `true`，
  但**取值不安全**（可外传任意本机文件）。→ 两处统一改为 `false`（安全值），
  并新增检查 **C3：代码默认值必须与 schema 一致**
  （这条检查当场又揪出 `op_timeout` schema=120 / 代码=40 的真实分裂）。
- **SSRF：IPv4-mapped IPv6 可绕过**（CWE-918）—— `ipaddress` 对
  `::ffff:127.0.0.1` 的 `is_loopback` 是 **False**，而 Chromium 会真的连到回环。
  十进制/十六进制写法我上一轮堵了，这个映射形式漏了。已补（含
  `::ffff:7f00:1` / `::ffff:2130706433` 等变体），并加入用例表。
- **带凭据下载允许明文 HTTP**（CWE-319）→ 非 HTTPS 时**不带 Cookie**。
- **`available` 判据不对**：非持久化启动会**先赋 `_browser` 再建 `_context`**，
  并发调用能在 `_context` 还是 None 时绕过锁进来，自愈逻辑甚至会把
  对方**正在初始化**的浏览器关掉。→ 只认 `_context`。
- **`navigate(new_tab=True)` 不关旧页面**：每调一次多一张 Chromium 页面常驻，
  而 `_check_tab_id()` 又让调用方选不到它们 —— 纯泄漏。→ 建新页后关旧页。
- **chunk 写失败只 abort sink 不置 future**：调用方会一直等到自己的超时
  （下载默认 **600 秒**），磁盘满/非法 base64 表现为"工具卡 10 分钟"。
- **命令没到 bridge 时 sink 没人收**：扩展未连接 / 未知命令会从
  `send_command` 入口就抛，不走它内部的 try，于是句柄泄漏、磁盘留 0 字节文件。
  → 补 `abort_download_sink()` 公开入口并在失败路径调用。
- **profile 新鲜度漏了 Local Storage**：不少站点把登录 token 只存在
  Local Storage，漏了就一直复用旧副本，用户"重新登录了却还是登出状态"。
- **回归检查 C12 拿注释当判据**：注释还在、`el.click()` 被加回来时照样会过。
  → 改为检查**真正的代码**（并做了反向验证）。

### v2.1.5（2026-09-15）

**按 CodeRabbit 第三轮审查修复 13 项**（含又一批 ReferenceError 级问题）：

- 🔴 **`capabilities.js` 没有任何 import** —— 它调用的 `resolveTab` /
  `assertInjectable` / `callContent` / `sendRaw` / `sendChunk` / `MSG` 全都没导入，
  ES 模块严格模式下直接 `ReferenceError`：**执行JS、上传、Cookie 导出全不可用**，
  非空下载也会在 `sendChunk` 崩掉。（我上一轮"已修"其实没落到位。）
  → 补上 import，并新增检查 **A13b：用了但没 import 的符号**
  （上一轮加的 A13 只验了反方向，漏掉了这条）。
- 🔴 **上传路径白名单在合并时丢了** —— `upload_allow_any_path` /
  `upload_allowed_dirs` 两个配置项连同校验逻辑一起消失，
  模型可借"上传"把**本机任意文件**外传（CWE-200）。→ 已恢复，
  且校验放在**读文件之前**（用 realpath 归一，防 `../` 穿越）。
- **`github.*` 会匹配 `github.com.evil.test`** —— `fnmatch` 的 `*` 跨点号，
  等于把白名单授给了攻击者域（CWE-284）。→ 改为按标签边界逐段匹配。
- **弹窗回收会关掉自己正在创建的页面** —— Playwright 的 `context.new_page()`
  在 **await 返回之前**就派发 `page` 事件，那一刻 `_page` 还指着旧页，
  回收逻辑会把新建的这张关掉。→ 加"创建中"守卫。
- **`open_download_sink` 打不开文件时只记日志就 return** —— 后续 chunk 全被丢，
  `_finish_sink` 返回 None，调用方**报告下载成功但磁盘上没文件**。→ 改为抛出。
- **`userDisconnected` 没持久化** —— MV3 的 Service Worker 被回收再唤醒后标记丢失，
  用户点「断开」约 30 秒后连接自己回来，弹窗那句"自动重连已暂停"成了假话。
  → 存进 `chrome.storage.local`。
- **`exec_js` 的表达式/函数体回退会在任何异常时触发** ——
  一条"能解析但运行到一半抛错"的脚本（如 `items.forEach(i => post(i))`）
  会被**执行两遍**，副作用重复。→ 只在 `SyntaxError` 时回退。
- **`scroll`/`mouse_wheel` 会滚双倍距离** —— `dispatchEvent` 返回 false 说明
  页面已 `preventDefault` 自己处理了，代码却照样再滚一次窗口。
- **`click_count` 不产生双击** —— 循环 down/up 会让页面收到"两次独立单击"，
  `dblclick` 永不触发。→ 改用 `mouse.click(click_count=n)`。
- **`navigate` 取标题失败时漏 `title` 字段** —— `_send` 把失败转成 `OpResult`
  而非抛异常，`except` 分支根本不走，兜底 `setdefault` 被跳过，破坏两后端契约。
- 文档修正：popup 的「默认只读」已过时（实际默认可读写）；
  `first_run_notice` 把 `inherit` 模式说成"没有登录态"是错的；
  README 补充 **Windows App-Bound Encryption**（Chrome 127+）会让部分 Cookie
  无法跨应用解密，并建议此时改用 `browser_cookie` 导出/导入。

另有 5 项是**回归测试套件自身**：`PATH` 写死导致找不到 node、子进程引用
可能被 GC 提前回收、`defined` 没算类体赋值、schema 直接下标会让整组中断、
以及上一轮那个"顶层模块全被跳过"的 A1（等于大部分文件没验）。

### v2.1.4（2026-09-15）

**按 CodeRabbit 第二轮审查修复 13 项**（1 个 Critical + 12 个真实问题）：

- 🔴 **扩展里 `socket` 未定义 → 扩展永远连不上**：把 `socket` 挪进
  `shared.js` 的 `state` 之后，`background.js` 里还留着 `socket.onopen = ...`
  这类裸引用。ES 模块是严格模式，直接 `ReferenceError`，
  而且 `connect()` 里那句是无条件执行的 —— **每次连接都失败**。
  → 全部改为 `state.socket`，并新增检查 **G1：不允许裸的状态标识符**。
- **替换旧连接时没取消旧心跳任务**：旧连接的 `_cleanup` 因 session 已换而直接返回，
  旧心跳继续活着，还会往**新连接**发 ping。每次重连多留一个协程。
  → 替换前先 cancel。
- **下载 sink 在失败路径不关闭**：超时/异常/`ok=false` 时不收 sink，
  文件句柄挂到进程退出、磁盘留半成品。→ 三条路径都 `_abort_sink`，`close()` 也清。
- **上传上限在解码后才检查**：超限文件已经完整占住内存，上限形同虚设。
  → 先按 base64 长度估算拦截，再边解码边累计兜底。
- **`exec_js` 强制表达式语法**：`(function(){ return (${script}); })()` 只接受单条
  表达式，多语句（`const a=1; return a;`）会语法错误。
  → 先试表达式、失败落函数体模式，两种写法都支持。
- **`userScripts` 探测结果被缓存（含失败态）**：用户按提示打开开关后再试仍是失败，
  必须重载扩展。→ 只缓存成功结果。
- **下载最后一块缓冲没查上限**：刚好卡边界的文件能绕过限制。
- **`--disable-...` 之外**：启动失败的半成品实例没关就置 None（泄漏孤儿 Chromium 进程）；
  `copytree` 同步阻塞事件循环（几 GB 会把整个 KiraAI 卡住）；
  `custom_user_data_dir` 可以指向真实浏览器目录（正是要避免的抢锁场景）；
  用**目录** mtime 当 profile 缓存版本（`Cookies` 改了目录 mtime 不变 → 一直用旧副本，
  新登录读不到）；idle 看门狗自我取消导致"正常收尾"变异常退出。
- **下载时 cookie 语义丢失**：用 `{name: value}` 更新 CookieJar 会丢掉
  domain/path/secure/expires → 跨域重定向可能带错 cookie。→ 逐个 `add_cookie` 保留完整语义。
- **无头后端的 `tab_id` 被静默忽略**：传了别的 tab_id 不报错也不生效，
  模型以为操作了那张标签页。→ 明确告知"无头只有一张页"。

另有 6 项是**回归测试套件自身**的问题（CR 也一并审了）：
`py_compile` 会往源码树写 `.pyc`（污染文件清点）、jsdom 缺失后没 return、
`bridge_e2e` 没先检查 node、`callgraph` 的 async 检查是死代码（永远报不出）、
`tool_merge` 只验工具名不验 action、`harness` 文档写的是旧契约。

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
