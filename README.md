# 无头浏览器插件 (Headless Browser Plugin) 1.2.0

让 KiraAI 能够控制无头浏览器进行网页浏览、截图、下载、上传等操作，支持自动加载 cookie 文件实现各账号的半持久化登录。

## 功能特性

- 🌐 **浏览器控制**: 访问网页、点击元素、填写表单、滚动页面
- 🖥️ **真实浏览器接管**: 默认直接使用本机默认浏览器及其真实用户数据，登录态开箱即用；多级回退，无浏览器时自动下载内置 Chromium
- 📸 **截图功能**: 截取页面或元素，自动发送给 AI 查看
- 📁 **文件管理**: 下载文件、保存截图、发送文件给用户
- 🔧 **JS执行**: 在页面中执行 JavaScript 代码
- 🍪 **Cookie管理**: 自动加载 `data/files/cookie/` 目录下所有网站的 Cookie 文件，多站点独立存储，分享插件时安全隔离
- ⚙️ **灵活配置**: 支持无头/可视模式、自定义视口、User-Agent 等

## 安装依赖

插件自带 `requirements.txt`，安装/重载插件时 KiraAI 会自动执行 `pip install playwright aiohttp`，**无需手动安装 pip 依赖**。

浏览器本体也无需手动准备——插件启动时按以下顺序自动选择（见下节"浏览器来源"），前三级都找不到时会**自动下载内置 Chromium**。

> 老版本手动执行过 `playwright install chromium` 的用户不受影响。

## 浏览器来源（v1.2.0 新机制）

插件启动浏览器时按优先级依次尝试，全部失败会自动下载内置 Chromium：

1. **真实浏览器模式（默认开，`use_real_browser_profile`）**：直接使用你本机的默认浏览器（自动探测 Chrome → Edge → Chromium）及其**真实用户数据目录**，完整继承你已登录的账号、书签等数据。
   - ⚠️ 使用前请**完全退出正在运行的该浏览器**（用户数据目录被占用会自动回退到下一级）；
   - ⚠️ 自动化操作将以你的**真实账号身份**执行，请注意隐私与安全风险，不需要时可在配置中关闭；
   - 高级用户可用 `custom_user_data_dir` 手动指定用户数据目录。
2. **插件持久化 profile（默认开，`use_persistent_profile`）**：插件专用浏览器数据目录（登录一次后跨重启保留），不污染真实浏览器。
3. **系统浏览器普通模式**：用系统已装的 Chrome/Edge/Chromium 开一个全新会话。
4. **自动下载内置 Chromium**：以上都不可用时自动执行 `playwright install chromium` 并验证启动。

当前实际使用的来源会写入日志，也可用 `browser_debug` 工具查看。

## 配置说明

在 WebUI 的插件配置中设置以下选项：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `headless` | switch | `true` | 是否以无头模式运行（后台运行） |
| `browser_channel` | enum | `auto` | 浏览器来源：`auto`=自动探测（优先系统默认浏览器）；`chrome`/`msedge`/`chromium`=指定；`bundled`=只用内置 Chromium |
| `use_real_browser_profile` | switch | `true` | 使用真实浏览器的用户数据目录（继承登录态）。使用前需完全退出该浏览器，操作以真实账号身份执行 |
| `custom_user_data_dir` | string | - | 手动指定用户数据目录（留空自动定位），仅真实浏览器模式生效 |
| `use_persistent_profile` | switch | `true` | 真实浏览器不可用时使用插件专用持久化 profile（登录跨重启保留） |
| `default_viewport` | string | `1920x1080` | 浏览器视口大小 |
| `screenshot_dir` | string | `插件数据目录/screenshots` | 截图保存路径 |
| `download_dir` | string | `插件数据目录/downloads` | 文件下载路径 |
| `user_agent` | string | - | 自定义 User-Agent |
| `timeout` | integer | `60` | 页面加载超时时间（秒） |
| `auto_send_screenshot` | enum | `auto` | 截图发送模式：`auto`=自动发送，`manual`=AI决定何时发送 |
| `auto_describe_screenshot` | boolean | `true` | 是否使用VLM自动描述截图 |
| `vlm_model` | model_select | - | 选择用于描述截图的VLM模型（下拉框显示所有已配置模型） |
| `vlm_describe_prompt` | string | - | 自定义VLM提示词（可选，未设置则使用默认模板） |
| `vlm_timeout` | integer | `10` | VLM描述超时时间（秒） |
| `cookies_dir` | string | `data/files/cookie` | Cookie文件存放目录，启动时自动加载该目录下所有 *.json 文件 |
| `upload_allow_any_path` | switch | `true` | 是否允许上传任意目录的文件（true=允许任何路径，false=仅允许白名单目录） |
| `upload_allowed_dirs` | list | `["data/files", "data/temp"]` | 允许上传的目录白名单（每行一个，仅在 `upload_allow_any_path=false` 时生效） |

