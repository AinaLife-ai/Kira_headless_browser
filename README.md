# 浏览器插件 (Browser Plugin) 2.1.22

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
| Chrome | ✅ **135+** | `chrome.userScripts.execute`（执行任意 JS 依赖）**从 135 起默认可用**；清单已声明 `minimum_chrome_version: 135`（低于此版本走无头后端执行 JS） |
| Edge | ✅ **135+** | 同为 Chromium 内核，扩展机制一致；打开的是 `edge://extensions`（低于此版本走无头后端执行 JS） |
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
| `screenshot_dir` | string | 空（即用 `data/temp`） | 截图保存路径；留空时落到 `data/temp` |
| `download_dir` | string | 插件数据目录/downloads | 下载保存路径 |
| `screenshot_max_count` | integer | `50` | 截图最多保留张数（元素截图也计入清理） |
| `screenshot_auto_clean` | switch | `true` | 自动清理旧截图 |
| `download_auto_clean` | switch | `true` | 自动清理下载目录 |
| `download_max_count` | integer | `100` | 下载目录最多保留文件数 |
| `download_max_bytes` | integer | `2147483648` | 单个下载大小上限（2GB）。下载是**流式落盘**，无论多大都不占内存 |
| `upload_max_bytes` | integer | `268435456` | 上传大小上限（256MB）。上传走**分块流式**，单帧恒定 ~340KB；分块累积在**页面上下文**（SW 只转发），峰值内存 ≈文件大小 ×1.0。超过 `ExtensionBackend.MAX_UPLOAD_BYTES` 会被自动钳制 |
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

> 最低版本是 **Chrome/Edge 135**：扩展的 `browser_script`（执行任意 JS）依赖
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

### v2.1.22（2026-09-15）

**按 CodeRabbit 第十八轮审查修复 4 项**（含 1 个 SSRF 漏洞）：

- 🔴 **SSRF：内网地址完全没拦**。`is_local_host` 原本只判
  `is_loopback or is_unspecified`，于是这些都**直接放行**：
  - `10.0.0.5` / `192.168.1.1` / `172.16.0.1`（内网）
  - **`169.254.169.254`** —— 云厂商的**实例元数据端点**，
    拿到它就能读走云主机的临时凭据，是 SSRF 的典型目标
  - `fe80::1`（链路本地）、`::ffff:10.0.0.1`（v4-mapped 内网）
  → 改为按"内部地址"整体判定（回环 / 未指定 / 私有 / 链路本地 /
  保留段 / 组播），并处理 v4-mapped 与 6to4/Teredo 内嵌地址。
- 🔴 **只看字符串，不看解析结果**：`internal.corp` / `db.local` 这类
  主机名本身"不像本机"，但**解析出来可能就是 10.x**。
  → 新增 `resolved_url_is_internal()`：真的 `getaddrinfo` 一次，
  解析到内网就拒绝（在 `check_url` 里与字符串判定一起生效）。
  ⚠️ **如实说明局限**：校验与真正连接之间仍有 TOCTOU 窗口
  （DNS rebinding）。要彻底解决必须在**连接时**校验实际使用的 IP，
  这一层只能挡住"明显的内网目标"。已写进代码注释，不假装完备。
- ⚠️ **修这条时踩到的坑（差点造成大面积误伤）**：Python 把
  `198.18.0.0/15`（RFC 2544 基准测试段）也算作 `is_private`，
  而 **Clash / mihomo 这类代理默认就用它做 fake-IP**。
  一开始连 `example.com` 都被拒 —— 代理环境下**所有站点全废**。
  → 显式放行该段，并加了"公网地址不误判"的对照用例。
- **`update_cookies({}, ...)` 的 no-op 空转循环**（我删过一次，
  改别处时又带回来了）：传空 dict、吞异常，什么也不写入，
  却让人以为"这里处理了 cookie"。→ 删除，并加守卫 **C16l**
  （扫出任何传空 dict 的 `update_cookies`）。

**测试可信度 2 项**：
- **`C16k` 的断言太弱**：原判据是"存在 `= sendRaw(...)` 的赋值
  且出现了 `state.probe`" —— 但**赋值了却不用它做条件**照样能通过
  （`const s = sendRaw(...); if (true) probe()`）。
  → 改成**结构判定**：要求那个标识符真的出现在 `state.probe()`
  之前的条件里；并加了**变异夹具**（有守卫 → 必过；赋值但无守卫 →
  必须判不过；完全无守卫 → 必须判不过）。
  写夹具时还发现：不剥注释会被自己写的说明文字误判（假红）。
- **`contract.py` 的 upload 夹具用 `mkdtemp`**，落在受管理的
  `TemporaryDirectory` 之外 → 永远不会被清理。→ 移入同一个根之下。

**新增用例**：`B2.10`（内网/元数据地址必拦）、`B2.11`（公网地址与
代理 fake-IP 段不得误判）、`B2.12`（主机名解析到内网会被拦）。
三个都做了反向验证。

### v2.1.21（2026-09-15）

**按 CodeRabbit 第十七轮审查修复 6 项**（含 1 个"假成功"+ 3 处测试可信度）：

