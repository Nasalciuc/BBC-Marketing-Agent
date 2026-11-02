"""
One structured answer from Claude: a forced client tool, optionally after web searches (the Anthropic
server tool). Returns the tool's input, the URLs the searches actually returned — so a fact's source
can be checked against what was really found — and the usage.

`Call` is a plain async callable so the tests use a fake.
"""
from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_TURNS = 5
MAX_TOKENS = 16000


class PlanningError(RuntimeError):
    pass


@dataclass
class ToolAnswer:
    data: dict[str, Any]
    sources: set[str] = field(default_factory=set)
    usage: dict[str, Any] = field(default_factory=dict)      # model, input_tokens, output_tokens, searches


class Call(Protocol):
    def __call__(self, system: str, prompt: str, tool: dict[str, Any], *, search: bool = False,
                 max_searches: int = 8) -> Awaitable[ToolAnswer]: ...


def _add(usage: dict[str, Any], response: Any) -> None:
    u = getattr(response, "usage", None)
    if u is None:
        return
    usage["input_tokens"] = usage.get("input_tokens", 0) + (getattr(u, "input_tokens", 0) or 0)
    usage["output_tokens"] = usage.get("output_tokens", 0) + (getattr(u, "output_tokens", 0) or 0)
    server = getattr(u, "server_tool_use", None)
    usage["searches"] = usage.get("searches", 0) + (getattr(server, "web_search_requests", 0) or 0)


def anthropic_call(model: str, api_key: str, *, client: Any = None) -> Call:
    """`client` is for the tests (a fake with `messages.create`)."""
    if client is None:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=300, max_retries=2)

    async def call(system: str, prompt: str, tool: dict[str, Any], *, search: bool = False,
                   max_searches: int = 8) -> ToolAnswer:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        sources: set[str] = set()
        usage: dict[str, Any] = {"model": model}
        forced = not search
        for _ in range(MAX_TURNS):
            # max_uses counts per request; a continued turn is a new request, so the cap is kept here
            left = max_searches - usage.get("searches", 0)
            tools: list[dict[str, Any]] = [tool]
            if search:
                tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": max(1, left)})
            if left <= 0:
                forced = True
            choice = {"type": "tool", "name": tool["name"]} if forced else {"type": "auto"}
            response = await client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system, tools=tools,
                                                    tool_choice=choice, messages=messages)
            _add(usage, response)
            for block in response.content:
                if block.type == "web_search_tool_result" and isinstance(block.content, list):
                    sources |= {r.url for r in block.content if getattr(r, "url", None)}
            if response.stop_reason == "max_tokens":
                # a tool call cut off here is incomplete: never use it
                raise PlanningError(f"{model}: the answer to {tool['name']} was cut off at {MAX_TOKENS} tokens")
            for block in response.content:
                if block.type == "tool_use" and block.name == tool["name"]:
                    return ToolAnswer(dict(block.input) if isinstance(block.input, dict) else {}, sources, usage)
            content = [b.model_dump(exclude_none=True) for b in response.content]
            messages = [*messages, {"role": "assistant", "content": content}]
            if response.stop_reason == "pause_turn":       # a long search turn: let it go on
                continue
            # it stopped without the tool: ask for it, with what it found already in the conversation
            messages.append({"role": "user", "content": f"Now call {tool['name']} with what you found."})
            forced = True
        raise PlanningError(f"{model} gave no {tool['name']} after {MAX_TURNS} turns")

    return call
