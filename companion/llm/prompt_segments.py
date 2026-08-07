"""Typed prompt segments and deterministic trust-boundary rendering."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from companion.security.sanitizer import sanitize_markup


class PromptTrust(StrEnum):
    SYSTEM_POLICY = "system_policy"
    APPLICATION_INSTRUCTION = "application_instruction"
    TOOL_RESULT = "tool_result"
    USER_MESSAGE = "user_message"
    RETRIEVED_MEMORY = "retrieved_memory"
    DOCUMENT_CONTENT = "document_content"
    MODEL_DERIVED_MEMORY = "model_derived_memory"
    EXTERNAL_CONTENT = "external_content"


@dataclass(frozen=True)
class PromptSegment:
    trust: PromptTrust
    content: str
    source_id: str = ""


def render_segment(segment: PromptSegment) -> str:
    """Render data as data, never as an executable instruction.

    The rendering is deliberately uniform for all untrusted classes.  We do
    not rely on an LLM obeying a sentence such as "ignore instructions"; the
    application policy is rendered separately and untrusted data is wrapped in
    an immutable, typed record with escaped tag-like markup.
    """
    text = sanitize_markup(segment.content) or ""
    if segment.trust in {PromptTrust.SYSTEM_POLICY, PromptTrust.APPLICATION_INSTRUCTION}:
        return text
    source = f' source="{segment.source_id}"' if segment.source_id else ""
    return (
        f'<data trust="{segment.trust.value}"{source}>\n'
        f"{text}\n"
        "</data>"
    )


def render_segments(segments: Iterable[PromptSegment]) -> str:
    return "\n\n".join(render_segment(segment) for segment in segments if segment.content)


def user_message(text: str, source_id: str = "") -> PromptSegment:
    return PromptSegment(PromptTrust.USER_MESSAGE, text, source_id)


def retrieved_memory(text: str, source_id: str = "") -> PromptSegment:
    return PromptSegment(PromptTrust.RETRIEVED_MEMORY, text, source_id)


def document_content(text: str, source_id: str = "") -> PromptSegment:
    return PromptSegment(PromptTrust.DOCUMENT_CONTENT, text, source_id)