- 🔴 **链路测试会假报"正常"**：收到服务端 `PING` 后，原来的代码**无论
  PONG 有没有发出去**都会调 `state.probe()` —— 而收到 ping 只说明"我们能收"，
  `sendRaw` 返回 `false` 说明 socket 只能收不能发，链路其实是不通的。
  结果是弹窗显示"链路正常"，实际连命令都发不出去。
  → 只有 `sendRaw(...)` 返回真时才认定往返成立。新增守卫 **C16k**。
- **桩与真实 Playwright 不一致**：`regression/stubs` 里的 `mouse.down/up`
  只收 `button`，而 `HeadlessBackend.mouse_click` 会传 `click_count` ——
  桩直接 `TypeError`，**双击那条路在测试里根本走不到**。
  → 补上 `click_count` 参数。

**测试可信度 3 项**（都是"检查本身不够硬"）：
- **U4 / C8b 不判退出码**：脚本崩了却恰好留下能解析的输出时，逐项断言会
  "少报"（少报 = 漏检），甚至一项都不报。→ 都改成**先判 `returncode`**，
  并且要求结果完整（U4 非空、C8b 覆盖到必要场景）。
- **`cred_mode.mjs` 是"注入式"验证**：它把 `isHttps` 作为变量**直接喂进**
  凭据表达式 —— 那样只验证了三元表达式本身，**生产里 `isHttps` 是怎么算出来的
  完全没被覆盖**。把 `url.startsWith("https://")` 改成恒真（HTTP 也带 Cookie）
  照样通过。→ 改成**从 capabilities.js 抽出真实的 `isHttps` 定义行**，
  用真实 URL 驱动执行，并加大小写不敏感与本机 HTTP 两个用例。
  反向验证：`isHttps = true` → 立即 FAIL。
- **`upload_sweep.mjs` 只做"源码里有没有 setInterval"的间接检查** →
  改成用**可控定时器**（替掉 `setInterval` 与 `Date.now`）真跑回收：
  推进时钟到 10 分钟空闲后，被遗弃的会话必须收块失败、
  而"一直有活动"的慢速会话必须活着。另加"清理器随会话启停"一条。
  反向验证：判据改回 `created` → 慢速会话那条 FAIL；删掉 sweeper →
  遗弃会话那条 FAIL。

### v2.1.20（2026-09-15）

**按 CodeRabbit 第十六轮审查修复 9 项**（含 1 个内存泄漏、1 个权限收紧）：

- 🔴 **被遗弃的上传会话会一直占着内存**：上传到一半时如果插件崩溃 /
  连接断开 / 用户换页面，`upload_finish` 永远不来，那个会话连同已收到的
  分块（可能几百 MB）就**一直挂在页面里** —— 而回收只发生在
  "下次 `uploadBegin` 时顺手清"，下次上传可能是几小时之后。
  → 加了**定期清理器**（有活跃会话时启动、空了就停），
  并且回收判据用 **`lastActive`**（每次收块刷新）而不是 `created` ——
  否则一个传得很慢的**大文件**会在传输途中被误回收。
- **令牌落盘有一个"权限空窗"**：原来是先 `write_text` 写最终文件、
  再 `chmod(0o600)`。在写入与 chmod 之间，文件是**默认权限**
  （通常 0644，同机其他用户可读）；中途被杀掉时权限永远补不上。
  → 改成：目录建 `0o700` → 令牌写进同目录下 `mkstemp` 出来的临时文件
  （**创建时**就 `0o600`）→ `os.replace` **原子替换**。
  实测：文件 0o600、目录 0o700、内容一致、无临时残留。
- **`_v` 未初始化**（测试脚本）：`subprocess.run` 本身抛异常（超时/权限）时，
  下面的错误信息 f-string 会读未定义的 `_v` → `NameError`，
  把"node 版本判不出来"变成一句与版本无关的崩溃。→ 先给默认值。

**回归套件 5 项**：
- **`A1` 会误报"存起来的可调用"**：`self._sink = fn` 之后再 `self._sink()`
  是合法写法，但 A1 只收集 `def`、不收集属性赋值 → 报"未定义"。
  误报多了这条检查就没人看了。→ 收集本类的 `self.x = ...` 赋值，
  并且**排除嵌套类**（那个类的 `self` 是它自己）。两个方向都做了反向验证。
- **`C5a` 在找不到 `export const CMD = {` 时 `IndexError`** → 异常逃出
  `run()`，整组变成一条笼统失败、后面检查全不执行。→ 先判标记存在。
- **`callgraph` 的嵌套类处理**（同上）。
- **`cred_mode.mjs` 用 `URL.pathname`** → 改 `fileURLToPath`
  （Windows 上会留下 `/C:/...`，含空格/非 ASCII 也不解码）。
- **`run_all.py` 打印的是 `HERE.parent`** 而不是检查实际用的
  `PLUGIN_DIR` → 多副本/反向验证时会把人误导到别的树。→ 打印真实值。

**新增用例 U4**（上传会话回收行为，5 项）：用 jsdom 真跑 `content.js`，
验证"慢速传输中仍能收块"、"abort 后立即失效"、"存在周期性清理器"、
"回收判据基于活动时间"。

