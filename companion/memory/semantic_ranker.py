"""Semantic importance reranking for FAISS candidates."""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Iterable

from companion import config
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class SemanticImportanceRanker:
    def __init__(
        self,
        db: MemoryDatabase,
        *,
        recency_half_life_days: float = 180.0,
        recency_floor: float = 0.70,
        access_bonus_max: float = 0.20,
        access_saturation: int = 50,
        anchor_boost_value: float = 1.15,
        max_final_score: float = 1.5,
    ) -> None:
        self.db = db
        self.recency_half_life_days = recency_half_life_days
        self.recency_floor = recency_floor
        self.access_bonus_max = access_bonus_max
        self.access_saturation = access_saturation
        self.anchor_boost_value = anchor_boost_value
        self.max_final_score = max_final_score

    def rerank(self, candidates: list[tuple[Fact, float]], *, query_text: str = "", update_access: bool = False) -> list[tuple[Fact, float]]:
        if not candidates or not config.ENABLE_IMPORTANCE_RANKING:
            return candidates
        try:
            metadata = self.db.hydrate_fact_metadata([fact.id for fact, _ in candidates])
            ranked: list[tuple[Fact, float, float]] = []
            for fact, raw_score in candidates:
                meta = metadata.get(fact.id)
                if meta and int(meta.get("archived") or 0):
                    continue
                vector_score = self._normalize_vector_score(raw_score)
                final_score = self.final_score(vector_score, fact, meta)
                ranked.append((fact, final_score, vector_score))
            ranked.sort(key=lambda item: item[1], reverse=True)
            if update_access and config.ENABLE_ACCESS_TRACKING:
                self.update_access(((fact.id, vector_score, final_score) for fact, final_score, vector_score in ranked), query_text)
            return [(fact, score) for fact, score, _ in ranked]
        except Exception as exc:
            logger.warning("Importance reranking failed, using vector order: %s", exc)
            return candidates

    def final_score(self, vector_score: float, fact: Fact, metadata: dict | None = None) -> float:
        importance = int((metadata or {}).get("importance", fact.importance) or fact.importance)
        anchor = bool((metadata or {}).get("anchor_flag") or self._is_anchor(fact))
        manual_lock = bool((metadata or {}).get("manual_lock") or 0)
        decay_exempt = bool((metadata or {}).get("decay_exempt") or 0)
        access_count = int((metadata or {}).get("access_count") or 0)
        created_at = str((metadata or {}).get("created_at") or fact.created_at or fact.date)
        score = (
            vector_score
            * self._importance_weight(importance)
            * self._recency_factor(created_at, anchor=anchor, manual_lock=manual_lock, decay_exempt=decay_exempt)
            * self._access_factor(access_count)
            * (self.anchor_boost_value if anchor else 1.0)
        )
        return min(score, self.max_final_score)

    def update_access(self, scores: Iterable[tuple[str, float, float]], query_text: str) -> None:
        try:
            unique = list(dict.fromkeys(scores))
            query_hash = hashlib.sha256(query_text.encode("utf-8")).hexdigest() if query_text else None
            self.db.record_fact_access_batch(unique, query_hash=query_hash)
        except Exception as exc:
            logger.warning("Access tracking failed: %s", exc)

    def _importance_weight(self, importance_score: int) -> float:
        bounded = max(1, min(10, importance_score))
        return 0.75 + 0.05 * bounded

    def _recency_factor(self, created_at: str, *, anchor: bool, manual_lock: bool, decay_exempt: bool) -> float:
        if anchor:
            return 1.0
        if manual_lock or decay_exempt:
            return 0.95
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 86400.0)
        except (ValueError, AttributeError):
            age_days = 0.0
        decayed = math.exp(-age_days / self.recency_half_life_days)
        return self.recency_floor + (1.0 - self.recency_floor) * decayed

    def _access_factor(self, access_count: int) -> float:
        bounded = max(0, min(access_count, self.access_saturation))
        return 1.0 + self.access_bonus_max * (math.log1p(bounded) / math.log1p(self.access_saturation))

    def _normalize_vector_score(self, raw_score: float) -> float:
        return max(0.0, min(1.0, raw_score))

    def _is_anchor(self, fact: Fact) -> bool:
        tags = {t.lower() for t in fact.tags}
        return fact.memory_kind == "permanent" or bool(tags & {"anchor", "core_identity", "pinned"})
