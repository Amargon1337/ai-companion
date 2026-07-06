"""Grounding handler — search_with_grounding integration and fallback."""
from __future__ import annotations

import logging
import re as _re
from typing import Any

from companion.llm import client as llm

logger = logging.getLogger(__name__)


async def handle_grounding(message, query: str, ctx_data: dict[str, Any], uid: int,
                           retrieval_mgr, memory_store) -> bool:
    try:
        bundle = retrieval_mgr.select(
            query=query, facts=ctx_data.get("facts", []), reflections=ctx_data.get("reflections", []),
            summaries=ctx_data.get("summaries", []), permanent_notes=ctx_data.get("permanent_notes", ""),
            identity_vault_block=ctx_data.get("identity_vault_block", ""),
            personality_snapshot=ctx_data.get("personality", ""),
            recent_messages=ctx_data.get("recent", []),
            active_goals=ctx_data.get("active_goals", []),
            causal_links=ctx_data.get("causal_links", []),
            predictions=[],
            world_model_context=ctx_data.get("world_model_context", ""),
            user_model_context=ctx_data.get("user_model_context", ""),
        )
        ctx = bundle.to_prompt_block()
        from companion.bot_core import send_typing, send_long_message
        await send_typing(message)
        text, sources = await llm.run_llm(llm.search_with_grounding, query, ctx)
        reply = f"\U0001f50d {text}"
        if sources:
            reply += f"\n\n\U0001f4ce Источники:\n{sources}"
        await send_long_message(message, reply)
        memory_store.log_message(role="assistant", text=text[:500], importance=5, mode="default", user_id=uid)
        return True
    except Exception as e:
        logger.warning(f"Grounding fallback to chat: {e}")
        await message.answer("\u26a0\ufe0f Google Search недоступен. Отвечаю из личной памяти...")
        return False


async def grounding_answer_only(query: str, ctx_data: dict[str, Any],
                                retrieval_mgr) -> str:
    try:
        bundle = retrieval_mgr.select(
            query=query,
            facts=ctx_data.get("facts", []),
            reflections=ctx_data.get("reflections", []),
            summaries=ctx_data.get("summaries", []),
            permanent_notes=ctx_data.get("permanent_notes", ""),
            identity_vault_block=ctx_data.get("identity_vault_block", ""),
            personality_snapshot=ctx_data.get("personality", ""),
            recent_messages=ctx_data.get("recent", []),
            active_goals=ctx_data.get("active_goals", []),
            causal_links=ctx_data.get("causal_links", []),
            predictions=[],
            world_model_context=ctx_data.get("world_model_context", ""),
            user_model_context=ctx_data.get("user_model_context", ""),
        )
        context = bundle.to_prompt_block()
        text, sources = await llm.run_llm(llm.search_with_grounding, query, context)
        if sources:
            return f"\U0001f50d {text}\n\n\U0001f4ce Источники:\n{sources}"
        return f"\U0001f50d {text}"
    except Exception as e:
        logger.warning("Grounding retry failed: %s", e)
        return ""


def should_retry_with_grounding(query: str, critique: dict[str, Any]) -> bool:
    warnings = critique.get("warnings", [])
    factual_trigger = bool(_re.search(
        r"^(?:когда|где|кто|сколько|какая|какой)\b",
        query.lower().strip()
    ))
    return factual_trigger or any("источник" in warning.lower() for warning in warnings)
