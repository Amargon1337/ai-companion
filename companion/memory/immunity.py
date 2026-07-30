"""Immunity Shield — protects high-importance core facts from automatic archival or decay.

Enforces structural immunity rules (categories, flags, importance >= 9, permanent) without NLP keyword scanning.
"""
from __future__ import annotations
from typing import Any

from companion.models import Fact

IMMUNE_CATEGORIES = {
    "family",
    "health",
    "core_values",
    "relationships",
    "identity",
    "core_identity",
    "anchor",
    "medical",
    "values",
    "immune",
}


def is_immune(fact: Fact | dict[str, Any]) -> bool:
    """Check if a fact is immune to automatic archival or decay based on structural properties.

    Immunity is granted structurally to facts where:
    - importance >= 9
    - memory_kind == 'permanent'
    - explicit flags: decay_exempt, anchor_flag, manual_lock
    - category or tag/flag in IMMUNE_CATEGORIES
    """
    if isinstance(fact, Fact):
        importance = fact.importance
        memory_kind = fact.memory_kind
        tags = [str(t).lower() for t in fact.tags]
        category = str(fact.meta.get("category", "")).lower()
        decay_exempt = (
            bool(fact.meta.get("decay_exempt", 0))
            or bool(fact.meta.get("anchor_flag", 0))
            or bool(fact.meta.get("manual_lock", 0))
        )
    else:
        importance = int(fact.get("importance", 5))
        memory_kind = str(fact.get("memory_kind", "event")).lower()
        tags = [str(t).lower() for t in fact.get("tags", [])]
        meta = fact.get("meta", {})
        if isinstance(meta, dict):
            category = str(meta.get("category", fact.get("category", ""))).lower()
            decay_exempt = (
                bool(meta.get("decay_exempt", 0))
                or bool(meta.get("anchor_flag", 0))
                or bool(meta.get("manual_lock", 0))
                or bool(fact.get("decay_exempt", 0))
                or bool(fact.get("anchor_flag", 0))
            )
        else:
            category = str(fact.get("category", "")).lower()
            decay_exempt = bool(fact.get("decay_exempt", 0)) or bool(fact.get("anchor_flag", 0))

    if importance >= 9:
        return True

    if memory_kind == "permanent" or decay_exempt:
        return True

    if category in IMMUNE_CATEGORIES:
        return True

    for t in tags:
        if t in IMMUNE_CATEGORIES:
            return True

    return False