**清理**：删掉 `content.js` 里已不再使用的 `upload_blob`
（旧的一次性上传逻辑，分块流式上线后就没人调了）。

### v2.1.19（2026-09-15）

**按 CodeRabbit 第十五轮审查修复 13 项**（含 2 个运行期崩溃、3 个"假成功"）：

- 🔴 **`mode="selector"` 会在无头后端直接崩**：`main.py` 路由时会传
  `selector=`，但 `HeadlessBackend.get_page` 的签名里**没有这个参数** ——
  调用即 `TypeError: got an unexpected keyword argument 'selector'`，
  而且是在调度层抛出的，模型只会看到一句莫名其妙的报错。
  → 补上签名与实现（与扩展后端同形状：只取该元素的文本）。
  实测：`get_page(selector="#x")` 现在返回 `content='selector-text'`。
- 🔴 **`_force_close` 会取消自己**：心跳循环在发送失败时调
  `_force_close`，而它会 `cancel()` 自己的 task —— 当前协程在下一个
  `await` 点被取消，**后面的 `_close_ws` 根本执行不到**，
  socket 就那么留着（面板显示已断开、实际连接还在）。
  → 判断 `self._heartbeat_task is asyncio.current_task()` 时不自取消。
- **扩展后端截图"假成功"**：`screenshot(full_page=True)` / `selector=`
  被**默默忽略**，照样截一张视口图并返回成功 —— 调用方以为拿到了整页/
  元素截图。→ 明确返回失败，让路由回退到无头后端。
- **`get_page` 的 selector 同理**（见上）。
- **确认通知不会消失**：`chrome.notifications.create` 用了
  `requireInteraction: true`，但只有**点击**路径会 `clear`，**超时路径不会** ——
  超时的确认一直挂在通知栏，用户过一会儿再点它还会二次响应。
  → 把清理统一收进 `settle()`（点击/超时都走它），并去掉 `resolveConfirm`
  里的重复清理。
- **`state.probe` 并发互相踩**：它是**单个**槽位，第二个测试会覆盖第一个，
  而旧探测的超时定时器仍会触发、把**新**探测的回调清掉 ——
  表现为"点了测试没反应"。→ 有探测在跑时直接拒绝。
- **合成点击不触发 `dblclick`**：`mouse_click(click_count=2)` 只派发两次
  `click`，而**合成事件**不像真实输入那样由浏览器推导出 `dblclick` ——
  双击选词/双击打开这类交互完全不会触发。→ `n>=2` 时补一个 `dblclick`。
- **README 的 Edge 版本与其它地方矛盾**（120 vs 135）→ 统一为 **135**。
- **`setup_guide.py` 有句不实说明**："无头后端可以用 `--load-extension`
  把扩展预装进去" —— 代码里**没有**这条路（启动参数反而带
  `--disable-extensions`）。→ 改正。

**回归套件 4 项**：
- **`bridge_e2e` 只判"有没有 node"**：CLIENT_JS 用到 **Node 22+** 的
  API，Node 20/21 会在跑到一半时以难懂的方式炸。→ 预先判版本，低了就跳过。
- **`.gitignore` / `file_hygiene` 的名字对不上**：实际生成的是带唯一后缀的
  `kira_ext_client_*.mjs`，而两边都在匹配旧的确切名
  `_ext_client.mjs` → 残留永远清不出来、D5 会一直报脏。
- **`claims` 要求 `cookie_get` 必须是集合里第一个元素** → 改为位置无关；
  **`upload_max_bytes` 的钳制只判"标识符出现过"**（import/注释也算）→
  改为要求真的存在夹值逻辑。反向验证：把钳制换成"只提一句" → 立即 FAIL。
- **`tool_merge` 的 C8 只做语法检查** → 新增 **C8b 行为验证**：
  把凭据表达式在 HTTP/HTTPS 下**各真跑一次**。
  ⚠️ 这条很值：把条件写反（HTTPS→omit、HTTP→include）时
  **C8 照样通过**（表达式里两个字面量都在），只有 C8b 抓得住 ——
  而那个写法会让**会话 Cookie 在明文 HTTP 上被发出去**。

### v2.1.18（2026-09-15）

**按 CodeRabbit 第十四轮审查修复 9 项**（含 2 个安全项）：

- 🔴 **多级公共后缀漏判 → 规则形同虚设**：域名主体判定靠一张**硬编码**
  的 `MULTI_TLD` 表（com.cn / co.uk / …），那种写法永远补不全 ——
  `co.za` / `com.ar` / `co.il` 都不在表里，于是 `bank.co.za` 的主体被算成
  `co.za`，`*.bank*` 这类规则**匹配不上真正的银行域**。
  → 改用维护中的 **Public Suffix List**（`publicsuffix2`，已加进
  `requirements.txt`；拿不到时退回"最后两段"并给出警告）。
  实测：`bank.co.za` / `bank.com.ar` / `bank.co.il` 现在都能命中。
- 🔴 **归一化规则时把裸 IPv6 的尾组当端口削掉**：
  `re.sub(r":\d+$")` 会把 `2001:db8::1` 削成 `2001:db8:`、`::1` 削成 `:`，
  用户填的 IPv6 屏蔽词**永远匹配不上**。
  → 只从 `hostname:port` 或 `[IPv6]:port` 剥端口，裸 IPv6 原样保留。
