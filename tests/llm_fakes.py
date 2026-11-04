"""A scripted stand-in for the Anthropic client: it replays canned responses, in order."""

import copy
from types import SimpleNamespace

import anthropic
import httpx2


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def thinking() -> SimpleNamespace:
    # With display "omitted" (the default) the text is empty and only the signature comes back.
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def tool_use(name: str, arguments: dict, id: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=arguments)


def reply(*blocks, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=None)


def refusal(category: str) -> SimpleNamespace:
    details = SimpleNamespace(type="refusal", category=category, explanation=None)
    return SimpleNamespace(content=[], stop_reason="refusal", stop_details=details)


def connection_error() -> anthropic.APIConnectionError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


class ScriptedLLM:
    """Looks like AsyncAnthropic for the only call the agent makes: beta.messages.create."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.closed = False
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def close(self) -> None:
        self.closed = True

    async def _create(self, **request):
        # A deep copy, so a later in-place edit of the history would show up as a difference.
        self.calls.append(copy.deepcopy(request))
        if not self.script:
            raise AssertionError("the agent called the model more times than the script has")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step
