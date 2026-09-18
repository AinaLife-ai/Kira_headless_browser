class LLMRequest:
    def __init__(self, system_prompt=None, tool_set=None):
        self.system_prompt = system_prompt or []
        self.tool_set = tool_set


class LLMModelClient:
    pass