- **`minimum_chrome_version` 定错**：清单写的是 120，但
  `chrome.userScripts.execute` 是**从 Chrome 135 起才默认可用**的
  （查证：Chromium extensions 组的公告）。低于 135 的用户会拿到一个
  "不支持"的报错却不知道为什么。→ 清单与 README 都改为 **135**。
- **`sendChunk` 丢掉发送结果**：`sendRaw` 在 socket 已关时返回 `false`，
  而 `sendChunk` 把它丢掉了 —— 下载会一路走到 `return {ok:true}`，
  调用方以为成功，**而磁盘上的文件缺了后面所有分块**。
  → `sendChunk` 回传结果，下载处检查失败即中止并抛错。
- **带端口的完整地址会连错端口**：用户粘 `http://127.0.0.1:8000` 时，
  端口 8000 被丢弃、改用端口字段（默认 5267）——**静默连到错误的端口**。
  → 解析出显式端口并优先使用。

**回归套件 4 项**：
- **`callgraph` 的 D1 现在只筛 `str(...)` 调用**，不再对 `logger.info(...)`
  这类属性调用做无谓判定。
- **`C16e` 又会漏掉模板插值里的未声明变量**：我在 G1 修过这个问题，
  但 `static_audit.py` 里**还有一份拷贝**用的是"整个模板串删掉"的老写法 ——
  `` `${foo}` `` 里的 `foo` 一并消失。→ 同样改为保留 `${...}` 内容，
  并加了**自检夹具**（只在插值里引用未声明变量 → 必须被抓到）。
- **`tool_merge` 的 C6 只在全文里找 `chrome.userScripts.execute`** →
  限定到 `execJs` 函数体内部（别处提到不算）。
- **`tool_merge` 的 C1 对非 action 式工具直接跳过** → `browser_wait`
  丢了 `seconds` 参数这种能力丢失查不出来。→ 增加按**具名参数**的校验
  （`LEGACY_PARAMS` 表），反向验证：删掉 `seconds` → 立即 FAIL。
- **`upload_stream.mjs` 用 `URL.pathname` 拼路径** → 改为 `fileURLToPath`。

**新增用例**：`B2.8`（PSL 多级后缀）、`B2.9`（裸 IPv6 不被削端口），
两个都做了反向验证（还原成旧写法 → 立即 FAIL）。

### v2.1.17（2026-09-15）

**按 CodeRabbit 第十三轮审查修复 8 项**（含 1 个内存泄漏 + 1 个测试假绿）：

- 🔴 **`uploadFinish` 失败时泄漏页面侧会话**：原来是先 `_uploadTabs.delete()`
  再调 `upload_finish` —— 一旦失败/超时，**页面侧的 `_upSessions` 就没人清了**，
  分块一直挂着（大文件就是几百 MB 的内存泄漏），而且下次同名上传还会
  撞上残留状态。→ 改成失败时**先发 `upload_abort` 清理页面会话、再删映射**；
  成功路径才直接删（页面在 `upload_finish` 内部已自清）。
- 🔴 **测试假绿（又是同一类）**：`content_dom` 只比对脚本的 stdout 标记，
  **不检查退出码** —— 脚本崩溃前恰好打完全部成功标记时会被误判为通过。
  → 非零退出码一律先记 U0 失败，并把 U1–U3 一并置失败。
  ⚠️ 修完后反向验证**仍然是绿的**，追下去发现更深一层：
  `harness.JS_DIR` 锚在套件自身目录（`HERE/js`），**不受 `KIRA_PLUGIN_DIR` 影响**，
  于是"检查脚本从默认目录读、被测的 content.js 从指定副本读" —— 两棵树混着用。
  → `JS_DIR` 改为跟随 `PLUGIN_DIR`。修完反向验证才真正变红。
- **popup 的 connect / test / disconnect 没接住 `sendMessage` 的 reject**：
  service worker 被回收时它会 reject，弹窗会卡在"连接中…/测试中…"不回来。
  → 三个调用点都补上，并抽出 `_renderBackendUnavailable()` 与 `refresh()`
  共用同一套渲染。新增守卫 **C16j**（扫出任何没被 try 兜住的 `sendMessage`）。
- **档案新鲜度误判**：`IndexedDB` 参与了"副本是否过期"的 mtime 扫描，
  但复制档案时 `IndexedDB` **在 IGNORE 名单里、根本不搬** ——
  于是源侧 IndexedDB 一变就判定过期、整份档案白重拷一遍。
  → 从扫描路径里去掉。
- **`schema.json` 的 hint 里有过时描述**：我上一轮只用 `replace` 换了尾巴，
  留下"整个文件要放进**一条**消息…单帧必须扛得住"这种**与现状矛盾**的旧说法
  （同时又说"单帧尺寸不再是约束"）。→ 重写为准确的流式 + 页面累积说明。
