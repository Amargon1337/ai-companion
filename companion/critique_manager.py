"""Self-critique logic — response quality evaluation and adjustment."""
from __future__ import annotations

import logging
from typing import Any

from companion.user_model import user_model

logger = logging.getLogger(__name__)


def run_self_critique(query: str, response: str, ctx_data: dict[str, Any]) -> dict[str, Any]:
    try:
        return user_model.critique_response(
            response=response,
            query=query,
            context={
                "facts_count": len(ctx_data.get("facts", [])),
                "has_causal": bool(ctx_data.get("causal_links")),
                "has_predictions": bool(ctx_data.get("predictions")),
            },
        )
    except Exception as e:
        logger.warning("Self critique failed: %s", e)
        return {"flags": [], "confidence": 1.0, "warnings": []}


def apply_critique_to_text(text: str, critique: dict[str, Any]) -> str:
    confidence = critique.get("confidence", 1.0)
    warnings = critique.get("warnings", [])
    flags = critique.get("flags", [])
    if confidence >= 0.75 and not warnings:
        return text
    if "uncertain_language" in flags:
        if warnings:
            return text + "\n\n\u26a0\ufe0f " + "; ".join(warnings[:2])
        return text
    prefix = "Не до конца уверен, но по текущему контексту: " if confidence < 0.55 else "Похоже, что: "
    adjusted = text
    lowered = text.lower()
    if not lowered.startswith(("не до конца уверен", "похоже", "вероятно", "возможно")):
        adjusted = prefix + text[:1].lower() + text[1:] if text else text
    if warnings:
        adjusted += "\n\n\u26a0\ufe0f Уровень уверенности снижен: " + "; ".join(warnings[:2])
    return adjusted
