"""Regression tests for runtime diagnostics."""
from __future__ import annotations

from companion import observability
from companion.models import ContextBundle
from tests.conftest import make_fact


def test_trace_lifecycle_keeps_latest_snapshot():
    observability._active.clear()
    observability._latest.clear()
    observability._recent.clear()

    trace = observability.begin_trace(123, "почему")
    trace.timings_ms["retrieval"] = 12.5
    finished = observability.finish_trace(123)

    assert finished is trace
    assert observability.active_trace(123) is None
    assert observability.latest_trace(123) is trace
    assert trace.timings_ms["total"] >= 0


def test_capture_bundle_records_counts_and_scoring(memory_store):
    # Keep this test independent of provider calls: the bundle is already built.
    fact = make_fact("Иван любит Морзика", importance=8)
    memory_store.add_fact(fact)
    bundle = ContextBundle(facts=[fact])
    trace = observability.begin_trace(123, "Морзик")

    observability.capture_bundle(trace, bundle, {fact.id: 0.91}, memory_store)

    assert trace.counts["facts"] == 1
    assert trace.counts["beliefs"] == 0
    assert trace.facts[0]["id"] == fact.id
    assert trace.facts[0]["similarity"] == 0.91
    observability.finish_trace(123)


def test_memory_stats_reads_storage(memory_store):
    stats = observability.memory_stats(memory_store)

    assert stats["facts"] == 0
    assert stats["beliefs"] == 0
    assert stats["graph_edges"] == 0
    assert stats["faiss_dirty"] is False


def test_replay_round_trip(memory_store):
    trace = observability.begin_trace(123, "Морзик")
    trace.response_text = "Ответ"
    trace.counts = {"facts": 1}
    observability.finish_trace(123)
    trace = observability.latest_trace(123)
    observability.save_replay(trace, memory_store)

    replay = observability.load_replay(memory_store, trace.replay_id)

    assert replay["query"] == "Морзик"
    assert replay["response_text"] == "Ответ"
    assert replay["counts"] == {"facts": 1}