- **`callgraph` 的 B1/C1/D1 没有各自 catch `SyntaxError`**：任一文件语法错误
  会让异常逃出 `run()`，整组变成一条笼统失败、后面段落全不执行。
  → 三处都按 A 段的模式单独接住并记为该段的 FAIL。
- **`claims` 只验集合名存在**：`CONFIRM_ONLY_COMMANDS` 可以是空集合，
  检查照样通过。→ 收紧为"同一声明里必须同时出现集合名与 `cookie_get`"，
  两侧都加了；顺带删掉我自己重复的一条。
- **`upload_stream.mjs` 里 `rcap` 的结论没并入 `allOk`**：只打印不判，
  "误拒了"也照样 exit 0。→ 并入 `allOk`，并补一条"上限生效时必须真的被拒"
  的正向用例。

### v2.1.16（2026-09-15）

**上传：把分块的累积从 Service Worker 挪到页面上下文 —— 内存不再放大 2.67 倍**

v2.1.15 把上传改成"分块流式"解决了帧尺寸问题，但还有个更隐蔽的
性能问题：**分块仍然攒在 MV3 的 Service Worker 里**，而 SW 恰恰是
整个扩展里最容易被系统回收的地方（回收会让上传直接断掉），
并且它的峰值 = 文件 × 1.33（base64）+ 拼接副本 ≈ **文件 × 2.67**。

改成三段式：

```
插件 ──(upload_chunk)──▶ Service Worker ──(立即转发)──▶ 内容脚本（累积）
                              ↑
                      峰值恒定为一块（~0.3MB）
```

- **SW 只做转发**：收到一块就 `callContent` 转发给页面，自己不保存任何内容。
- **页面侧（content.js）负责累积**：每块 base64 立刻解码成 `Uint8Array`
  存进会话，`upload_finish` 时才 `new Blob(chunks)` → `new File([blob])`。
- 新增 `upload_begin`（此时就把 input 解析好，避免传完才发现选择器不对）、
  `upload_chunk`（**顺序校验**，乱序会让文件损坏，宁可明确报错）、
  `upload_finish`、`upload_abort`。

**实测（Node 基线，RSS，不中途 GC）**：

| 文件 | 改造前（SW 累积） | 改造后（页面累积） |
|---|---|---|
| 64 MB | 85 MB (1.33×) | **70 MB (1.10×)** |
| 200 MB | 267 MB (1.33×) | **200 MB (1.00×)** |
| 256 MB | 341 MB (1.33×) | **261 MB (1.02×)** |

**SW 侧峰值从"随文件线性增长"变成恒定的 ~0.3MB。**

**吞吐与正确性实测**（真实 uvicorn，逐块往返）：

| 文件 | 块数 | 用时 | 吞吐 | 内容校验 |
|---|---|---|---|---|
| 32 MB | 129 | 1.6s | 19.8 MB/s | ✓ SHA-256 一致 |
| 100 MB | 402 | 5.4s | 18.6 MB/s | ✓ 一致 |
| 256 MB | 1029 | 13.3s | 19.2 MB/s | ✓ 一致 |

→ 默认上限提到 **256MB**（约 13 秒传完，内存约 1.0× 文件大小）。

**新增守卫**：
- **C16h**：SW 侧上传路径**不许出现累积/拼接**（`parts.push` / `join(` 等）。
  反向验证：加回 `st.parts.push(data)` → 立即 FAIL。
- **C16i**：累积必须实现在 `content.js`。
- **U1/U2/U3**：用 jsdom **真跑** `content.js`，从 `input.files[0]` 把内容
  读回来**逐字节比对**（1MB/5MB/20MB），并验证乱序被拒、上限为 0 时不误拒。
  ⚠️ 这个用例第一版把插件路径**写死**了 —— 反向验证时"悄悄测的还是原目录
  的文件"，检查永远通过、等于没有检查。改成读 `KIRA_PLUGIN_DIR` 后才真正生效。

### v2.1.15（2026-09-15）

**上传改为分块流式 —— 顺带发现"单帧 16MiB 硬上限"这个隐藏约束**。

问："上传上限真不能大一点吗？"

查下来发现，之前配的 32MB **本身就发不出去**：

- KiraAI 用 **uvicorn**，其 `ws_max_size` 默认 **16 MiB**，
  框架**没有覆盖**它（`webui/app.py` 的 `uvicorn.Config` 里没这个参数）。
- 实测：整条帧超过 16 MiB → 对端回 `1009 (message too big)` 并
  **关闭整个 WebSocket 连接**。也就是说一次超大上传会**把连接打断**，
  连带后面所有命令一起崩，而不只是这一次上传失败。
- 而 base64 会放大 4/3 —— 所以"一条消息"能承载的文件上限其实只有
  **~12 MiB**。之前配的 32MB、以及更早的 200MB，全都发不出去。

**修法**：把上传改成**分块流式**（和下载方向对称）——
`upload` 只建立会话拿 `upload_id`，之后每块单独一条 `upload_chunk`
消息，`upload_finish` 时才在扩展侧拼成 `File` 塞进 `input[type=file]`；
中途失败用 `upload_abort` 丢弃。

实测对比（真实 uvicorn，默认配置）：

