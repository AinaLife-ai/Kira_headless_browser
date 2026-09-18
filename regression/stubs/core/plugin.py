"""Minimal stubs so the real headless_browser main.py can be imported."""


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


logger = _Logger()


class Priority:
    HIGH = 10
    MEDIUM = 5
    SYS_HIGH = 1


class _Register:
    def tool(self, **kw):
        def deco(fn):
            return fn
        return deco

    def ws(self, *a, **kw):
        def deco(fn):
            return fn
        return deco

    def api(self, *a, **kw):
        def deco(fn):
            return fn
        return deco

    def page(self, *a, **kw):
        def deco(fn):
            return fn
        return deco


register = _Register()


class _On:
    def llm_request(self, **kw):
        def deco(fn):
            return fn
        return deco

    def im_message(self, **kw):
        def deco(fn):
            return fn
        return deco


on = _On()


class PageMenu:
    def __init__(self, **kw):
        pass


class PluginPage:
    @staticmethod
    def from_folder(x):
        return x


class BasePlugin:
    def __init__(self, ctx, cfg):
        self.ctx = ctx
        self.plugin_cfg = cfg
