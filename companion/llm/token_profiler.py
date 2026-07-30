"""Phase 6.3: Token Profiler — per-section token usage breakdown."""
from __future__ import annotations

from dataclasses import dataclass, field
from companion.llm.token_budget import estimate_tokens


@dataclass
class TokenProfile:
    """Token usage breakdown for a single turn."""
    section_tokens: dict[str, int] = field(default_factory=dict)
    output_tokens: int = 0

    @property
    def total_input(self) -> int:
        return sum(self.section_tokens.values())

    @property
    def total(self) -> int:
        return self.total_input + self.output_tokens

    def report(self) -> str:
        lines = []
        for name, tokens in sorted(self.section_tokens.items(), key=lambda x: -x[1]):
            lines.append(f"  {name:<20s} {tokens:>6d} tokens")
        lines.append(f"  {'Output':<20s} {self.output_tokens:>6d} tokens")
        lines.append(f"  {'─' * 28}")
        lines.append(f"  {'TOTAL':<20s} {self.total:>6d} tokens")
        return "\n".join(lines)


class TokenProfiler:
    """Tracks token usage per section and per turn."""

    def __init__(self) -> None:
        self._history: list[TokenProfile] = []

    def profile_prompt(self, token_breakdown: dict[str, int], output_text: str = "") -> TokenProfile:
        """Creates a TokenProfile from the compiled prompt breakdown."""
        tp = TokenProfile(
            section_tokens=dict(token_breakdown),
            output_tokens=estimate_tokens(output_text),
        )
        self._history.append(tp)
        # Keep last 100 profiles
        if len(self._history) > 100:
            self._history = self._history[-100:]
        return tp

    @property
    def average_total(self) -> float:
        if not self._history:
            return 0.0
        return sum(p.total for p in self._history) / len(self._history)

    @property
    def last_profile(self) -> TokenProfile | None:
        return self._history[-1] if self._history else None