| 文件 | 旧做法（一条消息） | 新做法（分块流式） |
|---|---|---|
| 30 MiB | ❌ `ConnectionClosedError` | ✅ 完整传输，base64 校验一致 |
| 100 MiB | ❌ `ConnectionClosedError` | ✅ 完整传输，base64 校验一致 |

**那么现在上限由什么决定？内存。** 扩展侧要攒下全部分块的 base64
才能拼出一个完整 `File`，实测堆增量 ≈ 文件大小 × 2.67：

| 文件 | 扩展侧堆增量 |
|---|---|
| 32 MB | 85 MB |
| **64 MB** | **171 MB** |
| 100 MB | 267 MB |
| 200 MB | 532 MB |

MV3 的 Service Worker 常驻内存有限，200MB 那档有被系统回收的风险。
→ 默认取 **64MB**（帧尺寸已不再是约束，内存是）。

新增守卫 **C16f**（不许把整份文件塞进单条消息）、**C16g**
（扩展侧必须真的实现分块接收/收尾/中止 —— 查**函数定义**，
不能只查名字，否则 export 名单会让它"看起来存在"）。

### v2.1.14（2026-09-15）

**按 CodeRabbit 第十二轮审查修复 14 项**（含 2 个安全问题）：

- 🔴 **上传上限只改了一半**（**我上一轮的漏改**）：我把
  `ExtensionBackend.MAX_UPLOAD_BYTES` 降到 32MB，**却没改 schema.json /
  main.py / README 的默认值** —— 它们仍是 200MB 并会一路传下去，
  硬顶形同虚设。→ 四处默认值统一为 33554432，**并且在 main.py 里
  按 `MAX_UPLOAD_BYTES` 钳制配置值**（用户调大也不会突破单条消息的极限）。
  新增守卫 **H2** 逐处核对四个默认值 + 钳制是否存在。
- 🔴 **页面操作超时可能被当成"失败"从而换后端重试**：`callContent` 的
  超时意味着"命令**可能已经在页面里执行了**，只是回执没回来"。
  原样上报成普通失败，上层会换（无头）后端重试 →
  **同一个点击/输入被做两次**。
  → 给 `OpResult` 加 `indeterminate` 标志（与 `declined` 并列的
  "终止性结果"），超时类错误一律标成不确定，上层**不再换后端**，
  而是如实告诉模型"可能已生效，请先看页面状态"。
- **无头下载：不再去写 aiohttp 的私有属性 `_cookie_jar`**：
  上一轮为了"降级到 http 时丢 cookie"，直接替换了 session 的私有字段 ——
  那是实现细节，库一升级就碎。→ 改成**遇到非 HTTPS 跳直接停下**，
  既不外泄 Cookie，也不碰私有 API。
- **`buildWsUrl` 不认 `http://` / `https://`**：面板上用户很自然会粘
  `http://127.0.0.1:5267`，不认的话前缀会整个留在主机名里 →
  拼出 `ws://http://127.0.0.1:5267:5267/...` 这种废地址。
  → 认 http/https 并分别映射到 ws/wss。
- **连接超时会关掉 socket 并重排重连**、**popup 的 refresh 捕获
  `sendMessage` reject**（service worker 被回收时它会 reject，
  不接住会不断产生 unhandled rejection 且弹窗停在旧状态）、
  **`ExtensionBackend.display` 走公开的 `info`**、
  **`op_timeout_ratio` 校验 `0<ratio<1`** 且不再被 5 秒下限顶到框架超时之上。
- **`rotate()` 的文档与代码不符**：docstring 说"世代号一变旧令牌立即失效"，
  但实际 `tv` 是 `sha256(access_token)[:16]`、**与世代号无关** ——
  真正起作用的是 `is_current_token()` 里的 **jti 闸门**。→ 改正文档。

**回归套件 8 项**：
- **B2.7 改用 `new URL()` 做精确 origin 比较**（原来 `startsWith` 太松），
  并把 http/https 归一也纳入用例；顺带发现 `new URL()` 会规范化 IPv6
  （`0:0:0:0:0:0:0:1` → `[::1]`、`::ffff:127.0.0.1` → `[::ffff:7f00:1]`），
  期望值按规范形式写。
- **`bridge_e2e` 的假客户端不再响应 `wait_for`**：它会立即回结果，
  于是"取消泄漏"用例**永远走不到超时路径**（看起来在测超时，其实没测）。
- **`callgraph` 的 D1 改用 AST 结构判断**，不再用 `ast.unparse()` 的
  字符串前缀 —— 前缀匹配会被 `str(event.session_id)` 误命中。
- **`_check_tab_id` 被重复调用三次**、**cookie 导入里有个 no-op 循环**
  （传空 dict 的 `update_cookies` + 吞异常，什么都没做）→ 都删掉。
- **`stubs` 的固定 `/tmp/kira_data` 改为每次唯一的临时目录**。
- `.gitignore` 的 `_click_runner.mjs` 改为匹配 `_click_runner_*.mjs`。

**新增守卫 H2**（见上）。反向验证：把 schema 默认值改回 200MB
→ H2 立即 FAIL。

### v2.1.13（2026-09-15）

**按 CodeRabbit 第十一轮审查修复 12 项**（含 1 个"我声称修了但根本没改"的）：

