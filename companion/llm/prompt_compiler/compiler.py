"""Phase 6.1: Prompt Compiler — Section-based prompt assembly engine.

Replaces monolithic build_system_instruction() with a modular, budget-aware,
cacheable prompt compiler. Each section builds independently and the compiler
assembles them in priority order within the token budget.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from companion.llm.token_budget import estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    """Base unit of a compiled prompt."""
    name: str
    priority: int          # Lower = higher priority (assembled first)
    content: str = ""
    max_tokens: int = 2000
    enabled: bool = True

    @property
    def token_cost(self) -> int:
        return estimate_tokens(self.content)

    def build(self, context: dict[str, Any]) -> str:
        """Override in subclasses or set content directly."""
        return self.content


@dataclass
class CompiledPrompt:
    """Result of prompt compilation."""
    text: str
    sections_included: list[str]
    sections_trimmed: list[str]
    total_tokens: int
    cache_key: str
    token_breakdown: dict[str, int] = field(default_factory=dict)


class PromptCompiler:
    """Assembles PromptSections into a final system instruction within budget."""

    def __init__(self, total_budget: int = 8000) -> None:
        self.total_budget = total_budget
        self._sections: list[PromptSection] = []
        self._cache: dict[str, CompiledPrompt] = {}

    def register(self, section: PromptSection) -> None:
        self._sections.append(section)

    def clear_sections(self) -> None:
        self._sections = []

    def compile(
        self,
        context: dict[str, Any] | None = None,
        budget_override: dict[str, int] | None = None,
    ) -> CompiledPrompt:
        """Compile all registered sections into a final prompt string."""
        ctx = context or {}
        budget_map = budget_override or {}

        # Build all sections
        built: list[tuple[PromptSection, str]] = []
        for sec in self._sections:
            if not sec.enabled:
                continue
            content = sec.build(ctx) if hasattr(sec, 'build') and callable(sec.build) else sec.content
            if content:
                built.append((sec, content))

        # Sort by priority (lower = higher priority)
        built.sort(key=lambda x: x[0].priority)

        # Cache check
        raw_concat = "".join(c for _, c in built)
        cache_key = hashlib.md5(raw_concat.encode("utf-8", errors="replace")).hexdigest()[:16]
        if cache_key in self._cache:
            logger.debug("Prompt cache hit: %s", cache_key)
            return self._cache[cache_key]

        # Assemble within budget
        included: list[str] = []
        trimmed: list[str] = []
        parts: list[str] = []
        token_breakdown: dict[str, int] = {}
        tokens_used = 0

        for sec, content in built:
            sec_budget = budget_map.get(sec.name, sec.max_tokens)
            sec_tokens = estimate_tokens(content)

            # Trim content if it exceeds section budget
            if sec_tokens > sec_budget:
                # Rough character-level trim (3 chars ≈ 1 token)
                max_chars = sec_budget * 3
                content = content[:max_chars] + "\n[...trimmed...]"
                sec_tokens = estimate_tokens(content)

            # Check global budget
            if tokens_used + sec_tokens > self.total_budget:
                trimmed.append(sec.name)
                continue

            parts.append(f"# {sec.name.upper()}\n{content}\n")
            included.append(sec.name)
            token_breakdown[sec.name] = sec_tokens
            tokens_used += sec_tokens

        final_text = "\n".join(parts)
        result = CompiledPrompt(
            text=final_text,
            sections_included=included,
            sections_trimmed=trimmed,
            total_tokens=tokens_used,
            cache_key=cache_key,
            token_breakdown=token_breakdown,
        )

        # Cache (limit to 32 entries)
        if len(self._cache) > 32:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[cache_key] = result

        return result

    def invalidate_cache(self) -> None:
        self._cache.clear()
