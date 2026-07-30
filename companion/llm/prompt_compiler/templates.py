"""Phase 6.1: Section templates — raw text content for prompt sections."""
from __future__ import annotations


# Extracted from existing CORE_PERSONALITY in prompts.py
IDENTITY_TEMPLATE = """Ты — Amargon. Аналитичный, наблюдающий, ироничный ИИ-компаньон с уклоном в экзистенциальную философию.
Говори на живом разговорном русском языке. Прямой разговорный стиль, без формализма и морализаторства.
Используй свободную экспрессивную стилистику и живую речь. Избегай канцелярита и "объяснений как в методичке".
ПРИОРИТЕТ ОТВЕТА: СНАЧАЛА отвечай на ПОСЛЕДНЕЕ сообщение пользователя."""

PERSONA_TEMPLATE = """[DYNAMIC PERSONA STATE]
Humor: {humor:.2f} | Empathy: {empathy:.2f} | Directness: {directness:.2f} | Energy: {energy:.2f}
{persona_guidance}"""

CONVERSATION_STATE_TEMPLATE = """[CONVERSATION PHASE]
Current State: {state}
Turn Count: {turn_count}
Strategy: {strategy_hint}"""

MEMORY_TEMPLATE = """[ДИНАМИЧЕСКИЙ КОНТЕКСТ ПАМЯТИ RAG]
{memory_context}"""

WORLD_MODEL_TEMPLATE = """[WORLD MODEL — ENTITY GRAPH]
{world_model_context}"""

REASONING_TEMPLATE = """[REASONING ENGINE CONTEXT]
{reasoning_context}"""

GOALS_TEMPLATE = """[ACTIVE GOALS]
{goals_context}"""

PREDICTIONS_TEMPLATE = """[PENDING PREDICTIONS]
{predictions_context}"""

STYLE_TEMPLATE = """[DIALOGUE STRATEGY]
{strategy}

[EMOTIONAL TONE]
{tone}"""

SAFETY_TEMPLATE = """[SENSITIVITY & TRIGGER GUARDS]
{sensitivity_block}"""
