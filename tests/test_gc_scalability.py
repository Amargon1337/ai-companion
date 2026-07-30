"""Unit tests for GC Deduplication scalability using DuplicateCandidateProvider."""
from __future__ import annotations

import tempfile
from companion.memory.governor import MemoryGovernor
from companion.memory.hygiene import FaissDuplicateCandidateProvider, MemoryHygieneService
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_gc_candidate_provider_scalability() -> None:
    provider = FaissDuplicateCandidateProvider(top_k=5)

    facts = []
    # Create 48 unrelated facts
    for i in range(48):
        facts.append({
            "id": f"fact_unrel_{i}",
            "fact": f"Уникальное событие номер {i} в совершенно другой области науки {i*13}",
            "importance": 5,
            "status": "active",
            "date": "2026-07-01",
        })

    # Add 2 duplicate facts
    facts.append({
        "id": f"fact_dup_a",
        "fact": "Иван обожает пить свежий эспрессо утром",
        "importance": 5,
        "status": "active",
        "date": "2026-07-10",
    })
    facts.append({
        "id": f"fact_dup_b",
        "fact": "Иван обожает пить свежий эспрессо утром каждый день",
        "importance": 5,
        "status": "active",
        "date": "2026-07-15",
    })

    candidates = provider.get_candidates(facts)
    # Ensure candidate count is much smaller than O(N^2) pairwise (50*49/2 = 1225)
    assert len(candidates) < 50, f"Expected < 50 candidates, got {len(candidates)}"

    # Check that the duplicate pair is in candidates
    found_pair = False
    for f1, f2 in candidates:
        ids = {f1["id"], f2["id"]}
        if "fact_dup_a" in ids and "fact_dup_b" in ids:
            found_pair = True
            break
    assert found_pair is True, "Duplicate pair not found by candidate provider"

    # Now verify MemoryHygieneService uses it properly
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)
        gov = MemoryGovernor(db)
        service = MemoryHygieneService(
            db=db,
            governor=gov,
            stale_days=999,  # disable stale check for this test
            low_activation_threshold=0.0,  # disable low act check
            similarity_threshold=0.75,
            candidate_provider=provider,
        )
        report = service.audit(facts)
        assert len(report.duplicate_candidates) >= 1
        dup_ids = {report.duplicate_candidates[0][0], report.duplicate_candidates[0][1]}
        assert dup_ids == {"fact_dup_a", "fact_dup_b"}
    finally:
        db.close()
