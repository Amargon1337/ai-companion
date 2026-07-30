"""Replay learning, golden export, benchmarking, and retrieval weight tuning."""
from __future__ import annotations

import itertools
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from companion.llm.client import aio_oneshot, parse_json_object
from companion.memory.text_sim import text_overlap

DEFAULT_WEIGHTS = {"semantic": 0.50, "importance": 0.30, "recency": 0.20}
logger = logging.getLogger(__name__)


def score_fact(fact: dict[str, Any], weights: dict[str, float]) -> float:
    similarity = float(fact.get("similarity") or 0.0)
    importance = float(fact.get("importance", 0.5))
    recency = float(fact.get("recency", 0.5))
    return (
        weights["semantic"] * similarity
        + weights["importance"] * importance
        + weights["recency"] * recency
    )


def rerank(facts: list[dict[str, Any]], weights: dict[str, float], top_k: int) -> list[dict[str, Any]]:
    return sorted(facts, key=lambda fact: score_fact(fact, weights), reverse=True)[:top_k]


def _quality(selected: list[dict[str, Any]], required_ids: set[str]) -> tuple[float, float]:
    selected_ids = {str(fact.get("id", "")) for fact in selected}
    hits = len(selected_ids & required_ids)
    recall = hits / len(required_ids) if required_ids else 1.0
    precision = hits / len(selected_ids) if selected_ids else (1.0 if not required_ids else 0.0)
    return recall, precision


def benchmark(cases: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, float]:
    scores = []
    for case in cases:
        required = {str(value) for value in case.get("must_retrieve_ids", [])}
        selected = rerank(case.get("candidates", []), weights, max(1, int(case.get("top_k", 5))))
        scores.append(_quality(selected, required))
    count = len(scores)
    if not count:
        raise ValueError("No labelled golden cases")
    recall = sum(item[0] for item in scores) / count
    precision = sum(item[1] for item in scores) / count
    return {"recall": recall, "precision": precision, "score": (recall + precision) / 2, "cases": count}


def tune_weights(cases: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = benchmark(cases, DEFAULT_WEIGHTS)
    best_weights = dict(DEFAULT_WEIGHTS)
    best_metrics = baseline
    values = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
    for semantic, importance in itertools.product(values, repeat=2):
        recency = round(1.0 - semantic - importance, 2)
        if recency < 0.0 or recency > 0.8:
            continue
        weights = {"semantic": semantic, "importance": importance, "recency": recency}
        metrics = benchmark(cases, weights)
        if metrics["score"] > best_metrics["score"]:
            best_weights, best_metrics = weights, metrics
    return {
        "baseline_weights": DEFAULT_WEIGHTS,
        "baseline": baseline,
        "best_weights": best_weights,
        "best": best_metrics,
        "improved": best_metrics["score"] > baseline["score"],
    }


def load_golden(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Golden dataset must be a JSON array")
    return [item for item in data if item.get("must_retrieve_ids")]


def export_golden(db_path: Path, output: Path, limit: int = 300) -> int:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT replay_id, payload FROM retrieval_replays ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    cases = []
    for replay_id, raw in reversed(rows):
        payload = json.loads(raw)
        candidates = []
        seen_ids = set()
        for fact in payload.get("facts", []) + payload.get("learning_candidates", []):
            fact_id = str(fact.get("id", ""))
            if fact_id and fact_id not in seen_ids:
                candidates.append(fact)
                seen_ids.add(fact_id)
        cases.append({
            "id": f"golden-{replay_id}",
            "replay_id": replay_id,
            "query": payload.get("query", ""),
            "response": payload.get("response_text", ""),
            "candidates": candidates,
            "must_retrieve_ids": [],
            "must_not_retrieve_ids": [],
            "good_response_notes": "",
            "top_k": len(payload.get("facts", [])) or 5,
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(cases)


def satisfaction_signal(next_query: str, replay: dict[str, Any]) -> dict[str, Any]:
    lowered = next_query.lower().strip()
    negative = any(word in lowered for word in ("не так", "неправ", "забыл", "нет,", "ошибка"))
    positive = any(word in lowered for word in ("cпаcибо", "cпаcибо", "точно", "да,", "верно", "понял"))
    overlap = text_overlap(next_query, str(replay.get("query", "")))
    continued = overlap >= 0.12 or "?" in next_query
    label = "no" if negative else "yes" if positive or continued else "uncertain"
    return {"label": label, "continued_topic": continued, "query_overlap": overlap, "next_query": next_query[:500]}


def annotate_previous_satisfaction(store: Any, user_id: int, next_query: str) -> bool:
    rows = store.db.list_retrieval_replays(user_id=user_id, limit=1)
    if not rows:
        return False
    row = rows[0]
    payload = json.loads(row["payload"])
    if payload.get("satisfaction"):
        return False
    payload["satisfaction"] = satisfaction_signal(next_query, payload)
    store.db.update_retrieval_replay_payload(row["replay_id"], json.dumps(payload, ensure_ascii=False))
    return True


async def learn_replays(store: Any, limit: int = 10) -> int:
    """Ask the configured LLM to critique unreviewed replays and persist JSON annotations."""
    learned = 0
    for row in reversed(store.db.list_retrieval_replays(limit=limit * 3)):
        payload = json.loads(row["payload"])
        if payload.get("learning") or not payload.get("response_text"):
            continue
        candidates = store.search_facts(str(payload.get("query", "")), limit=20)
        candidate_data = [{"id": fact.id, "text": fact.fact, "score": score} for fact, score in candidates]
        prompt = (
            "Оцени retrieval replay. Верни только JSON: "
            '{"better_memory_possible":bool,"promote_fact_ids":[],"irrelevant_fact_ids":[],"notes":""}.\n'
            f"Query: {payload.get('query', '')}\nSelected: {json.dumps(payload.get('facts', []), ensure_ascii=False)}\n"
            f"Other candidates: {json.dumps(candidate_data, ensure_ascii=False)}\n"
            f"Response: {payload.get('response_text', '')}"
        )
        try:
            annotation = parse_json_object(await aio_oneshot(prompt))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            annotation = {"error": str(exc)}
        except Exception as exc:
            logger.exception("Replay learning failed for %s: %s", row["replay_id"], exc)
            break
        payload["learning"] = annotation
        payload["learning_candidates"] = candidate_data
        store.db.update_retrieval_replay_payload(row["replay_id"], json.dumps(payload, ensure_ascii=False))
        learned += 1
        if learned >= limit:
            break
    return learned
