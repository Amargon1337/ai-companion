"""Tests for the replay learning data flywheel."""
from __future__ import annotations

import asyncio
import json

from evaluation.learning import (
    annotate_previous_satisfaction,
    benchmark,
    export_golden,
    learn_replays,
    satisfaction_signal,
    tune_weights,
)


def _case():
    return {
        "id": "dog",
        "query": "dog",
        "must_retrieve_ids": ["dog"],
        "top_k": 1,
        "candidates": [
            {"id": "dog", "similarity": 0.9, "importance": 0.8, "recency": 0.7},
            {"id": "job", "similarity": 0.1, "importance": 1.0, "recency": 1.0},
        ],
    }


def test_benchmark_and_tuning_use_labelled_fact_ids():
    metrics = benchmark([_case()], {"semantic": 0.5, "importance": 0.3, "recency": 0.2})
    tuned = tune_weights([_case()])

    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 1.0
    assert tuned["best"]["score"] >= tuned["baseline"]["score"]


def test_export_golden_creates_annotation_template(memory_store, tmp_path):
    payload = {"query": "q", "response_text": "a", "facts": [{"id": "one", "text": "fact"}]}
    memory_store.db.save_retrieval_replay("one", 123, json.dumps(payload))
    output = tmp_path / "golden.json"

    assert export_golden(memory_store.db.path, output, limit=10) == 1
    case = json.loads(output.read_text(encoding="utf-8"))[0]
    assert case["must_retrieve_ids"] == []
    assert case["candidates"][0]["id"] == "one"


def test_satisfaction_annotates_previous_replay_once(memory_store):
    memory_store.db.save_retrieval_replay(
        "one", 123, json.dumps({"query": "Python project", "response_text": "answer"})
    )

    assert annotate_previous_satisfaction(memory_store, 123, "Да, раccкажи ещё про Python")
    assert not annotate_previous_satisfaction(memory_store, 123, "another")
    payload = json.loads(memory_store.db.get_retrieval_replay("one")["payload"])
    assert payload["satisfaction"]["label"] == "yes"
    assert satisfaction_signal("Нет, это ошибка", payload)["label"] == "no"


def test_replay_learning_persists_llm_annotation(memory_store, monkeypatch):
    payload = {"query": "q", "response_text": "a", "facts": []}
    memory_store.db.save_retrieval_replay("one", 123, json.dumps(payload))

    async def fake_oneshot(_prompt):
        return '{"better_memory_possible":false,"promote_fact_ids":[],"irrelevant_fact_ids":[],"notes":"ok"}'

    monkeypatch.setattr("evaluation.learning.aio_oneshot", fake_oneshot)
    assert asyncio.run(learn_replays(memory_store, limit=1)) == 1
    learned = json.loads(memory_store.db.get_retrieval_replay("one")["payload"])
    assert learned["learning"]["notes"] == "ok"
