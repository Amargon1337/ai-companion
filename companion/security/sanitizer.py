"""Sanitizer for user input to prevent prompt injections."""
from __future__ import annotations

import re

_INJECTION_MARKERS = re.compile(
    r"(систем\w*\s+(?:правил\w*|инструкц\w*))|"
    r"(обязан\s+выполнять)|"
    r"(игнорируй\s+(?:предыдущ\w*|все\w*|систем\w*))|"
    r"(ты\s+должен\s+(?:теперь|отныне|всегда))|"
    r"(нов\w*\s+директив\w*)|"
    r"(ignore\s+(?:all\s+)?(?:previous|prior|system|developer)\s+(?:instructions?|messages?|prompts?))|"
    r"(system\s+(?:prompt|message|instructions?|rules?))|"
    r"(developer\s+(?:message|instructions?))|"
    r"(you\s+are\s+now)|"
    r"(follow\s+(?:these|my)\s+instructions?)|"
    r"(^|\n)\s*(?:system|developer|assistant)\s*:",
    re.IGNORECASE
)

_SANITIZED_DANGEROUS_TAGS = re.compile(
    r"[‹›]\s*/?\s*(?:script|iframe|object|embed|style|system|developer|assistant|tool|function)\b",
    re.IGNORECASE,
)


def _looks_like_injection(text: str | None) -> bool:
    if not text:
        return False
    return bool(_INJECTION_MARKERS.search(text) or _SANITIZED_DANGEROUS_TAGS.search(text))


def sanitize_markup(text: str | None) -> str | None:
    """Finds XML/HTML-like tags and replaces < and > with ‹ and › inside matches."""
    if text is None or not text.strip():
        return text

    pattern = re.compile(r'</?\s*([a-zA-Z_][\w\-]*)\s*/?>')

    def replace(match: re.Match) -> str:
        val = match.group(0)
        return val.replace('<', '‹').replace('>', '›')

    return pattern.sub(replace, text)
