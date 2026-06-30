"""Unified memory store — facts, messages, relations, reflections, beliefs."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime
from typing import Any

from companion.config import (
    EMPTY_PERSONALITY,
    PERSONALITY_PATH,
)
from companion.memory.importance import days_since
from companion.memory.text_sim import text_overlap
from companion.memory.vector_index import VectorIndex
from companion.models import Fact, FactRelation, MessageRecord, Reflection
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self) -> None:
        self.db = MemoryDatabase()
        self.vector = VectorIndex()
        self._personality_cache: dict[str, Any] | None = None

    # ── Meta ──────────────────────────────────────────────────────────

    def get_compress_count(self) -> int:
        return int(self.db.get_meta("compress_count", "0"))

    def increment_compress_count(self) -> int:
        n = self.get_compress_count() + 1
        self.db.set_meta("compress_count", str(n))
        return n

    # ── Messages ──────────────────────────────────────────────────────

    def log_message(
        self,
        role: str,
        text: str,
        importance: int,
        mode: str = "default",
        signals: list[str] | None = None,
        user_id: int | None = None,
    ) -> MessageRecord:
        msg = MessageRecord(
            role=role,
            text=text,
            importance=importance,
            mode=mode,
            signals=signals or [],
            user_id=user_id,
        )
        d = msg.to_dict()
        self.db._insert_message(d)
        return msg

    def recent_messages(
        self, min_importance: int = 0, limit: int = 50
    ) -> list[MessageRecord]:
        rows = self.db.list_messages(min_importance=min_importance, limit=limit)
        return [MessageRecord.from_dict(r) for r in rows]

    # ── Facts ─────────────────────────────────────────────────────────

    def add_fact(self, fact: Fact) -> Fact:
        d = fact.to_dict()
        self.db._insert_fact(d)
        self.vector.compute_and_cache(fact.fact, content_type="fact")
        return fact

    def list_facts(self, status: str = "active") -> list[Fact]:
        rows = self.db.list_facts(status=status)
        return [Fact.from_dict(r) for r in rows]

    def list_all_facts(self) -> list[Fact]:
        rows = self.db.list_all_facts()
        return [Fact.from_dict(r) for r in rows]

    def search_facts(self, query: str, limit: int = 20) -> list[Fact]:
        try:
            results = self.vector.search(query, top_k=limit, content_type="fact")
            if results:
                ql = query.lower()
                seen = set()
                hits: list[Fact] = []
                for r in results:
                    if len(hits) >= limit:
                        break
                    for f in self.list_facts("active"):
                        if f.id in seen:
                            continue
                        if f.fact == r["content"]:
                            seen.add(f.id)
                            hits.append(f)
                            break
                if hits:
                    return hits
        except Exception as exc:
            logger.debug("Vector search unavailable, falling back to keyword: %s", exc)

        q = query.lower()
        hits = [
            f for f in self.list_facts("active")
            if q in f.fact.lower()
            or any(q in t.lower() for t in f.tags)
        ]
        if not hits:
            hits = [
                f for f in self.list_facts("active")
                if any(w in f.fact.lower() for w in q.split() if len(w) > 3)
            ]
        return hits[:limit]

    def add_relation(self, rel: FactRelation) -> None:
        d = rel.to_dict()
        self.db._insert_relation(d)
        if rel.relation == "supersedes":
            self.db.update_fact_status(rel.to_id, "superseded")

    def get_active_fact_texts(self) -> list[str]:
        return [f.fact for f in self.list_facts("active")]

    def find_similar_fact(self, text: str, threshold: float = 0.52) -> Fact | None:
        """Dedup via char n-grams — устойчиво к русским окончаниям."""
        norm = self._normalize(text)
        best: Fact | None = None
        best_score = 0.0
        for f in self.list_facts("active"):
            score = text_overlap(norm, self._normalize(f.fact))
            if score > best_score:
                best_score = score
                best = f
        return best if best_score >= threshold else None

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().strip())

    # ── Reflections ─────────────────────────────────────────────────

    def add_reflection(self, reflection: Reflection) -> Reflection:
        d = reflection.to_dict()
        self.db._insert_reflection(d)
        return reflection

    def list_reflections(self, status: str = "active") -> list[Reflection]:
        rows = self.db.list_reflections(status=status)
        return [Reflection.from_dict(r) for r in rows]

    # ── Beliefs ───────────────────────────────────────────────────────

    def add_belief(self, belief: str, based_on: list[str], importance: int = 6) -> None:
        d = {
            "id": f"belief_{uuid.uuid4().hex[:10]}",
            "belief": belief,
            "based_on": based_on,
            "importance": importance,
            "status": "active",
            "created_at": datetime.now().isoformat(),
        }
        self.db._insert_belief(d)
        self.vector.compute_and_cache(belief, content_type="belief")

    def list_beliefs(self) -> list[dict[str, Any]]:
        return self.db.list_beliefs()

    # ── Personality snapshot ─────────────────────────────────────────

    def load_personality(self) -> dict[str, Any]:
        if self._personality_cache is not None:
            return self._personality_cache
        try:
            with open(PERSONALITY_PATH, encoding="utf-8") as f:
                self._personality_cache = json.load(f)
                return self._personality_cache
        except (FileNotFoundError, json.JSONDecodeError):
            return dict(EMPTY_PERSONALITY)

    def save_personality(self, data: dict[str, Any]) -> None:
        self._personality_cache = data
        parent = os.path.dirname(PERSONALITY_PATH) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, PERSONALITY_PATH)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def build_personality_snapshot_text(self) -> str:
        """Compressed personality for retrieval context (not full JSON dump)."""
        p = self.load_personality()
        lines = ["[Снимок личности]"]
        for key in ("values", "fears", "strengths", "weaknesses"):
            items = p.get(key, [])
            if items:
                lines.append(f"{key}: " + "; ".join(str(x) for x in items[:8]))
        interests = p.get("interests", {})
        if interests:
            top = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:10]
            lines.append("interests: " + ", ".join(f"{k}({v})" for k, v in top))
        beliefs = p.get("beliefs", [])
        if beliefs:
            lines.append("beliefs: " + "; ".join(str(b) for b in beliefs[:10]))
        changes = p.get("changes", [])
        if changes:
            lines.append("recent_changes: " + "; ".join(str(c) for c in changes[-5:]))
        return "\n".join(lines)

    # ── Monthbook data ────────────────────────────────────────────────

    def facts_for_period(self, ym: str, min_importance: int = 5) -> list[Fact]:
        return [
            f for f in self.list_all_facts()
            if (f.date or "")[:7] == ym
            and f.importance >= min_importance
            and f.status in ("active", "superseded", "archived")
        ]

    def high_importance_messages_for_period(
        self, ym: str, min_importance: int = 7
    ) -> list[MessageRecord]:
        return [
            m for m in self.recent_messages(min_importance=min_importance, limit=500)
            if m.ts[:7] == ym
        ]

    def apply_importance_decay(self) -> int:
        """Lower effective tier for old low-importance facts — never delete."""
        updated = 0
        for f in self.list_facts("active"):
            if f.importance >= 8 or f.memory_kind == "permanent":
                continue
            age = days_since(f.date or f.created_at)
            if age > 180 and f.importance <= 4 and f.status == "active":
                self.db.update_fact_status(f.id, "archived")
                updated += 1
            elif age > 90 and f.importance <= 3 and f.status == "active":
                self.db.update_fact_status(f.id, "inactive")
                updated += 1
        return updated

    def reindex_all(self) -> dict[str, int]:
        """Reindex all facts, beliefs, reflections, and causal links into vector index."""
        counts: dict[str, int] = {"facts": 0, "beliefs": 0, "reflections": 0, "causal_links": 0}

        fact_texts = [f.fact for f in self.list_facts("active") if f.fact.strip()]
        self.vector.compute_and_cache_batch(fact_texts, content_type="fact")
        counts["facts"] = len(fact_texts)

        beliefs = self.list_beliefs()
        belief_texts = [b["belief"] for b in beliefs if b.get("belief", "").strip()]
        self.vector.compute_and_cache_batch(belief_texts, content_type="belief")
        counts["beliefs"] = len(belief_texts)

        reflections = self.list_reflections()
        refl_texts = [r.insight for r in reflections if r.insight.strip()]
        self.vector.compute_and_cache_batch(refl_texts, content_type="reflection")
        counts["reflections"] = len(refl_texts)

        from companion.reasoning import reasoning_engine
        try:
            causal = reasoning_engine.get_relevant_causal_context("")
            if isinstance(causal, list):
                causal_texts = [c if isinstance(c, str) else str(c) for c in causal if c]
                self.vector.compute_and_cache_batch(causal_texts, content_type="causal_link")
                counts["causal_links"] = len(causal_texts)
        except Exception as exc:
            logger.debug("Causal link indexing skipped: %s", exc)

        logger.info("Reindexed %d facts, %d beliefs, %d reflections, %d causal links",
                     counts["facts"], counts["beliefs"], counts["beliefs"], counts["causal_links"])
        return counts

    def stats(self) -> dict[str, int]:
        return {
            "facts_active": self.db.count_facts("active"),
            "facts_total": self.db.count_facts(None),
            "messages": self.db.count_messages(),
            "reflections": len(self.db.list_reflections()),
            "beliefs": len(self.db.list_beliefs()),
            "compress_count": self.get_compress_count(),
        }
