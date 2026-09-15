class MessageChain:
    def __init__(self, elements=None):
        self.elements = elements or []


class KiraMessageBatchEvent:
    def __init__(self, session="qq:dm:1"):
        self.session = session
