"""A fake, semantics-faithful Playwright used to probe the plugin's lifecycle.

Models the parts that matter for leak analysis:
  * context.pages is a LIVE list of every page in the context
  * a popup (target=_blank / window.open) appends to it
  * page.close() removes that page; context.close() removes all
  * pages can die on their own (user closes the tab)

Everything is counted in STATS so the test can assert on it.
"""

STATS = {
    "pages_created": 0,
    "pages_closed": 0,
    "contexts_created": 0,
    "contexts_closed": 0,
    "browsers_launched": 0,
    "playwright_started": 0,
    "playwright_stopped": 0,
}

#: set to True to make every launch path fail, so we can exercise level-4 fallback
ALL_LAUNCHES_FAIL = False


class FakePage:
    def __init__(self, ctx, url="about:blank"):
        self._ctx = ctx
        self._url = url
        self._closed = False
        self._title = "fake"
        self._timeout = None
        STATS["pages_created"] += 1

    # --- plugin-facing surface -------------------------------------------
    @property
    def url(self):
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return self._url

    async def title(self):
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return self._title

    async def goto(self, url, **kw):
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        self._url = url
        return None

    def set_default_timeout(self, ms):
        self._timeout = ms

    def is_closed(self):
        """真实 Playwright 的 Page.is_closed() 是同步方法"""
        return self._closed

    async def evaluate(self, script):
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return 1

    async def close(self):
        if self._closed:
            return
        self._closed = True
        STATS["pages_closed"] += 1
        if self in self._ctx.pages:
            self._ctx.pages.remove(self)

    # --- events -----------------------------------------------------------
    def _die_by_itself(self):
        """Simulate the user closing this tab in a real browser window."""
        self._closed = True
        if self in self._ctx.pages:
            self._ctx.pages.remove(self)

    def _open_popup(self, url="https://popup.example"):
        """Simulate a target=_blank / window.open from this page.

        真实 Playwright 会在页面打开时触发 context 的 "page" 事件，
        插件正是靠它回收弹窗 —— 这里必须同样触发。
        """
        p = FakePage(self._ctx, url)
        self._ctx.pages.append(p)
        self._ctx._fire("page", p)
        return p


class FakeContext:
    def __init__(self, browser=None, **kw):
        self.pages = []
        self._browser = browser
        self._closed = False
        self._handlers = {}
        STATS["contexts_created"] += 1

    @property
    def browser(self):
        return self._browser

    def on(self, event, fn):
        self._handlers.setdefault(event, []).append(fn)

    def _fire(self, event, *args):
        """同步派发事件；回调是 async 就丢进事件循环。"""
        import asyncio
        for fn in self._handlers.get(event, []):
            r = fn(*args)
            if asyncio.iscoroutine(r):
                asyncio.ensure_future(r)

    async def new_page(self):
        p = FakePage(self)
        self.pages.append(p)
        return p

    async def add_cookies(self, cookies):
        return None

    async def cookies(self, url=None):
        return []

    async def close(self):
        if self._closed:
            return
        self._closed = True
        STATS["contexts_closed"] += 1
        for p in list(self.pages):
            p._closed = True
            STATS["pages_closed"] += 1
        self.pages.clear()


class FakeBrowser:
    def __init__(self):
        STATS["browsers_launched"] += 1
        self._closed = False

    async def new_context(self, **kw):
        return FakeContext(browser=self)

    async def close(self):
        self._closed = True


class _Chromium:
    async def launch(self, **kw):
        if ALL_LAUNCHES_FAIL:
            raise RuntimeError("fake: launch failed")
        return FakeBrowser()

    async def launch_persistent_context(self, user_data_dir, **kw):
        if ALL_LAUNCHES_FAIL:
            raise RuntimeError("fake: persistent launch failed")
        return FakeContext(browser=None, **kw)


class _Playwright:
    def __init__(self):
        self.chromium = _Chromium()

    async def stop(self):
        STATS["playwright_stopped"] += 1


class _Starter:
    async def start(self):
        STATS["playwright_started"] += 1
        return _Playwright()


def async_playwright():
    return _Starter()
