class Image:
    def __init__(self, image=None, **kw):
        self.image = image


class Text:
    def __init__(self, text="", **kw):
        self.text = text


class File:
    def __init__(self, file=None, name=None, size=None, **kw):
        self.file = file
        self.name = name
        self.size = size
