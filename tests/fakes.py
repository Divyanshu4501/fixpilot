"""Fake OpenAI client and fake sandbox, so the agent loop can be tested without API or Docker."""

import json
from types import SimpleNamespace

from fixpilot.sandbox import CommandResult


class FakeItem(SimpleNamespace):
    """Stands in for an SDK output item (reasoning / message / function_call)."""

    def model_dump(self, exclude_none: bool = False) -> dict:
        d = dict(vars(self))
        return {k: v for k, v in d.items() if v is not None} if exclude_none else d


def bash_call(command: str, call_id: str = "c1") -> FakeItem:
    return FakeItem(type="function_call", name="bash", arguments=json.dumps({"command": command}),
                    call_id=call_id)


def submit_call(call_id: str = "s1") -> FakeItem:
    return FakeItem(type="function_call", name="submit", arguments="{}", call_id=call_id)


def text_message(text: str) -> FakeItem:
    return FakeItem(type="message", role="assistant",
                    content=[SimpleNamespace(type="output_text", text=text)])


def usage(tokens_in: int = 1000, cached: int = 0, tokens_out: int = 100, reasoning: int = 0):
    return SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out,
                           input_tokens_details=SimpleNamespace(cached_tokens=cached),
                           output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))


def response(*items, status: str = "completed", use=None):
    return SimpleNamespace(output=list(items), status=status, usage=use or usage())


class FakeClient:
    """Returns scripted responses in order (the last one repeats); records every request."""

    def __init__(self, responses: list):
        self._responses = responses
        self.requests: list[dict] = []
        self.responses = self  # so client.responses.create(...) works

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))  # deep copy of what was sent
        i = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[i]


class FakeSandbox:
    """Records commands; returns a fixed output; diff() returns a fixed patch."""

    def __init__(self, output: str = "ok", patch: str = "diff --git a/x.py b/x.py\n"):
        self.output, self.patch = output, patch
        self.commands: list[str] = []

    def run(self, command: str, timeout: int | None = None) -> CommandResult:
        self.commands.append(command)
        return CommandResult(output=self.output, exit_code=0, duration=0.01)

    def diff(self) -> str:
        return self.patch
