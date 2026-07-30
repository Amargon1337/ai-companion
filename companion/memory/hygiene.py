"""Memory Hygiene Service (Stage 6) — Garbage Collector and Memory Audit service."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from companion.memory.activation import fact_activation_score
from companion.memory.governor import (
    ArchiveRecommendation,
    MemoryGovernor,
    MemoryRecommendation,
    MergeRecommendation,
)
from companion.memory.immunity import is_immune
from companion.memory.importance import days_since
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


@dataclass
class HygieneAuditReport:
    timestamp: str
    total_facts: int
    stale_candidates: list[str] = field(default_factory=list)
    low_activation_candidates: list[str] = field(default_factory=list)
    duplicate_candidates: list[tuple[str, str, float]] = field(default_factory=list)
    recommendations: list[MemoryRecommendation] = field(default_factory=list)


class DuplicateCandidateProvider:
    """Provides candidate pairs (fact1, fact2) to inspect for duplication."""

    def get_candidates(self, facts: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        raise NotImplementedError


class FaissDuplicateCandidateProvider(DuplicateCandidateProvider):
    """Candidate provider using a vector index (FAISS) or token-bucket indexing to avoid O(N^2) comparisons."""

    def __init__(self, vector_index: Any | None = None, top_k: int = 5) -> None:
        self.vector_index = vector_index
        self.top_k = top_k

    def get_candidates(self, facts: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        if len(facts) < 2:
            return []
        valid_facts = [f for f in facts if not is_immune(f) and f.get("id") and f.get("fact")]
        if not valid_facts:
            return []

        candidates = []
        seen_pairs = set()

        if self.vector_index and hasattr(self.vector_index, "search"):
            by_id = {str(f["id"]): f for f in valid_facts}
            by_content = {str(f["fact"]): f for f in valid_facts}
            for f in valid_facts:
                id1 = str(f["id"])
                try:
                    results = self.vector_index.search(f["fact"], top_k=self.top_k + 1)
                    for res in results:
                        content_str = str(res.get("content", "")) if isinstance(res, dict) else ""
                        matched_f = by_content.get(content_str)
                        if matched_f and str(matched_f["id"]) != id1:
                            id2 = str(matched_f["id"])
                            pair_key = tuple(sorted([id1, id2]))
                            if pair_key not in seen_pairs:
                                seen_pairs.add(pair_key)
                                candidates.append((by_id[id1], by_id[id2]))
                except Exception:
                    pass
            if candidates:
                return candidates

        # Token-bucket inverted indexing (O(N * k) where k is bucket size)
        token_buckets: dict[str, list[dict[str, Any]]] = {}
        for f in valid_facts:
            text = str(f.get("fact", "")).lower()
            tokens = {w for w in text.replace(".", " ").replace(",", " ").split() if len(w) >= 3}
            for tok in tokens:
                token_buckets.setdefault(tok, []).append(f)

        for bucket in token_buckets.values():
            if 1 < len(bucket) <= 20:
                for i in range(len(bucket)):
                    for j in range(i + 1, len(bucket)):
                        id1 = str(bucket[i]["id"])
                        id2 = str(bucket[j]["id"])
                        pair_key = tuple(sorted([id1, id2]))
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            candidates.append((bucket[i], bucket[j]))

        if not candidates and len(valid_facts) <= 100:
            for i in range(len(valid_facts)):
                for j in range(i + 1, len(valid_facts)):
                    id1 = str(valid_facts[i]["id"])
                    id2 = str(valid_facts[j]["id"])
                    pair_key = tuple(sorted([id1, id2]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        candidates.append((valid_facts[i], valid_facts[j]))

        return candidates


class MemoryHygieneService:
    """Audit service for finding stale facts, low activation facts, and duplicate candidates.

    Does not delete facts directly; generates an audit report with recommendations for the Governor.
    """

    def __init__(
        self,
        db: MemoryDatabase,
        governor: MemoryGovernor,
        *,
        stale_days: int = 90,
        low_activation_threshold: float = 0.20,
        similarity_threshold: float = 0.80,
        candidate_provider: DuplicateCandidateProvider | None = None,
        vector_index: Any | None = None,
    ) -> None:
        self.db = db
        self.governor = governor
        self.stale_days = stale_days
        self.low_activation_threshold = low_activation_threshold
        self.similarity_threshold = similarity_threshold
        self.candidate_provider = candidate_provider or FaissDuplicateCandidateProvider(
            vector_index=vector_index
        )

    def _text_similarity(self, text1: str, text2: str) -> float:
        w1 = set(text1.lower().split())
        w2 = set(text2.lower().split())
        if not w1 or not w2:
            return 0.0
        return len(w1 & w2) / len(w1 | w2)

    def audit(self, facts: list[dict[str, Any]] | None = None) -> HygieneAuditReport:
        """Run hygiene audit and return a structured report with candidate recommendations."""
        if facts is None:
            facts = self.db.list_facts(status="active")

        report = HygieneAuditReport(
            timestamp=datetime.now().isoformat(),
            total_facts=len(facts),
        )

        seen_recs: set[str] = set()

        # 1. Stale & Low activation candidates
        for fact in facts:
            if is_immune(fact):
                continue
            fact_id = str(fact.get("id", ""))
            if not fact_id:
                continue

            last_ts = fact.get("last_used_at") or fact.get("last_retrieved_at") or fact.get("date") or fact.get("created_at", "")
            age = days_since(str(last_ts))
            if age >= self.stale_days:
                report.stale_candidates.append(fact_id)
                if fact_id not in seen_recs:
                    report.recommendations.append(
                        ArchiveRecommendation(
                            fact_id=fact_id,
                            reason=f"stale_inactive_{int(age)}d",
                            source="gc",
                        )
                    )
                    seen_recs.add(fact_id)

            score = fact_activation_score(fact)
            imp = int(fact.get("importance", 5))
            if score <= self.low_activation_threshold and imp <= 3:
                report.low_activation_candidates.append(fact_id)
                if fact_id not in seen_recs:
                    report.recommendations.append(
                        ArchiveRecommendation(
                            fact_id=fact_id,
                            reason=f"low_activation_score:{score:.2f}",
                            source="gc",
                        )
                    )
                    seen_recs.add(fact_id)

        # 2. Duplicate detection using candidate provider
        candidate_pairs = self.candidate_provider.get_candidates(facts)
        for f1, f2 in candidate_pairs:
            id1 = str(f1.get("id", ""))
            t1 = str(f1.get("fact", ""))
            id2 = str(f2.get("id", ""))
            t2 = str(f2.get("fact", ""))

            sim = self._text_similarity(t1, t2)
            if sim >= self.similarity_threshold:
                # Keep newer fact, merge older fact into newer
                d1 = str(f1.get("date") or f1.get("created_at", ""))
                d2 = str(f2.get("date") or f2.get("created_at", ""))
                if d1 < d2:
                    older_id, newer_id = id1, id2
                else:
                    older_id, newer_id = id2, id1

                report.duplicate_candidates.append((older_id, newer_id, sim))
                if older_id not in seen_recs:
                    report.recommendations.append(
                        MergeRecommendation(
                            fact_id=older_id,
                            target_fact_id=newer_id,
                            reason=f"duplicate_sim:{sim:.2f}",
                            source="gc",
                        )
                    )
                    seen_recs.add(older_id)

        logger.info(
            "Hygiene audit finished: %d stale, %d low activation, %d duplicates, %d recommendations",
            len(report.stale_candidates),
            len(report.low_activation_candidates),
            len(report.duplicate_candidates),
            len(report.recommendations),
        )
        return report

    def apply_recommendations(self, report: HygieneAuditReport) -> dict[str, int]:
        """Submit all audit recommendations to the Memory Governor."""
        return self.governor.process_recommendations(report.recommendations)