### 截图发送模式

**`auto` 模式（默认）：**
- 截图后自动发送给用户
- AI 会收到 VLM 对截图的描述
- 适合快速响应，不需要 AI 判断的场景

**`manual` 模式：**
- 截图后不会自动发送
- AI 会先查看截图内容（通过 VLM 分析）
- AI 可以根据内容决定是否发送给用户
- 适合需要 AI 判断截图是否有价值的场景
- AI 可以使用 `browser_send_file` 手动发送

**切换模式：**
在插件配置中修改 `auto_send_screenshot` 选项，然后重载插件。

### VLM 模型配置

截图后插件可以使用 VLM（视觉语言模型）自动分析截图内容，提取页面信息供后续 AI 调用工具使用。

**重要说明：**
基于KiraAI框架传统，用于描述截图的 VLM 模型必须是 **LLM 类型**（不是图像类型）。即使模型支持视觉分析，也需要在 LLM 模型组中配置才能用于描述功能。

**配置步骤：**
1. 在**提供商**设置中，将视觉模型（如 Qwen-VL）添加到 **大语言模型** 组（而不是图像组）
2. 保存提供商配置
3. 在插件配置的 `vlm_model` 下拉框中选择该模型

**支持的视觉模型：**
- `Qwen/Qwen2-VL-72B-Instruct` (硅基流动)
- `gpt-4o` (OpenAI)
- `claude-3-opus` (Anthropic)
- `kimi-k2-0905` (Moonshot)

**方式二：使用系统默认VLM**
在系统设置-默认模型中配置VLM模型，插件会自动使用。

**检查VLM配置：**
调用 `browser_check_vlm` 工具查看当前配置状态和可用模型列表。

### VLM 提示词模板

插件内置了专门为**浏览器自动化优化**的 VLM 提示词模板。当 VLM 分析截图时，会输出以下结构化信息：

```
### 1. 页面基本信息
- 页面标题、URL、页面类型

### 2. 可交互元素清单（关键！）
- 搜索框：位置、placeholder文字
- 按钮：文字和大概位置
- 链接：重要导航链接
- 表单字段：输入框、下拉菜单

### 3. 当前状态
- 页面是否已完全加载？
- 是否有错误提示、弹窗、警告？
- 是否需要登录才能操作？

### 4. 关键内容
- 页面的主要内容/搜索结果是什么？
- 是否有验证码、人机验证？
- 是否有弹窗广告遮挡？

### 5. 建议的下一步操作
- 如果要搜索：点击哪里、输入什么
- 如果要点击：建议的CSS选择器
- 如果要填写表单：每个字段填什么

### 6. 坐标参考
- 重要元素的大致坐标（基于1920x1080）
```

这样后续 LLM 拿到描述后，可以直接调用浏览器工具完成操作！

**自定义提示词：**
如需覆盖默认模板，在插件配置中填写 `vlm_describe_prompt`。自定义提示词将完全替代默认模板。

### 🍪 Cookie 管理

插件支持自动加载 **多个网站** 的 Cookie，方便 AI 以已登录状态操作各类网站。

**存储方式：**
- Cookie 文件统一存放在 `data/files/cookie/` 目录
- 每个网站一个独立的 JSON 文件，如 `chatgpt.json`、`claude.json`、`gemini.json`
- 插件启动或浏览器重启时，自动扫描并加载该目录下所有 `*.json` 文件

**文件格式（标准 Chrome 导出格式）：**
```json
[
  {
    "name": "session-token",
    "value": "xxx",
    "domain": ".chatgpt.com",
    "path": "/",
    "secure": true,
    "httpOnly": true,
    "sameSite": "Lax",
    "expirationDate": 11451418881
  }
]
```
支持嵌套格式（如 `{"cookies": [...]}`），插件会自动解包。

**如何使用：**
1. 从浏览器扩展（如 EditThisCookie、Get cookies.txt）导出对应网站的 Cookie
2. 保存为 JSON 文件，放入 `data/files/cookie/` 目录，插件启动时自动加载（cookie 值仅注入浏览器会话，不会出现在 AI 可见的上下文中）
3. 建议按站点名命名方便管理，如 `chatgpt.json`
4. 重载插件或重启 KiraAI 即可自动加载

**安全隔离：**
`data/files/cookie/` 目录位于项目数据目录下，**不随插件文件打包**：已在 `manifest.json` 的 `exclude` 字段中声明打包排除，并在仓库 `.gitignore` 中忽略该目录。分享插件源码时，你的 Cookie 信息不会泄露。如需分享，请确保移除该目录。
**然而必须注意，你发送的任何内容实际上都经手了你的模型提供商与服务商，请自行评估风险**