- 🔴 **第七轮我声称修好的 `buildWsUrl` 其实一行都没改**：
  第七轮的提交信息和 README 都写着「`buildWsUrl` 把裸 IPv6 的冒号当端口
  剥掉 —— 已修」，但 **`protocol.js` 根本没进那次提交**。
  实测 ``::1`` 仍被剥成 ``::`` → 判不出回环 → 生成 `wss://::5267/...`
  这个非法 URL，**扩展连本机都连不上**。
  之后第八、九、十轮都没发现 —— 因为**没有任何检查会去回验"声称"**。
  → 这次真的改了（端口只从带方括号的 IPv6 或单冒号主机里剥；
  裸 IPv6 序列化时补方括号），并**新增行为探针 B2.7**（用 node 真跑
  `protocol.js`，行为不对就红）和**声称核对 H1**。
  B2.7/H1 都做了反向验证：还原成未修改版本 → 立即 FAIL。
- **连接超时后 socket 没收掉**：只 settle 不 close，socket 一直停在
  `CONNECTING`、`state.socket` 仍指向它 → 之后每次 `ensureAlive`/`connect`
  都以为"已有连接"而直接返回，**用户点多少次重连都没用**。
  → 超时即 close、只清自己的引用、并安排重连。
- **上传尺寸估算高估**：base64 长度按 `len*3/4` 硬算，没扣末尾的 `=`。
  `YQ==` 只有 1 字节却算成 3 → **真实大小刚好等于上限的文件被误判超限**。
  → 先减 padding 再换算，现在与真实字节数完全一致。
- **上传上限 200MB 单条消息扛不住**：内容一次性放进 `chunks` 随**一条**
  WebSocket 消息发出，base64 放大 1.33 倍、`json.dumps` 再复制一份 →
  峰值 >500MB，单帧 267MB 本身也会被协议端拒。
  → 默认降到 **32MB**（峰值约 85MB）。
- **`bridge.py` 分块写入失败不 resolve future**："超上限"分支会 resolve，
  写入失败分支只 abort sink → future 一直挂着，`send_command` 要干等到
  `download_timeout`（默认 **600 秒**）才报错。→ 补上 resolve。

**回归套件 6 项**：
- **`INHERITED_OK` 白名单混入通用方法名**（`get`/`append`/`format`/
  `create_task` 等）→ 等于给 A1 开洞：`self.get(...)` / `self.format(...)`
  写错永远不会被报出来。→ 只保留框架真正提供的名字。
  反向验证：加一个 `self.format("x")` → A1 立即 FAIL。
- **`bridge_e2e` 用共享的 `_ext_client.mjs`** → 并发跑会互相覆盖、
  甚至在 finally 里删掉对方的脚本。→ 改用唯一名字的临时文件。
- **`contract.py` 用 `mkdtemp` 不清理**，且**"两边后端都没有该字段就跳过"**
  → 那会放过"渲染层要读的字段两个后端都不返回"这种最严重的情况。
  → 用 `TemporaryDirectory`；去掉跳过。**去掉后立刻暴露真问题**：
  渲染层有个 `get_text` 分支，但没有任何后端方法/工具动作会产生它 ——
  是死代码，已删。
- **`runtime_behavior` 的 R2 未判启动失败就解引用 `p._page`**（启动失败时
  会抛 AttributeError，把"启动失败"伪装成无关崩溃）；**R4 只测一个方向**
  → 补 `R4b` 反向（screenshot_ 最新时应留下 screenshot_），
  否则"永远优先保留 element_"的 bug 也能通过。
- **`security_rules` 的 B2.6 不认简写属性** `{ name, path }` → 只匹配
  `path:` 的话，简写形式的路径泄露检测不出来。→ 同时识别简写。
  反向验证：改成 `{ name, path }` → B2.6 立即 FAIL。
- **`static_audit` 的 A13c 临时文件名写死** → 并发跑会互相覆盖/删文件；
  **`tool_merge` 的 C2 用全仓正则取 action** → 会命中别的工具的 enum。
  → A13c 名字唯一化；C2 改用 `_tool_enum()`。

**文档**：`setup_guide` 补上可选的「允许用户脚本 / Allow User Scripts」
步骤（Chrome 138+ 关着时该功能会提示"不可用"，看起来像浏览器太旧）。

### v2.1.12（2026-09-15）

**按 CodeRabbit 第十轮审查修复 7 项**：

- 🔴 **面板每 3 秒抛一次 ReferenceError**（**我上一轮改出来的回归**）：
  上一轮我把 `const ad = p.allowed_domains || [];` 删掉、改走 `_renderDomains`，
  但漏了下面 `if (!p.read_only && !ad.length)` 仍在用 `ad` →
  可写模式下**初次加载和每 3 秒的轮询都会在这一行抛错**，
  `catch` 只显示"读取失败"，后面的安全提示、确认记录、WebSocket 路径
  **全部停止更新**。
  ⚠️ 只读模式因短路求值（`!p.read_only` 先为 false）**不会**触发，
  所以这个 bug 在只读配置下完全看不出来 —— 极难发现。
  → 改用局部 `allowedCount`。
