"""A scripted stand-in for CodexProvider: returns canned turns and records every prompt."""

import threading
from collections.abc import Callable, Mapping
from typing import Any

Step = dict[str, Any] | Exception | Callable[[str, threading.Event], dict[str, Any]]


def turn(reply: str = "ok", actions: list | None = None, needs: list | None = None) -> dict:
    return {"reply": reply, "actions": actions or [], "needs": needs or []}


def call(tool: str, **args: Any) -> dict:
    return {"tool": tool, "args": args}


class FakeProvider:
    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.prompts: list[str] = []
        self.schemas: list[Mapping[str, Any]] = []

    def complete(
        self,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        timeout: float | None = None,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.schemas.append(output_schema)
        if not self.steps:
            raise AssertionError("the fake provider ran out of scripted turns")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(prompt, cancel or threading.Event())
        return step

    @property
    def calls(self) -> int:
        return len(self.prompts)