## 可用工具

### 浏览器控制

- **`browser_navigate`** - 访问指定 URL
  - `url`: 要访问的网址
  - `wait_until`: 等待状态 (`load`/`domcontentloaded`/`networkidle`)

- **`browser_click`** - 点击页面元素
  - `selector`: CSS 选择器
  - `button`: 鼠标按钮 (`left`/`right`/`middle`)

- **`browser_fill`** - 填写表单字段
  - `selector`: CSS 选择器
  - `value`: 要填写的文本
  - `clear_first`: 是否先清空字段

- **`browser_scroll`** - 滚动页面
  - `direction`: 方向 (`down`/`up`/`bottom`/`top`)
  - `amount`: 滚动距离（像素）

- **`browser_go_back`** - 返回上一页

- **`browser_refresh`** - 刷新页面

### 截图与内容获取

- **`browser_screenshot`** - 截图（根据配置自动发送或由AI决定）
  - `selector`: 元素选择器（可选，默认截取整页）
  - `filename`: 文件名（可选）
  - `full_page`: 是否截取完整页面
  - `send_now`: 是否立即发送（仅manual模式下有效）

- **`browser_get_text`** - 获取页面文本内容
  - `selector`: 元素选择器（可选）
  - `max_length`: 最大返回长度

- **`browser_get_info`** - 获取页面基本信息（标题、URL）

### JavaScript 执行

- **`browser_execute_js`** - 执行 JavaScript 代码
  - `script`: JS 代码字符串

### 文件管理

- **`browser_upload_file`** - 上传文件到指定文件输入框（绕过系统文件对话框）
  - `selector`: 文件输入框的 CSS 选择器（如 `#upload-files`、`input[type=file]`）
  - `file_path`: 要上传文件的**绝对路径**（默认允许任意目录；可通过 `upload_allow_any_path=false` 限定仅允许白名单目录内的文件，出于安全考虑会拒绝其他路径）
  - 返回: 上传成功或失败的信息

- **`browser_download`** - 下载文件（自动发送给用户）
  - `url`: 文件 URL
  - `filename`: 保存文件名（可选）

- **`browser_list_files`** - 列出下载/截图目录的文件
  - `dir_type`: 目录类型 (`downloads`/`screenshots`)
  - `limit`: 最大显示数量

- **`browser_send_file`** - 发送指定文件给用户
  - `filepath`: 文件完整路径
  - `as_image`: 是否作为图片发送

### 键盘模拟

- **`browser_keyboard_type`** - 模拟键盘输入文本
  - `text`: 要输入的文本
  - `delay`: 每个字符之间的延迟（毫秒）

- **`browser_keyboard_press`** - 模拟按下按键
  - `key`: 按键名称，如 `Enter`, `Tab`, `Control+a`, `Shift+Tab`

- **`browser_keyboard_down_up`** - 按住或释放键盘按键（用于复杂组合键）
  - `action`: `down` 或 `up`
  - `key`: 按键名称

### 鼠标模拟

- **`browser_mouse_move`** - 移动鼠标到指定坐标
  - `x`: X坐标
  - `y`: Y坐标
  - `steps`: 步数（越大越平滑）

- **`browser_mouse_click`** - 在指定坐标点击
  - `x`, `y`: 坐标（可选，不指定则在当前位置点击）
  - `button`: 按钮 (`left`/`right`/`middle`)
  - `click_count`: 点击次数

- **`browser_mouse_down_up`** - 按住或释放鼠标按键
  - `action`: `down` 或 `up`
  - `button`: 鼠标按钮

- **`browser_mouse_wheel`** - 鼠标滚轮滚动
  - `delta_x`: 水平滚动距离
  - `delta_y`: 垂直滚动距离（正数向下）

- **`browser_mouse_drag`** - 鼠标拖拽
  - `start_x`, `start_y`: 起始坐标
  - `end_x`, `end_y`: 目标坐标
  - `button`: 鼠标按钮
  - `steps`: 移动步数

- **`browser_hover`** - 将鼠标悬停在指定元素上
  - `selector`: CSS选择器

### 其他

- **`browser_wait`** - 等待元素出现或等待指定时间
  - `seconds`: 等待秒数
  - `selector`: 等待该元素出现

### 调试工具

- **`browser_debug`** - 调试浏览器状态

- **`browser_check_vlm`** - 检查VLM模型配置状态

- **`browser_test_visible`** - 测试浏览器可视模式

## 使用示例

### 示例 1: 访问网页并截图

```
用户: 帮我打开 https://www.example.com 并截图看看

AI:
1. browser_navigate(url="https://www.example.com")
2. browser_screenshot()
```

### 示例 2: 填写表单

