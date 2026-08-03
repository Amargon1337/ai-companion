"""Unit tests for retrieval diversity and novelty exploration bonus (Phase 1.6)."""
from __future__ import annotations

import math

from companion import config
from companion.models import Fact


def test_novelty_exploration_bonus_boosts_unseen_facts(memory_store) -> None:
    store = memory_store

    f_unseen = Fact(id="f-nov-1", fact="Python 3.14 unseen fact", date="2026-07-29", importance=5, confidence=0.9, source="msg", status="active")
    f_seen_often = Fact(id="f-nov-2", fact="Python 3.14 seen often fact", date="2026-07-29", importance=5, confidence=0.9, source="msg", status="active")

    store.add_fact(f_unseen)
    store.add_fact(f_seen_often)

    with store.db._conn() as conn:
        conn.execute("UPDATE facts SET facts_sent_count=0 WHERE id='f-nov-1'")
        conn.execute("UPDATE facts SET facts_sent_count=99 WHERE id='f-nov-2'")

    meta_unseen = {"importance": 5, "facts_sent_count": 0}
    meta_seen = {"importance": 5, "facts_sent_count": 99}

    score_unseen = store.semantic_ranker.final_score(0.8, f_unseen, meta_unseen)
    score_seen = store.semantic_ranker.final_score(0.8, f_seen_often, meta_seen)

    # f_unseen should have higher final score due to novelty bonus
    assert score_unseen > score_seen

    expected_diff = config.NOVELTY_EXPLORATION_BETA * (1.0 - 1.0 / math.sqrt(100))
    assert math.isclose(score_unseen - score_seen, expected_diff, rel_tol=1e-4)
