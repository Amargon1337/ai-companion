"""Tests for deterministic and replay-based evaluation."""
# The fixtures intentionally preserve mixed-script Russian text from real
# replay data; RUF001 would incorrectly suggest changing its semantics.
# ruff: noqa: RUF001
from __future__ import annotations

import json

from evaluation.runner import aggregate, compare_baseline, evaluate_scenario, load_scenarios, run_evaluation


def test_offline_scenario_metrics():
    scenario = {
        "id": "dog",
        "suite": "memory",
        "query": "Как зовут пcа?",
        "top_k": 1,
        "must_retrieve": ["Морзик"],
        "must_not_retrieve": ["кофе"],
        "candidates": [
            {"id": "dog", "text": "Пcа зовут Морзик", "importance": 9},
            {"id": "coffee", "text": "Иван любит кофе", "importance": 6},
        ],
    }

    result = evaluate_scenario(scenario)

    assert result["recall"] == 1.0
    assert result["precision"] == 1.0
    assert result["forbidden_retrievals"] == 0
    assert result["selected_ids"] == ["dog"]


def test_replay_scenario_reads_sqlite(memory_store):
    payload = {
        "facts": [{"id": "dog", "text": "Пcа зовут Морзик", "retrieval_score": 0.9, "similarity": 0.8}],
        "timings_ms": {"total": 42},
        "input_tokens": 100,
        "response_text": "Его зовут Морзик",
    }
    memory_store.db.save_retrieval_replay("replay-1", 123, json.dumps(payload, ensure_ascii=False))
    scenario = {
        "id": "replay-dog",
        "suite": "memory",
        "mode": "replay",
        "replay_id": "replay-1",
        "query": "Как зовут пcа?",
        "must_retrieve": ["Морзик"],
        "response_must_contain": ["Морзик"],
    }

    result = evaluate_scenario(scenario, db_path=memory_store.db.path)

    assert result["recall"] == 1.0
    assert result["response_recall"] == 1.0
    assert result["latency_ms"] == 42
    assert result["tokens"] == 100


def test_load_scenarios_ignores_baseline(tmp_path):
    (tmp_path / "memory.json").write_text('[{"id":"one","query":"q"}]', encoding="utf-8")
    (tmp_path / "baseline.json").write_text('{"recall":1}', encoding="utf-8")

    scenarios = load_scenarios(tmp_path)

    assert len(scenarios) == 1
    assert scenarios[0]["suite"] == "memory"


def test_baseline_comparison_detects_regression():
    metrics = {
        "recall": 0.8, "precision": 1.0, "mrr": 1.0, "diversity": 1.0,
        "response_recall": 1.0, "forbidden_retrievals": 0, "response_violations": 0,
        "latency_ms": 10, "tokens": 100,
    }
    baseline = dict(metrics, recall=0.9)

    comparison = compare_baseline(metrics, baseline)

    assert comparison["regressions"] == 1
    assert comparison["changes"]["recall"] < 0


def test_aggregate_exposes_release_metric_names():
    result = aggregate([{
        "recall": 1.0, "precision": 0.5, "mrr": 1.0, "diversity": 1.0,
        "response_recall": 1.0, "latency_ms": 12, "tokens": 80,
        "average_similarity": 0.2, "average_score": 0.7,
        "forbidden_retrievals": 0, "response_violations": 0,
    }])

    assert result["memory_recall"] == 1.0
    assert result["retrieval_precision"] == 0.5
    assert result["average_latency_ms"] == 12
    assert result["average_tokens"] == 80


def test_seed_evaluation_runs():
    report = run_evaluation(__import__("pathlib").Path("evaluation"))

    assert report["metrics"]["scenarios"] >= 5
    assert set(report["suites"]) >= {"memory", "retrieval", "reasoning", "personality", "hallucination"}
    assert report["suite_metrics"]["memory"]["scenarios"] == 2