```
用户: 打开登录页，输入用户名 test 和密码 123456

AI:
1. browser_navigate(url="https://example.com/login")
2. browser_screenshot()  # 查看页面结构
3. browser_fill(selector="#username", value="test")
4. browser_fill(selector="#password", value="123456")
5. browser_click(selector="#submit-btn")
6. browser_screenshot()  # 确认结果
```

### 示例 3: 下载文件

```
用户: 下载这个文件 https://example.com/file.pdf

AI:
browser_download(url="https://example.com/file.pdf", filename="document.pdf")
```

### 示例 4: 执行 JavaScript

```
用户: 获取当前页面的 cookie

AI:
browser_execute_js(script="document.cookie")
```

### 示例 5: 使用键盘操作

```
用户: 在搜索框输入 "Python" 然后按回车搜索

AI:
1. browser_navigate(url="https://www.baidu.com")
2. browser_click(selector="#kw")  # 聚焦搜索框
3. browser_keyboard_type(text="Python")
4. browser_keyboard_press(key="Enter")
5. browser_wait(seconds=2)
6. browser_screenshot()
```

### 示例 6: 使用鼠标点击坐标

```
用户: 点击屏幕中央（假设按钮在 960, 540）

AI:
1. browser_navigate(url="https://www.example.com")
2. browser_mouse_move(x=960, y=540)
3. browser_mouse_click()  # 在当前位置点击
4. browser_screenshot()
```

### 示例 7: 鼠标拖拽

```
用户: 把左边的滑块拖到右边

AI:
1. browser_navigate(url="https://www.example.com/drag")
2. browser_mouse_drag(start_x=100, start_y=300, end_x=400, end_y=300, steps=20)
3. browser_screenshot()
```

### 示例 8: 模拟组合键

```
用户: 全选页面内容并复制

AI:
1. browser_click(selector="body")  # 聚焦页面
2. browser_keyboard_press(key="Control+a")  # 全选
3. browser_keyboard_press(key="Control+c")  # 复制
4. browser_keyboard_type(text="已复制页面内容")
```

## 注意事项

1. **首次使用需要安装 Playwright**: 运行 `playwright install chromium` 安装浏览器
2. **截图会自动发送**: 使用 `browser_screenshot` 后，图片会自动发送给用户
3. **下载文件会自动发送**: 使用 `browser_download` 后，文件会自动发送给用户
4. **文件保存位置**: 截图和下载的文件保存在插件数据目录下

## 故障排除

### ImportError: playwright
```bash
pip install playwright
playwright install chromium
```

### 页面加载超时
- 检查网络连接
- 增加 `timeout` 配置值
- 使用 `wait_until="domcontentloaded"` 替代 `networkidle`

### 元素找不到
- 先截图查看页面结构
- 检查 CSS 选择器是否正确
- 等待页面完全加载后再操作

## 更新日志

<details>
<summary>点击展开</summary>

### v1.2.0（2026-08-23）
- 新增：浏览器来源四级回退——默认接管本机真实浏览器及其用户数据目录（继承登录态），依次回退 插件持久化 profile → 系统浏览器普通模式 → 自动下载内置 Chromium（此前下载只能手动执行）
- 新增：`browser_channel`（auto/chrome/msedge/chromium/bundled）、`use_real_browser_profile`（默认开）、`custom_user_data_dir`、`use_persistent_profile`（默认开）配置项
- 新增：`requirements.txt`，pip 依赖由 KiraAI 自动安装，无需手动 pip install
- 修复：浏览器启动加并发锁，避免多工具同时触发重复启动
- 修复：关闭浏览器时单步异常不再中断后续清理
- 安全：`browser_send_file` 纳入上传路径白名单限制，防止任意本地文件被外传
- 安全：`browser_download` 文件名净化，防止路径穿越写出下载目录
- 优化：`browser_download` 自动携带浏览器会话的 Cookie 与 User-Agent，可下载登录态资源
- 优化：浏览器启动失败时工具返回友好中文提示（含回退链说明），不再把异常堆栈抛给 AI
- 优化：`browser_debug` 显示当前浏览器来源；cookie 过期时间解析更健壮；配置项类型统一为 switch
- 新增：插件图标（适配 KiraAI WebUI 插件列表显示）
- 修复（按 CodeRabbit 审查意见）：Chromium 自动下载增加 600 秒超时保护；下载的内置 Chromium 复用插件持久化 profile（登录态不丢）；下载文件时 Cookie 按目标域名隔离存放（防跨域重定向泄露）；aiohttp 最低版本提升至 3.14.3（修复旧版本多个安全漏洞）

### v1.1.0
- 历史版本：无头/可视模式、截图+VLM 描述、cookie 自动加载、下载/上传、键鼠模拟等

</details>

## 许可证

AGPL-3.0 License
