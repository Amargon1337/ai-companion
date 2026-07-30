"""Tests for Stage 7: Immunity Shield (Structural rules)."""
import pytest
from companion.models import Fact
from companion.memory.immunity import is_immune


def test_immune_by_structural_category() -> None:
    f1 = Fact(
        fact="Моя мама работает врачом",
        date="2026-07-28",
        importance=6,
        confidence=0.9,
        source="msg",
        meta={"category": "family"},
    )
    f2 = Fact(
        fact="У Ивана аллергия на цитрусовые",
        date="2026-07-28",
        importance=7,
        confidence=0.9,
        source="msg",
        meta={"category": "health"},
    )
    f3 = Fact(
        fact="Иван любит пить чай утром",
        date="2026-07-28",
        importance=5,
        confidence=0.8,
        source="msg",
        meta={"category": "routine"},
    )

    assert is_immune(f1) is True
    assert is_immune(f2) is True
    assert is_immune(f3) is False


def test_immune_by_importance_or_kind() -> None:
    f_imp = Fact(fact="Важное событие", date="2026-07-28", importance=9, confidence=0.9, source="msg")
    f_perm = Fact(fact="Просто факт", date="2026-07-28", importance=5, confidence=0.9, source="msg", memory_kind="permanent")

    assert is_immune(f_imp) is True
    assert is_immune(f_perm) is True


def test_immune_by_tags_and_flags() -> None:
    f_tag = Fact(
        fact="Любимое блюдо",
        date="2026-07-28",
        importance=5,
        confidence=0.8,
        source="msg",
        tags=["core_identity"],
    )
    assert is_immune(f_tag) is True

    # Check dict form as returned by SQLite row
    d_dict = {
        "fact": "Обычное сообщение",
        "importance": 5,
        "memory_kind": "event",
        "tags": ["relationships"],
    }
    assert is_immune(d_dict) is True

    # Check decay_exempt / anchor_flag
    d_anchor = {
        "fact": "Якорь",
        "importance": 5,
        "meta": {"anchor_flag": 1},
    }
    assert is_immune(d_anchor) is True
