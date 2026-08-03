"""Unit tests for ingestion quarantine and fact validation (Phase 1.6)."""
from __future__ import annotations

import os
import tempfile

from companion.models import Fact
from companion.memory.store import MemoryStore
from companion.memory.policies.validation_policy import FactValidationPolicy


def test_fact_validation_policy_thresholds() -> None:
    policy = FactValidationPolicy(min_confidence=0.3)

    # 1. Normal fact
    d_ok = policy.evaluate({}, {"confidence": 0.8, "tags": ["python"]})
    assert d_ok.action == "activate"

    # 2. Low confidence fact
    d_low = policy.evaluate({}, {"confidence": 0.2, "tags": []})
    assert d_low.action == "quarantine"
    assert "confidence" in d_low.reason

    # 3. Hypothetical fact
    d_hyp = policy.evaluate({}, {"confidence": 0.9, "tags": ["hypothetical"]})
    assert d_hyp.action == "quarantine"
    assert "hypothetical" in d_hyp.reason

    # 4. Unverified / Contradiction fact
    d_contra = policy.evaluate({"has_contradiction": True}, {"confidence": 0.9, "tags": []})
    assert d_contra.action == "quarantine"


def test_memory_store_ingestion_quarantine(memory_store) -> None:
    store = memory_store

    f_active = Fact(id="f-q-1", fact="Python 3.14 released", date="2026-07-29", importance=7, confidence=0.9, source="msg", status="active")
    f_quar1 = Fact(id="f-q-2", fact="Maybe user likes Java", date="2026-07-29", importance=3, confidence=0.2, source="msg", status="active")
    f_quar2 = Fact(id="f-q-3", fact="User might move to Mars", date="2026-07-29", importance=5, confidence=0.8, source="msg", tags=["hypothetical"], status="active")

    store.add_fact(f_active)
    store.add_fact(f_quar1)
    store.add_fact(f_quar2)

    # Check DB status
    db1 = store.get_fact("f-q-1")
    db2 = store.get_fact("f-q-2")
    db3 = store.get_fact("f-q-3")

    assert db1 is not None and db1.status == "active"
    assert db2 is not None and db2.status == "quarantine"
    assert db3 is not None and db3.status == "quarantine"

    # Quarantined facts should not appear in list_facts("active")
    active_list = store.list_facts("active")
    active_ids = [f.id for f in active_list]
    assert "f-q-1" in active_ids
    assert "f-q-2" not in active_ids
    assert "f-q-3" not in active_ids
