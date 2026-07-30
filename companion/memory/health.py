"""Observability and Memory Health Metrics (Stage 7)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from companion.memory.importance import days_since
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


@dataclass
class GCCandidate:
    fact_id: str
    age_days: int
    importance: int
    text: str
    reason: str = ""


def collect_garbage(store: Any, apply: bool = False) -> list[GCCandidate]:
    """Audit active facts using hygiene service and return GC candidates."""
    report = store.hygiene_service.audit()
    candidates: list[GCCandidate] = []
    for rec in report.recommendations:
        if getattr(rec, "action", "") == "archive":
            fact_id = getattr(rec, "fact_id", "")
            fact = store.db.get_fact(fact_id)
            if fact:
                last_ts = (
                    fact.get("last_used_at")
                    or fact.get("last_retrieved_at")
                    or fact.get("date")
                    or fact.get("created_at", "")
                )
                age = 0
                try:
                    age = int(days_since(str(last_ts)))
                except Exception:
                    pass
                candidates.append(
                    GCCandidate(
                        fact_id=fact_id,
                        age_days=age,
                        importance=int(fact.get("importance", 5)),
                        text=str(fact.get("fact", "")),
                        reason=getattr(rec, "reason", ""),
                    )
                )
    if apply and candidates:
        store.persistence.process_recommendations(report.recommendations, initiator="gc")
    return candidates


def memory_health(store: Any) -> dict[str, Any]:
    """Compute back-compat memory health dict for bot commands and UI."""
    db = getattr(store, "db", None)
    if db is None:
        return {}
    monitor = MemoryHealthMonitor(db)
    metrics = monitor.get_health_metrics()

    all_facts = db.list_facts(status=None)
    dormant_facts = sum(1 for f in all_facts if str(f.get("status", "")).lower() == "dormant")
    superseded_facts = sum(1 for f in all_facts if str(f.get("status", "")).lower() == "superseded")

    report = store.hygiene_service.audit()
    duplicate_candidates = len(report.duplicate_candidates)
    gc_candidates = len(report.stale_candidates) + len(report.low_activation_candidates)

    quality_score = max(0, 100 - min(100, gc_candidates * 5 + duplicate_candidates * 10))

    metrics.update(
        {
            "facts": metrics["total_facts"],
            "duplicate_candidates": duplicate_candidates,
            "duplicate_groups": duplicate_candidates,
            "contradictions": 0,
            "orphan_active_facts": 0,
            "unused_embeddings": 0,
            "stale_predictions": 0,
            "gc_candidates": gc_candidates,
            "dormant_facts": dormant_facts,
            "superseded_facts": superseded_facts,
            "archived_facts": metrics["archived_facts"],
            "quality_score": quality_score,
        }
    )
    return metrics


def memory_index_health(store: Any) -> dict[str, int]:
    """Compute authoritative index health stats (active facts, indexed, orphans, missing)."""
    db = getattr(store, "db", None)
    vector = getattr(store, "vector", None)
    if db is None or vector is None:
        return {"active_facts": 0, "indexed_vectors": 0, "orphan_vectors": 0, "missing_vectors": 0}

    active_facts = db.list_facts(status="active")
    dormant_facts = db.list_facts(status="dormant")
    valid_hashes: set[str] = set()
    active_hashes: set[str] = set()

    for f in active_facts:
        txt = str(f.get("fact", "")).strip()
        if txt and hasattr(vector, "_content_hash"):
            h = vector._content_hash(txt)
            valid_hashes.add(h)
            active_hashes.add(h)
    for f in dormant_facts:
        txt = str(f.get("fact", "")).strip()
        if txt and hasattr(vector, "_content_hash"):
            valid_hashes.add(vector._content_hash(txt))

    indexed_hashes: set[str] = set()
    try:
        with vector._conn() as conn:
            rows = conn.execute(
                "SELECT content_hash FROM embeddings WHERE content_type='fact'"
            ).fetchall()
        indexed_hashes = {str(row["content_hash"]) for row in rows}
    except Exception as exc:
        logger.error("Failed to query indexed vector hashes: %s", exc)

    orphan_vectors = len(indexed_hashes - valid_hashes)
    missing_vectors = len(active_hashes - indexed_hashes)

    return {
        "active_facts": len(active_facts),
        "indexed_vectors": len(indexed_hashes),
        "orphan_vectors": orphan_vectors,
        "missing_vectors": missing_vectors,
    }



class MemoryHealthMonitor:
    """Monitors memory hygiene, retrieval precision, and lifecycle health metrics."""

    def __init__(self, db: MemoryDatabase, stale_days: int = 90) -> None:
        self.db = db
        self.stale_days = stale_days

    def get_health_metrics(self) -> dict[str, Any]:
        """Compute authoritative backend health metrics for the memory system."""
        all_facts = self.db.list_facts(status=None)
        total_facts = len(all_facts)

        active_facts = [f for f in all_facts if str(f.get("status", "")).lower() == "active"]
        archived_facts = [f for f in all_facts if str(f.get("status", "")).lower() == "archived"]

        # 1. Retrieval precision: total used / total retrieved for active facts
        total_retrieved = sum(
            int(f.get("facts_sent_count", f.get("retrieved_count", 0))) for f in active_facts
        )
        total_used = sum(
            int(f.get("facts_used_count", f.get("used_count", 0))) for f in active_facts
        )
        retrieval_precision = float(total_used) / float(total_retrieved) if total_retrieved > 0 else 1.0

        # 2. Citation rate: proportion of active facts that have been cited at least once
        cited_facts = sum(
            1 for f in active_facts if int(f.get("facts_used_count", f.get("used_count", 0))) > 0
        )
        citation_rate = float(cited_facts) / float(len(active_facts)) if active_facts else 0.0

        # 3. Archive rate: proportion of total facts that are archived
        archive_rate = float(len(archived_facts)) / float(total_facts) if total_facts > 0 else 0.0

        # 4. Memory growth velocity: average facts created per day over the age of oldest fact
        oldest_days = 1.0
        for f in all_facts:
            dt = str(f.get("date") or f.get("created_at") or "")
            if dt:
                try:
                    d = days_since(dt)
                    if d > oldest_days:
                        oldest_days = d
                except Exception:
                    pass
        memory_growth_velocity = float(total_facts) / oldest_days if total_facts > 0 else 0.0

        # 5. Stale memory ratio: proportion of active facts older than stale_days
        stale_count = 0
        for f in active_facts:
            dt = str(
                f.get("last_used_at")
                or f.get("last_retrieved_at")
                or f.get("date")
                or f.get("created_at")
                or ""
            )
            if dt:
                try:
                    if days_since(dt) >= self.stale_days:
                        stale_count += 1
                except Exception:
                    pass
        stale_memory_ratio = float(stale_count) / float(len(active_facts)) if active_facts else 0.0

        # 6 & 7. Mutation log metrics
        mutations = self.db.list_mutations(limit=10000)
        mutation_count = len(mutations)
        rollback_count = sum(
            1 for m in mutations if str(m.get("action", "")).lower() in ("rollback", "restore")
        )

        metrics = {
            "retrieval_precision": round(retrieval_precision, 4),
            "citation_rate": round(citation_rate, 4),
            "archive_rate": round(archive_rate, 4),
            "memory_growth_velocity": round(memory_growth_velocity, 4),
            "stale_memory_ratio": round(stale_memory_ratio, 4),
            "mutation_count": mutation_count,
            "rollback_count": rollback_count,
            "total_facts": total_facts,
            "active_facts": len(active_facts),
            "archived_facts": len(archived_facts),
        }
        logger.debug("Computed memory health metrics: %s", metrics)
        return metrics