- **G1 会漏掉模板串插值里的裸标识符**：上一轮我为了修误报，把整个模板串
  替换成 ` `` `，于是 `` `${socket.readyState}` `` 里的 `socket` 也被删了 ——
  G1 报 PASS，运行时却 ReferenceError。
  → 改成**先保留 `${...}` 内容、再删其余模板文本**；
  并按建议加了**自检夹具**（只在插值里放未定义标识符，要求 G1 必须报错）。
- **`no_api` 的报错文案误导**：Chrome 138+ 关掉「允许用户脚本」开关会让
  `chrome.userScripts` **变成 undefined**，与"浏览器版本太低"表现完全一样，
  但原文案只提版本。→ 补上开关排查步骤（并说明这种情况看起来就像不支持）。
- **README 的 `screenshot_dir` 默认值**：写成了"插件数据目录/data/temp"
  把两个位置拼在一起，用户会去插件数据目录里找截图。
  → 改为"空（即用 `data/temp`）"。

**回归套件 4 项**：
- **R3 忽略 `start()` 的失败**：启动失败时 `p.available` 是 false，
  于是 `closed = not p.available` 把**启动失败误报成"空闲清理成功"**（假绿）。
  → 先判 `start()` 返回值。
- **E2 读 `icon.png` 未容错**：文件缺失时 `read_bytes()` 抛 `FileNotFoundError`，
  整个 run 在 E2 中止 → **E3/E4 永不执行**，只记一条笼统失败。
  → 包 try/except，让缺失只让 E2 FAIL。
- **stub 里 `ensure_future` 的 task 无强引用**：事件循环只持弱引用，
  task 可能在异步处理器跑完前被 GC → 弹窗清理/生命周期检查不稳定。
  → 用集合留住 task（完成即丢弃）。
- **G1 未覆盖模板插值**（见上，含自检夹具）。

**新增守卫 `C16e`（面板脚本未声明标识符扫描）**：
本轮那个 `ad` bug 之所以能溜过整个套件，是因为没有任何检查会扫
`index.html` 里的变量引用。新增的扫描会找出"用了但从没声明"的标识符，
并正确处理 `catch (e)` / 箭头函数参数，避免误报。
**反向验证：把 `allowedCount` 改回未声明的 `ad` → C16e 立即 FAIL。**

### v2.1.11（2026-09-15）

**按 CodeRabbit 第九轮审查修复 8 项**（含 2 个安全项）：

- 🔴 **面板存在 XSS**：`bridge.py` 会把扩展上报的事件数据**原样转发**，
  `main.py` 存进 `confirm_log`，而 `web/index.html` 把 `action`/`command`
  和 `reason` **拼进 `conflog.innerHTML`** —— 持有 bridge token 的一方
  可以注入 `<img onerror=...>`，在**已认证的面板**里执行脚本。
  `allowed_domains` / `blocked_domains` 也走同一个注入点。
  → 全部改用 DOM 节点 + `textContent` 渲染，不再拼 HTML 字符串。
- 🔴 **`cookie_get` 导出可以绕过用户确认**：它只读、所以不在
  `WRITE_COMMANDS` 里（正确 —— 不能破坏只读模式/域名白名单的判定），
  但导出的是 `chrome.cookies.getAll` 的**真实取值** = 登录态，
  却因为不在确认集合里被静默放行。
  → 两侧各加一个**「只读但敏感」确认集**（`CONFIRM_ONLY_CMDS` /
  `CONFIRM_ONLY_COMMANDS`），确认判定改用「写操作 ∪ 只读敏感」，
  并补上专门的确认文案。
- **`evaluate` 把脚本异常当成功返回**：注入的包装器会把脚本自身的异常
  吞成 `{ __error: "..." }`，而 `first.error` 只反映
  `chrome.userScripts.execute` 这一层失败 → 脚本抛错时命令被记为**成功**。
  这与 `shared.js` 里 `callContent` 把 `__error` 当错误的做法不一致。
  → 在 `evaluate` 里把 `__error` 翻成抛出的异常。
- **无坐标点击不触发双击**：坐标路径传了 `click_count`，无坐标路径仍在
  循环 `down`/`up`（每次 clickCount 都是 1）→ `dblclick` 永远不触发。
  → 两条路径统一传 `click_count`。
- **弹窗会显示「可读取到 undefined 个标签页」**：`tab_count` 只在
  `listTabs()` 成功时才有，且那个错误被吞掉。→ 渲染前判空。
- **README 的 `screenshot_dir` 默认值写错**：schema 是空串表示"用
  `data/temp`"，`main.py` 与 `HeadlessBackend` 都按 `data/temp` 兜底，
  但 README 指向了别的目录。→ 改为 `data/temp`。

**回归套件 2 项**：
- `_tool_enum()` 会把该工具段里**所有** enum 并起来 → 某个被删掉的
  action 只要还留在别的参数 enum 里，C1 就检测不出来。
  → 只提取 `action` / `mode` 属性自己的 enum。
- C3 只搜索 `main.py`，而 `headless_backend.py` 也是配置消费者
  → 那里的默认值写错不会被发现。→ 两个文件一起查。

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
