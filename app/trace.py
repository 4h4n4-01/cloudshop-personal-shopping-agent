"""Per-session activity log so the GUI can show what every agent did and why."""
import time
from typing import Any


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log(self, agent: str, kind: str, title: str, detail: Any = None) -> None:
        """kind: llm | tool | decision | human | payment | error"""
        self.events.append(
            {"ts": time.time(), "agent": agent, "kind": kind, "title": title, "detail": detail}
        )
