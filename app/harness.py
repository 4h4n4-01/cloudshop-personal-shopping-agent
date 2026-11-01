"""Agent harness (runtime layer): the manual agentic loop that runs one tool-using agent.

Given a system prompt, the task, the agent's tool schemas and handlers, it drives the
LLM ↔ tool ↔ observation loop until the agent calls its `stop_tool` to submit a final answer (or
runs out of turns). We own this loop - rather than using the SDK's tool runner - so that every
tool call can be written to the user-visible trace, which is what makes the agent's actions
inspectable. The model access itself lives in `app/llm.py` (the model-provider layer).
"""
import json
from typing import Any, Callable

from .llm import _check, _extra, client


def run_tools(
    system: str,
    user: str,
    tools: list[dict],
    handlers: dict[str, Callable[..., Any]],
    on_tool: Callable[[str, dict, Any], None],
    stop_tool: str,
    max_turns: int = 10,
) -> None:
    from .config import CLAUDE_MODEL

    messages: list[dict] = [{"role": "user", "content": user}]
    for _ in range(max_turns):
        resp = client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            **_extra(),
        )
        _check(resp)
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return
        results, finished = [], False
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input)
            try:
                out = handlers[tu.name](**args)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out)})
            except Exception as e:  # report tool errors back to the model instead of crashing
                out = {"error": str(e)}
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(e), "is_error": True})
            on_tool(tu.name, args, out)
            finished = finished or tu.name == stop_tool
        messages.append({"role": "user", "content": results})
        if finished:
            return
