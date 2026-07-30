"""Evaluation runner for offline retrieval fixtures and production replays."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from companion.memory.text_sim import text_overlap


@dataclass(frozen=True)
class SelectedFact:
    fact_id: str
    text: str
    score: float
    similarity: float | None = None


def load_scenarios(directory: Path) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.name in {"baseline.json", "report.json"}:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON array")
        for item in data:
            if not isinstance(item, dict) or not item.get("id") or not item.get("query"):
                raise ValueError(f"Invalid scenario in {path}")
            scenario = dict(item)
            scenario["suite"] = path.stem
            scenarios.append(scenario)
    ids = [scenario["id"] for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation scenario IDs must be unique")
    return scenarios


def offline_retrieve(scenario: dict[str, Any]) -> tuple[list[SelectedFact], float]:
    started = time.perf_counter()
    query = str(scenario["query"])
    candidates = scenario.get("candidates", [])
    ranked: list[SelectedFact] = []
    for index, candidate in enumerate(candidates):
        text = str(candidate.get("text", ""))
        similarity = text_overlap(query, text)
        importance = max(0.0, min(1.0, float(candidate.get("importance", 5)) / 10.0))
        score = 0.75 * similarity + 0.25 * importance
        ranked.append(SelectedFact(str(candidate.get("id", index)), text, score, similarity))
    ranked.sort(key=lambda fact: fact.score, reverse=True)
    top_k = max(1, int(scenario.get("top_k", 5)))
    return ranked[:top_k], (time.perf_counter() - started) * 1000


def replay_retrieve(scenario: dict[str, Any], db_path: Path) -> tuple[list[SelectedFact], dict[str, Any]]:
    replay_id = str(scenario.get("replay_id", ""))
    if not replay_id:
        raise ValueError(f"Replay scenario {scenario['id']} has no replay_id")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT payload FROM retrieval_replays WHERE replay_id=?", (replay_id,)).fetchone()
    if not row:
        raise ValueError(f"Replay {replay_id} not found")
    payload = json.loads(row[0])
    facts = [
        SelectedFact(
            str(fact.get("id", "")),
            str(fact.get("text", "")),
            float(fact.get("retrieval_score", 0.0)),
            fact.get("similarity"),
        )
        for fact in payload.get("facts", [])
    ]
    return facts, payload


def _matches(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _diversity(facts: list[SelectedFact]) -> float:
    if len(facts) < 2:
        return 1.0
    similarities = [
        text_overlap(left.text, right.text)
        for index, left in enumerate(facts)
        for right in facts[index + 1 :]
    ]
    return max(0.0, 1.0 - sum(similarities) / len(similarities))


def evaluate_scenario(scenario: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    mode = str(scenario.get("mode", "offline"))
    payload: dict[str, Any] = {}
    if mode == "replay":
        if db_path is None:
            raise ValueError("Replay evaluation requires --db")
        selected, payload = replay_retrieve(scenario, db_path)
        latency_ms = float(payload.get("timings_ms", {}).get("total", 0.0))
        response = str(payload.get("response_text", ""))
        tokens = int(payload.get("input_tokens", 0))
    elif mode == "offline":
        selected, latency_ms = offline_retrieve(scenario)
        response = str(scenario.get("response", ""))
        tokens = int(scenario.get("tokens", 0))
    else:
        raise ValueError(f"Unknown evaluation mode: {mode}")

    required = [str(value) for value in scenario.get("must_retrieve", [])]
    forbidden = [str(value) for value in scenario.get("must_not_retrieve", [])]
    required_hits = [pattern for pattern in required if any(_matches(fact.text, [pattern]) for fact in selected)]
    relevant = [fact for fact in selected if _matches(fact.text, required)] if required else []
    forbidden_hits = [fact for fact in selected if _matches(fact.text, forbidden)]
    recall = len(required_hits) / len(required) if required else 1.0
    precision = len(relevant) / len(selected) if selected else (1.0 if not required else 0.0)

    reciprocal_rank = 0.0
    for rank, fact in enumerate(selected, start=1):
        if _matches(fact.text, required):
            reciprocal_rank = 1.0 / rank
            break

    required_response = [str(value) for value in scenario.get("response_must_contain", [])]
    forbidden_response = [str(value) for value in scenario.get("response_must_not_contain", [])]
    response_recall = (
        sum(pattern.lower() in response.lower() for pattern in required_response) / len(required_response)
        if required_response else 1.0
    )
    response_violations = sum(pattern.lower() in response.lower() for pattern in forbidden_response)
    similarities = [fact.similarity for fact in selected if fact.similarity is not None]

    return {
        "id": scenario["id"],
        "suite": scenario["suite"],
        "mode": mode,
        "recall": recall,
        "precision": precision,
        "mrr": reciprocal_rank,
        "diversity": _diversity(selected),
        "forbidden_retrievals": len(forbidden_hits),
        "response_recall": response_recall,
        "response_violations": response_violations,
        "latency_ms": latency_ms,
        "tokens": tokens,
        "average_similarity": sum(similarities) / len(similarities) if similarities else 0.0,
        "average_score": sum(fact.score for fact in selected) / len(selected) if selected else 0.0,
        "selected_ids": [fact.fact_id for fact in selected],
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        raise ValueError("No evaluation scenarios found")
    count = len(results)
    average_keys = (
        "recall", "precision", "mrr", "diversity", "response_recall",
        "latency_ms", "tokens", "average_similarity", "average_score",
    )
    metrics = {key: sum(float(result[key]) for result in results) / count for key in average_keys}
    metrics["forbidden_retrievals"] = sum(int(result["forbidden_retrievals"]) for result in results)
    metrics["response_violations"] = sum(int(result["response_violations"]) for result in results)
    metrics["scenarios"] = count
    # Keep the names used in release reports explicit instead of making
    # operators translate generic metric names by hand.
    metrics["memory_recall"] = metrics["recall"]
    metrics["retrieval_precision"] = metrics["precision"]
    metrics["average_latency_ms"] = metrics["latency_ms"]
    metrics["average_tokens"] = metrics["tokens"]
    return metrics


def aggregate_by_suite(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the same quality metrics split by evaluation suite."""
    suites: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        suites.setdefault(str(result["suite"]), []).append(result)
    return {suite: aggregate(items) for suite, items in sorted(suites.items())}


def compare_baseline(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    if not baseline:
        return {"regressions": 0, "changes": {}}
    higher_is_better = ("recall", "precision", "mrr", "diversity", "response_recall")
    lower_is_better = ("forbidden_retrievals", "response_violations", "latency_ms", "tokens")
    changes: dict[str, float] = {}
    regressions = 0
    tolerance = 1e-9
    for key in higher_is_better:
        changes[key] = float(metrics[key]) - float(baseline.get(key, metrics[key]))
        regressions += changes[key] < -tolerance
    for key in lower_is_better:
        changes[key] = float(metrics[key]) - float(baseline.get(key, metrics[key]))
        regressions += changes[key] > tolerance
    return {"regressions": int(regressions), "changes": changes}


def run_evaluation(
    directory: Path,
    *,
    db_path: Path | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenarios = load_scenarios(directory)
    results = [evaluate_scenario(scenario, db_path=db_path) for scenario in scenarios]
    metrics = aggregate(results)
    return {
        "metrics": metrics,
        "suite_metrics": aggregate_by_suite(results),
        "comparison": compare_baseline(metrics, baseline),
        "suites": sorted({scenario["suite"] for scenario in scenarios}),
        "results": results,
    }
