class _L:
    def __init__(self, *a, **k):
        pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def get_logger(*a, **k):
    return _L()
