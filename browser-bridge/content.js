/**
 * Kira Browser Bridge —— 页面内容脚本
 *
 * 所有需要接触页面 DOM 的操作都在这里实现。背景脚本通过
 * chrome.tabs.sendMessage 调用，本脚本用 sendResponse 回传结果。
 *
 * 为什么不放在 background 里：MV3 的 chrome.scripting.executeScript 只能传函数
 * 体，无法传字符串脚本，复杂逻辑放在 content script 里更清晰、也更可调试。
 *
 * 注入时机是 document_idle，但 background 会做一次 ping 探测并在必要时补注入，
 * 所以页面在扩展安装前打开也不会失效。
 */

(function () {
  "use strict";

  // 重复注入保护
  if (window.__kiraBridgeInjected) return;
  window.__kiraBridgeInjected = true;

  // ─── 工具函数 ──────────────────────────────────────────────────────────────

  /** 取可见文本：排除 script/style/noscript 与隐藏元素 */
  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    return true;
  }

  function cleanText(text) {
    return (text || "")
      .replace(/\r\n/g, "\n")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  /** 正文提取：优先 article/main，否则用 body 并剔除噪音节点 */
  function extractText() {
    const candidates = [
      "article", "main", "[role=main]", ".article-content", ".post-content",
      "#content", ".content", "#main", ".main",
    ];

    let root = null;
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el && isVisible(el) && (el.innerText || "").length > 300) {
        root = el;
        break;
      }
    }
    if (!root) root = document.body;
    if (!root) return "";

    // 克隆一份，剔除脚本与导航噪音
    const clone = root.cloneNode(true);
    clone.querySelectorAll(
      "script, style, noscript, iframe, svg, nav, header, footer, aside, " +
      "[aria-hidden=true], .ad, .ads, .advertisement, .cookie, .popup"
    ).forEach((n) => n.remove());

    return cleanText(clone.innerText || clone.textContent || "");
  }

  /** 结构提取：标题树 + 可交互元素，供 AI 判断「下一步点哪里」 */
  function extractOutline() {
    const lines = [];
    const title = document.title || "";
    lines.push(`页面标题: ${title}`);
    lines.push(`网址: ${location.href}`);
    lines.push(`语言: ${document.documentElement.lang || "未知"}`);
    lines.push("");

    // 标题层级
    const headings = Array.from(document.querySelectorAll("h1,h2,h3"))
      .filter(isVisible)
      .slice(0, 60);
    if (headings.length) {
      lines.push("## 标题结构");
      for (const h of headings) {
        const text = cleanText(h.innerText).slice(0, 120);
        if (!text) continue;
        const indent = "  ".repeat(Number(h.tagName[1]) - 1);
        lines.push(`${indent}- ${text}`);
      }
      lines.push("");
    }

    // 可交互元素
    const interactive = collectInteractive();
    if (interactive.length) {
      lines.push(`## 可交互元素（共 ${interactive.length} 个，可用 index 点击）`);
      for (const it of interactive.slice(0, 80)) {
        lines.push(`- [${it.index}] <${it.tag}> ${it.text || "(无文字)"}`);
      }
      lines.push("");
    }

    // 主要链接
    const links = Array.from(document.querySelectorAll("a[href]"))
      .filter((a) => isVisible(a) && cleanText(a.innerText))
      .slice(0, 40);
    if (links.length) {
      lines.push("## 主要链接");
      for (const a of links) {
        const text = cleanText(a.innerText).slice(0, 80);
        lines.push(`- ${text} → ${a.href}`);
      }
    }

    return lines.join("\n");
  }

  /** 收集可点击元素并编号，用于按 index 点击 */
  function collectInteractive() {
    const sel = "a[href], button, input[type=submit], input[type=button], " +
                "input[type=checkbox], input[type=radio], [role=button], " +
                "[onclick], summary, label[for]";
    const nodes = Array.from(document.querySelectorAll(sel)).filter(isVisible);

    return nodes.map((el, i) => ({
      index: i,
      tag: el.tagName.toLowerCase(),
      text: cleanText(el.innerText || el.value || el.getAttribute("aria-label") || "").slice(0, 100),
      el,
    }));
  }

  /** 给元素加一个短暂的高亮，让用户看到 AI 在操作什么 */
  function flash(el) {
    try {
      const prev = el.style.outline;
      const prevOffset = el.style.outlineOffset;
      el.style.outline = "3px solid #4f46e5";
      el.style.outlineOffset = "2px";
      setTimeout(() => {
        el.style.outline = prev;
        el.style.outlineOffset = prevOffset;
      }, 900);
    } catch (_) {}
  }


  /** 鼠标按下/释放的统一目标解析：
   *  有坐标就用坐标处的元素（down/up 才会落在同一个元素上），
   *  没坐标就用 activeElement，再退到 body。 */
  function mouseTarget(payload) {
    const x = payload && payload.x, y = payload && payload.y;
    if (x !== undefined && x !== null && y !== undefined && y !== null) {
      return document.elementFromPoint(Number(x), Number(y)) || document.body;
    }
    return document.activeElement || document.body;
  }

  function describeTarget(el) {
    return el === document.body ? "body"
      : `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}`;
  }

  /** 构造 MouseEvent 的 init（坐标 + 按键） */
  /**
   * 构造 MouseEvent 的 init。
   *
   * ⚠️ `button` 与 `buttons` 语义不同：
   *   button  = 本次事件的**那一个**键（mousemove 时无键 → 0）
   *   buttons = 当前**按住**的键位掩码（没按任何键 → 0）
   * 之前无条件给 buttons=1，等于宣称"鼠标移动时左键是按下状态" ——
   * 拖拽敏感页面会在一次纯 hover 移动上开始拖拽。
   * 所以：不传 button 时（移动）buttons 记为 0。
   */
  function mouseInit(x, y, button, clickCount) {
    const hasButton = button !== undefined && button !== null;
    const btn = !hasButton ? 0
      : (button === "right" ? 2 : button === "middle" ? 1 : 0);
    const buttons = !hasButton ? 0
      : (button === "right" ? 2 : button === "middle" ? 4 : 1);
    return {
      bubbles: true, cancelable: true, view: window,
      clientX: Number(x), clientY: Number(y),
      button: btn,
      buttons,
      clickCount: Number(clickCount) || 1,
    };
  }

  /** 构造 KeyboardEvent 的 init；支持 "Control+a" 这种组合键写法 */
  function keyEventInit(spec) {
    const raw = String(spec || "");
    const parts = raw.split("+").map((p) => p.trim()).filter(Boolean);
    const key = parts.length ? parts[parts.length - 1] : raw;
    const mods = parts.slice(0, -1).map((m) => m.toLowerCase());
    const codeMap = {
      Enter: "Enter", Tab: "Tab", Escape: "Escape", Backspace: "Backspace",
      Delete: "Delete", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
      ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", " ": "Space",
    };
    const keyCode = {
      Enter: 13, Tab: 9, Escape: 27, Backspace: 8, Delete: 46,
      ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, " ": 32,
    }[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
    return {
      bubbles: true, cancelable: true, view: window,
      key: key === " " ? " " : key,
      code: codeMap[key] || (key.length === 1 ? "Key" + key.toUpperCase() : key),
      keyCode, which: keyCode,
      ctrlKey: mods.includes("control") || mods.includes("ctrl"),
      shiftKey: mods.includes("shift"),
      altKey: mods.includes("alt"),
      metaKey: mods.includes("meta") || mods.includes("cmd"),
    };
  }

  function fail(msg) {
    return { __error: msg };
  }

  /** 模拟真实输入（React/Vue 等框架需要触发 input 事件才能同步状态） */
  function setNativeValue(el, value) {
    const proto = Object.getPrototypeOf(el);
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) {
      desc.set.call(el, value);
    } else {
      el.value = value;
    }
  }

  // ─── 命令实现 ──────────────────────────────────────────────────────────────

  const handlers = {
    ping() {
      return { ok: true, url: location.href, ready: !!document.body };
    },

    get_page(payload) {
      const detail = payload.detail || "text";
      let content;
      if (detail === "outline") {
        content = extractOutline();
      } else if (detail === "html") {
        content = document.documentElement.outerHTML;
      } else {
        content = extractText();
      }
      // 有些页面（PDF 预览、图片直链、浏览器内置查看器）没有可读正文。
      // 返回空字符串会让模型以为"页面是空的"，这里说清楚原因。
      if (!content || !content.trim()) {
        const reason = document.contentType && document.contentType !== "text/html"
          ? `该地址是 ${document.contentType}，不是网页，没有可提取的正文`
          : "页面没有可提取的正文（可能仍在加载，或内容在 iframe / Shadow DOM 里）";
        return { url: location.href, title: document.title,
                 content: `（${reason}）`, empty: true };
      }
      return { url: location.href, title: document.title, content };
    },

    get_selection() {
      const sel = window.getSelection ? String(window.getSelection()) : "";
      return { content: cleanText(sel) };
    },

    extract(payload) {
      const { selector, attr, limit } = payload;
      if (!selector) return fail("缺少 selector");

      let nodes;
      try {
        nodes = Array.from(document.querySelectorAll(selector));
      } catch (e) {
        return fail(`选择器语法错误：${e.message}`);
      }

      if (!nodes.length) return { items: [] };

      const max = Math.min(Number(limit) || 50, 200);
      const items = nodes.slice(0, max).map((el) => {
        if (attr) return el.getAttribute(attr);
        return cleanText(el.innerText || el.textContent || "");
      });

      return { items, total: nodes.length, returned: items.length };
    },

    async wait_for(payload) {
      const { selector, text, timeout } = payload;
      const deadline = Date.now() + (Number(timeout) || 10) * 1000;

      while (Date.now() < deadline) {
        if (selector) {
          try {
            const el = document.querySelector(selector);
            if (el && isVisible(el)) return { found: true };
          } catch (e) {
            return fail(`选择器语法错误：${e.message}`);
          }
        }
        if (text) {
          const body = document.body ? (document.body.innerText || "") : "";
          if (body.includes(text)) return { found: true };
        }
        await new Promise((r) => setTimeout(r, 300));
      }
      return { found: false };
    },

    upload_blob(payload) {
      const { selector, name, mime, base64 } = payload;
      if (!selector) return fail("缺少 selector");

      let el;
      try {
        el = document.querySelector(selector);
      } catch (e) {
        return fail(`选择器语法错误：${e.message}`);
      }
      if (!el) return fail(`找不到文件输入框「${selector}」`);
      if (el.tagName.toLowerCase() !== "input" || el.type !== "file") {
        return fail(`元素 ${selector} 不是文件输入框（tag=${el.tagName}, type=${el.type}）`);
      }

      // base64 → Uint8Array → File
      const bin = atob(base64 || "");
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const file = new File([bytes], name || "upload.bin",
                            { type: mime || "application/octet-stream" });

      // 用 DataTransfer 造 FileList 塞进去（绕过系统文件对话框）
      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;

      flash(el);

      // 受控组件（React/Vue）需要这两个事件才会同步内部状态
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));

      return {
        ok: true,
        matched: `input[type=file]${el.id ? "#" + el.id : ""}`,
        name, size: file.size,
      };
    },

    hover(payload) {
      const { selector } = payload;
      let el;
      try { el = document.querySelector(selector); } catch (e) { return fail(`选择器语法错误：${e.message}`); }
      if (!el) return fail(`找不到元素「${selector}」`);
      flash(el);
      const r = el.getBoundingClientRect();
      const opts = {
        bubbles: true, cancelable: true, view: window,
        clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
      };
      el.dispatchEvent(new MouseEvent("mouseover", opts));
      el.dispatchEvent(new MouseEvent("mouseenter", opts));
      el.dispatchEvent(new MouseEvent("mousemove", opts));
      return { ok: true, match: `selector=${selector}` };
    },

    key_press(payload) {
      const { key } = payload;
      if (!key) return fail("缺少 key");
      const el = document.activeElement || document.body;
      const opts = keyEventInit(key);
      el.dispatchEvent(new KeyboardEvent("keydown", opts));
      el.dispatchEvent(new KeyboardEvent("keypress", opts));
      el.dispatchEvent(new KeyboardEvent("keyup", opts));
      // Enter 在表单里通常要真提交，补一下
      if (opts.key === "Enter" && el.form && typeof el.form.requestSubmit === "function") {
        try { el.form.requestSubmit(); } catch (_) {}
      }
      return { ok: true, match: `key=${key}` };
    },

    key_down(payload) {
      const el = document.activeElement || document.body;
      el.dispatchEvent(new KeyboardEvent("keydown", keyEventInit(payload.key)));
      return { ok: true };
    },

    key_up(payload) {
      const el = document.activeElement || document.body;
      el.dispatchEvent(new KeyboardEvent("keyup", keyEventInit(payload.key)));
      return { ok: true };
    },

    mouse_move(payload) {
      const { x, y } = payload;
      const el = document.elementFromPoint(Number(x), Number(y)) || document.body;
      el.dispatchEvent(new MouseEvent("mousemove", mouseInit(x, y)));
      return { ok: true };
    },

    mouse_click(payload) {
      const { x, y, button, click_count } = payload;
      const el = document.elementFromPoint(Number(x), Number(y));
      if (!el) return fail(`坐标 (${x}, ${y}) 处没有元素`);
      flash(el);
      const n = Math.max(1, Number(click_count) || 1);
      for (let i = 0; i < n; i++) {
        const opts = mouseInit(x, y, button, i + 1);
        el.dispatchEvent(new MouseEvent("mousedown", opts));
        el.dispatchEvent(new MouseEvent("mouseup", opts));
        // ⚠️ 只派发一次 click —— 再调 el.click() 会让按钮收到两次点击
        el.dispatchEvent(new MouseEvent("click", opts));
      }
      return { ok: true, match: `${el.tagName.toLowerCase()}@(${x},${y})` };
    },

    mouse_down(payload) {
      // ⚠️ MouseEvent.button 是**数字**，"left" 会被转成 0 →
      // 右键/中键全被当成左键。必须用 mouseInit 做映射。
      // 另外 down/up 必须落在**同一个元素**上，否则按下和释放不是一对。
      const el = mouseTarget(payload);
      el.dispatchEvent(new MouseEvent("mousedown", mouseInit(payload.x, payload.y, payload.button)));
      return { ok: true, match: describeTarget(el) };
    },

    mouse_up(payload) {
      const el = mouseTarget(payload);
      el.dispatchEvent(new MouseEvent("mouseup", mouseInit(payload.x, payload.y, payload.button)));
      return { ok: true, match: describeTarget(el) };
    },

    mouse_wheel(payload) {
      const { delta_x, delta_y } = payload;
      const el = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2)
                 || document.body;
      // dispatchEvent 返回 false 说明监听器调了 preventDefault ——
      // 页面自己处理了这次滚轮，我们**不能再滚一次**，否则会滚双倍距离。
      const consumed = !el.dispatchEvent(new WheelEvent("wheel", {
        bubbles: true, cancelable: true,
        deltaX: Number(delta_x) || 0, deltaY: Number(delta_y) || 0,
      }));
      if (!consumed && Number(delta_y)) {
        window.scrollBy(0, Number(delta_y));
      }
      return { ok: true, consumed };
    },

    mouse_drag(payload) {
      const { start_x, start_y, end_x, end_y, button, steps } = payload;
      const sx = Number(start_x), sy = Number(start_y);
      const ex = Number(end_x), ey = Number(end_y);
      const el = document.elementFromPoint(sx, sy);
      if (!el) return fail(`起点 (${sx}, ${sy}) 处没有元素`);
      flash(el);
      el.dispatchEvent(new MouseEvent("mousedown", mouseInit(sx, sy, button)));
      const n = Math.max(2, Number(steps) || 10);
      for (let i = 1; i <= n; i++) {
        const t = i / n;
        el.dispatchEvent(new MouseEvent("mousemove",
          mouseInit(sx + (ex - sx) * t, sy + (ey - sy) * t, button)));
      }
      el.dispatchEvent(new MouseEvent("mouseup", mouseInit(ex, ey, button)));
      return { ok: true, match: `drag (${sx},${sy})→(${ex},${ey})` };
    },

    scroll(payload) {
      const { direction, amount } = payload;
      const before = window.scrollY;
      const step = Number(amount) || Math.round(window.innerHeight * 0.85);

      // ⚠️ 用瞬时滚动（behavior:"auto"），不要 smooth。
      //    smooth 是**异步动画**：函数立刻返回，但页面还在慢慢滚。
      //    调用方拿到 "已滚动" 后紧接着截图/取文本，拿到的还是旧位置；
      //    而且原来的实现无条件回 changed:true —— 就算已经在底部、
      //    根本没动，也报告"变了"，会让 AI 误判。
      switch (direction) {
        case "up":     window.scrollBy({ top: -step, behavior: "auto" }); break;
        case "down":   window.scrollBy({ top: step, behavior: "auto" }); break;
        case "top":    window.scrollTo({ top: 0, behavior: "auto" }); break;
        case "bottom": window.scrollTo({ top: document.body.scrollHeight, behavior: "auto" }); break;
        default: return fail(`未知滚动方向：${direction}`);
      }

      const after = window.scrollY;
      return {
        changed: Math.abs(after - before) > 1,   // 真实位移，不是"我调用了就变了"
        before,
        after,
        at_bottom: after + window.innerHeight >= document.body.scrollHeight - 2,
      };
    },

    click(payload) {
      const { selector, text, index } = payload;
      let target = null;
      let matched = "";

      if (selector) {
        try {
          target = document.querySelector(selector);
        } catch (e) {
          return fail(`选择器语法错误：${e.message}`);
        }
        if (!target) return fail(`找不到匹配选择器「${selector}」的元素`);
        matched = `selector=${selector}`;

      } else if (text) {
        const nodes = document.querySelectorAll(
          "a, button, [role=button], input[type=submit], input[type=button], " +
          "[onclick], summary, label, li, td, span, div"
        );
        const needle = text.trim().toLowerCase();

        // 优先精确匹配可见文字最短的那个（避免命中巨大的容器）
        const candidates = [];
        for (const el of nodes) {
          if (!isVisible(el)) continue;
          const t = cleanText(el.innerText || el.value || "").toLowerCase();
          if (!t) continue;
          if (t === needle) candidates.push({ el, score: 0, len: t.length });
          else if (t.includes(needle)) candidates.push({ el, score: 1, len: t.length });
        }
        if (!candidates.length) return fail(`页面上找不到包含「${text}」的可点击元素`);
        candidates.sort((a, b) => a.score - b.score || a.len - b.len);
        target = candidates[0].el;
        matched = `text=${text}`;

      } else if (index !== null && index !== undefined) {
        const list = collectInteractive();
        const item = list[Number(index)];
        if (!item) return fail(`index ${index} 超出范围（页面共 ${list.length} 个可交互元素）`);
        target = item.el;
        matched = `index=${index} (${item.tag}: ${item.text})`;

      } else {
        return fail("需要提供 selector、text 或 index 之一");
      }

      if (!target) return fail("未能定位到目标元素");

      // 页面里的 iframe 是无法从顶层文档 querySelector 到的。
      // 直接说"找不到元素"会让模型反复改选择器，白绕一圈；
      // 这里点破原因，让它转而用文字匹配或让用户确认目标位置。
      // ⚠️ 只在「用 selector 定位」时才提示 iframe ——
      // text / index 两条分支不走 selector，若在这里硬套，
      // 会用一个不存在的选择器判断后直接 fail，把本来能点的元素也拦掉。
      if (!selector) {
        /* text / index 定位成功，无需 iframe 提示 */
      } else if (window.top !== window.self) {
        /* 已经在子框架里，继续 */
      } else if (document.querySelector("iframe, frame")) {
        try {
          if (!document.querySelector(selector)) {
            return fail(
              `顶层文档里找不到「${selector}」。该页面包含 iframe，` +
              `目标元素可能在内嵌页面里 —— 扩展目前不注入 iframe，` +
              `建议改用 text 定位，或让用户先切到该框架对应的独立页面。`
            );
          }
        } catch (_) { /* 选择器语法问题交给上面的分支处理 */ }
      }

      // 把元素滚进视口，否则某些框架的点击会失效
      try {
        target.scrollIntoView({ block: "center", behavior: "instant" });
      } catch (_) {}

      flash(target);

      // 按真实事件序列派发，兼容各类前端框架
      const rect = target.getBoundingClientRect();
      const opts = {
        bubbles: true, cancelable: true, view: window,
        clientX: rect.left + rect.width / 2,
        clientY: rect.top + rect.height / 2,
      };
      target.dispatchEvent(new MouseEvent("mousedown", opts));
      target.dispatchEvent(new MouseEvent("mouseup", opts));
      target.dispatchEvent(new MouseEvent("click", opts));

      // ⚠️ 这里**不能**再调 target.click()。
      //
      // 上面三行已经派发过一轮完整事件（mousedown / mouseup / click），
      // 再调一次原生 click() 会让 button / input / [role=button] / [onclick]
      // 等元素**收到两次点击** —— 页面上表现为"点一次提交了两遍"，
      // 下单、发帖、发消息这类操作会重复执行。只有 <a> 因为有默认导航
      // 行为，恰好被旧代码排除了，才掩盖了这么久。
      //
      // 如果某个控件确实需要原生 click 才能响应，应该改用
      // el.dispatchEvent 补发它真正监听的那个事件，而不是重复点一次。

      return { changed: true, match: matched };
    },

    type(payload) {
      const { selector, text, submit, clear_first } = payload;
      if (!selector) return fail("缺少 selector");

      let el;
      try {
        el = document.querySelector(selector);
      } catch (e) {
        return fail(`选择器语法错误：${e.message}`);
      }
      if (!el) return fail(`找不到输入框「${selector}」`);

      const tag = el.tagName.toLowerCase();
      if (tag !== "input" && tag !== "textarea" && !el.isContentEditable) {
        return fail(`元素 ${selector} 不是可输入的表单控件（tag=${tag}）`);
      }

      try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch (_) {}
      flash(el);
      el.focus();

      if (el.isContentEditable) {
        if (clear_first !== false) el.textContent = "";
        el.textContent += text;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
      } else {
        if (clear_first !== false) {
          setNativeValue(el, "");
          el.dispatchEvent(new Event("input", { bubbles: true }));
        }
        setNativeValue(el, text);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }

      if (submit) {
        const keyOpts = { bubbles: true, cancelable: true, key: "Enter", code: "Enter", keyCode: 13, which: 13 };
        el.dispatchEvent(new KeyboardEvent("keydown", keyOpts));
        el.dispatchEvent(new KeyboardEvent("keypress", keyOpts));
        el.dispatchEvent(new KeyboardEvent("keyup", keyOpts));

        // 回车没触发表单提交时，兜底手动提交
        if (el.form) {
          try { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); } catch (_) {}
        }
      }

      return { ok: true };
    },
  };

  // ─── 消息入口 ──────────────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    const handler = handlers[msg.action];
    if (!handler) {
      sendResponse(fail(`未知操作：${msg.action}`));
      return true;
    }

    // wait_for 是异步的，其余同步返回也要统一成 Promise 形态
    Promise.resolve()
      .then(() => handler(msg))
      .then((res) => sendResponse(res))
      .catch((e) => sendResponse(fail(e.message || String(e))));

    return true; // 保持消息通道开放
  });

  console.log("[KiraBridge] content script 已就绪:", location.href);
})();
