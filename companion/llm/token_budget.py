"""Cheap, deterministic input-token budgeting without extra API calls."""
from __future__ import annotations

import math
from typing import Any


def estimate_tokens(value: str) -> int:
    """Conservatively estimate tokens for mixed Russian/English text."""
    if not value:
        return 0
    return max(1, math.ceil(len(value) / 3))


def history_text(message: dict[str, Any]) -> str:
    parts = message.get("parts", [])
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            chunks.append(str(part.get("text", "")))
        else:
            chunks.append(str(getattr(part, "text", part)))
    return "\n".join(chunks)


def trim_history(history: list[dict[str, Any]], token_budget: int) -> list[dict[str, Any]]:
    """Keep the newest complete turns that fit the budget."""
    if token_budget <= 0:
        return []

    selected: list[dict[str, Any]] = []
    used = 0
    for message in reversed(history):
        cost = estimate_tokens(history_text(message)) + 4
        if used + cost > token_budget:
            break
        selected.append(message)
        used += cost

    selected.reverse()
    while selected and selected[0].get("role") == "model":
        selected.pop(0)
    return selected
