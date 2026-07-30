"""Runtime request traces and memory diagnostics for operator commands."""
from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from companion.llm.token_budget import estimate_tokens
from companion.memory.importance import days_since, decay_factor


@dataclass
class RequestTrace:
    user_id: int
    query: str
    started_at: float = field(default_factory=time.perf_counter)
    timings_ms: dict[str, float] = field(default_factory=dict)
    history_tokens: int = 0
    context_tokens: int = 0
    input_tokens: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    facts: list[dict[str, Any]] = field(default_factory=list)
    replay_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    response_text: str = ""

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000


_active: dict[int, RequestTrace] = {}
_latest: dict[int, RequestTrace] = {}
_recent: deque[RequestTrace] = deque(maxlen=100)


def begin_trace(user_id: int, query: str) -> RequestTrace:
    trace = RequestTrace(user_id=user_id, query=query)
    _active[user_id] = trace
    return trace


def active_trace(user_id: int) -> RequestTrace | None:
    return _active.get(user_id)


def finish_trace(user_id: int) -> RequestTrace | None:
    trace = _active.pop(user_id, None)
    if trace is None:
        return None
    trace.timings_ms["total"] = trace.elapsed_ms()
    _latest[user_id] = trace
    _recent.append(trace)
    return trace


def save_replay(trace: RequestTrace, store: Any) -> None:
    payload = {
        "replay_id": trace.replay_id,
        "user_id": trace.user_id,
        "query": trace.query,
        "timings_ms": trace.timings_ms,
        "history_tokens": trace.history_tokens,
        "context_tokens": trace.context_tokens,
        "input_tokens": trace.input_tokens,
        "counts": trace.counts,
        "facts": trace.facts,
        "response_text": trace.response_text,
    }
    store.db.save_retrieval_replay(
        trace.replay_id,
        trace.user_id,
        json.dumps(payload, ensure_ascii=False),
    )


def load_replay(store: Any, replay_id: str) -> dict[str, Any] | None:
    raw = store.db.get_retrieval_replay(replay_id)
    if not raw:
        return None
    try:
        return json.loads(raw["payload"])
    except (TypeError, json.JSONDecodeError):
        return None


def latest_trace(user_id: int) -> RequestTrace | None:
    return _latest.get(user_id)


def average_timings() -> dict[str, float]:
    if not _recent:
        return {}
    keys = {key for trace in _recent for key in trace.timings_ms}
    result: dict[str, float] = {}
    for key in keys:
        samples = [trace.timings_ms[key] for trace in _recent if key in trace.timings_ms]
        result[key] = sum(samples) / len(samples)
    return result


def capture_bundle(trace: RequestTrace, bundle: Any, faiss_scores: dict[str, float], store: Any) -> None:
    prompt = bundle.to_prompt_block()
    trace.context_tokens = estimate_tokens(prompt)
    trace.counts = {
        "facts": len(bundle.facts),
        "beliefs": len(store.list_beliefs()),
        "patterns": len(bundle.patterns),
        "predictions": len(bundle.predictions),
        "reflections": len(bundle.reflections),
        "graph_edges": sum(len(store.get_fact_relations(f.id)) for f in bundle.facts),
        "summaries": len(bundle.summaries),
    }
    trace.facts = []
    for fact in bundle.facts:
        age = days_since(fact.date or fact.created_at)
        trace.facts.append({
            "id": fact.id,
            "text": fact.fact,
            "similarity": faiss_scores.get(fact.id),
            "importance": fact.importance / 10.0,
            "recency": decay_factor(age, fact.memory_kind),
            "retrieval_score": float(getattr(fact, "retrieval_score", 0.0)),
            "relations": len(store.get_fact_relations(fact.id)),
            "sent_count": fact.facts_sent_count,
            "used_count": fact.facts_used_count,
        })


def memory_stats(store: Any) -> dict[str, Any]:
    with store.db._conn() as conn:
        counts = {
            "facts": conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
            "active_facts": conn.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0],
            "beliefs": conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0],
            "patterns": conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0],
            "predictions": conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
            "graph_edges": conn.execute("SELECT COUNT(*) FROM fact_relations").fetchone()[0],
            "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        }
        usage = conn.execute(
            "SELECT COALESCE(SUM(facts_sent_count),0), COALESCE(SUM(facts_used_count),0) FROM facts"
        ).fetchone()
        dirty_row = conn.execute("SELECT value FROM meta WHERE key='faiss_index_dirty'").fetchone()
    counts["fact_sent"] = int(usage[0])
    counts["fact_used"] = int(usage[1])
    counts["faiss_dirty"] = bool(dirty_row and dirty_row[0] == "1")
    counts["faiss_vectors"] = int(getattr(store.vector.index, "ntotal", 0))
    return counts
