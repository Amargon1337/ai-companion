"""Unified memory store — facts, messages, relations, reflections, beliefs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from companion.models import CommPref, HumanModel, LifeTransition

from companion.config import (
    DATA_DIR,
)
from companion.memory.importance import days_since
from companion.memory.semantic_ranker import SemanticImportanceRanker

from companion.memory.vector_index import VectorIndex
from companion.memory.identity_vault import IdentityVault
from companion.models import Fact, FactRelation, MessageRecord, Reflection, Pattern
from companion.memory.events import IndexSyncService, MemoryEventBus
from companion.memory.governor import MemoryGovernor
from companion.memory.persistence import MemoryPersistenceLayer
from companion.memory.feedback import MemoryFeedbackLoop
from companion.memory.hygiene import MemoryHygieneService
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self) -> None:
        self.db = MemoryDatabase()
        self.vector = VectorIndex(db=self.db)
        self.event_bus = MemoryEventBus()
        self.index_sync = IndexSyncService(self.event_bus, self.vector, self.db)
        self.semantic_ranker = SemanticImportanceRanker(self.db)
        self.identity = IdentityVault(self.db.path)
        self.governor = MemoryGovernor(self.db)
        self.persistence = MemoryPersistenceLayer(self.db, self.governor, event_bus=self.event_bus)
        self.feedback_loop = MemoryFeedbackLoop(self.db, self.governor)
        self.hygiene_service = MemoryHygieneService(self.db, self.governor, vector_index=self.vector)
        from companion.memory.world_model import WorldModelService
        self.world_model = WorldModelService(self.db, vector=self.vector)
        from companion.memory.cognitive_loop import CognitiveLoopService
        self._cognitive = CognitiveLoopService(self.db, store=self, world_model=self.world_model, vector=self.vector)
        from companion.reasoning_engine import ReasoningEngineService
        self._reasoning_engine = ReasoningEngineService(db=self.db, store=self)
        from companion.learning_engine import LearningEngineService
        self._learning_engine = LearningEngineService(db=self.db, store=self)
        import threading
        self._cache_lock = threading.Lock()

    @property
    def world(self) -> Any:
        return self.world_model

    @property
    def cognitive(self) -> Any:
        return self._cognitive

    @property
    def reasoner(self) -> Any:
        return self._reasoning_engine

    @property
    def learning(self) -> Any:
        return self._learning_engine

    @property
    def lock(self) -> asyncio.Lock:
        """Lock for critical sections reading and mutating state.
        
        Contract:
        - Ownership: The lock protects read-modify-write operations on shared files 
          (e.g., personality.json) and multi-step dependent DB transactions.
        - Acquisition: The CALLER (e.g., pipeline, background task) MUST acquire this 
          lock BEFORE invoking methods that perform read-modify-write cycles. 
          MemoryStore does NOT acquire this lock internally to avoid deadlocks.
        - Blocking I/O: Do NOT hold this lock during long blocking I/O (like LLM calls). 
          Synchronous file I/O inside the lock should be delegated to threads.
        """
        import asyncio
        if not hasattr(self, "_lock"):
            self._lock = asyncio.Lock()
        return self._lock

    def _assert_locked(self) -> None:
        """Debug assertion to ensure the lock was acquired by the caller."""
        if hasattr(self, "_lock") and not self._lock.locked():
            logger.warning("DEBUG ASSERTION FAILED: store.lock is not held during critical mutation!")

    # ── Personality ───────────────────────────────────────────────────

    def load_personality(self) -> dict[str, Any]:
        """Load personality from SQLite DB (meta table)."""
        from companion.config import EMPTY_PERSONALITY
        val = self.db.get_meta("personality", "")
        if val:
            try:
                return json.loads(val)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse personality from DB: %s", e)
        return dict(EMPTY_PERSONALITY)

    def save_personality(self, data: dict[str, Any]) -> None:
        """Save personality to SQLite DB."""
        self.db.set_meta("personality", json.dumps(data, ensure_ascii=False))

    def build_canonical_profile_text(self) -> str:
        """Build one canonical user profile for prompts.

        IdentityVault is the identity source of truth. Personality and UserModel
        add non-identity enrichment so prompt sections do not fight each other.
        """
        parts: list[str] = []
        from companion.memory.consolidation import SNAPSHOT_MODEL, snapshot_text
        snapshot = self.db.get_state_model(SNAPSHOT_MODEL)
        if snapshot:
            parts.append(snapshot_text(snapshot))
        vault_block = self.identity.to_prompt_block()
        if vault_block:
            parts.append(vault_block)

        pers = self.load_personality()
        interests = pers.get("interests", {})
        if interests:
            top = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:7]
            parts.append("[Интересы]\n" + ", ".join(f"{k}({v})" for k, v in top))
        for field, title in [
            ("values", "Ценности"),
            ("fears", "Страхи"),
            ("motivation", "Мотивация"),
            ("strengths", "Сильные стороны"),
            ("weaknesses", "Уязвимости"),
        ]:
            items = [str(x).strip() for x in pers.get(field, []) if str(x).strip()]
            if items:
                parts.append(f"[{title}]\n" + "\n".join(f"- {x}" for x in items[:8]))
        relationships = pers.get("relationships", {})
        if relationships:
            parts.append("[Отношения]\n" + "\n".join(f"- {k}: {v}" for k, v in relationships.items()))
        habits = pers.get("habits", {})
        if habits:
            parts.append("[Привычки]\n" + "\n".join(f"- {k}: {v}" for k, v in habits.items()))
        addictions = pers.get("addictions", {})
        if addictions:
            parts.append("[Зависимости/рисковые паттерны]\n" + "\n".join(f"- {k}: {v}" for k, v in addictions.items()))
        changes = [str(x).strip() for x in pers.get("changes", []) if str(x).strip()]
        if changes:
            parts.append("[Недавние изменения]\n" + "\n".join(f"- {x}" for x in changes[-8:]))

        try:
            from companion.user_model import user_model
            user_model_block = user_model.to_prompt_block(include_identity=False)
            if user_model_block:
                parts.append(user_model_block)
        except Exception as exc:
            logger.debug("UserModel profile enrichment skipped: %s", exc)

        return "\n\n".join(parts)

    def build_personality_snapshot_text(self) -> str:
        """Backward-compatible name for the canonical prompt profile."""
        return self.build_canonical_profile_text()

    def load_master_summary(self) -> str:
        """Load master summary from SQLite DB (meta table)."""
        return self.db.get_meta("master_summary", "")

    def save_master_summary(self, content: str) -> None:
        """Save master summary to SQLite DB (meta table)."""
        self.db.set_meta("master_summary", content)

    # ── Meta ──────────────────────────────────────────────────────────

    def get_compress_count(self) -> int:
        return int(self.db.get_meta("compress_count", "0"))

    def increment_compress_count(self) -> int:
        return self.db.increment_meta("compress_count")

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
        from companion.security.sanitizer import sanitize_markup
        sanitized_text = sanitize_markup(text) or ""
        msg = MessageRecord(
            role=role,
            text=sanitized_text,
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
        # Dedup gate: block duplicates against already active/dormant facts.
        existing = self.find_similar_fact_any_status(fact.fact)
        if existing is not None:
            logger.debug("Dedup: skipping fact similar to %s", existing.id)
            return existing

        if fact.status == "active" and hasattr(self, "governor") and self.governor:
            decision = self.governor.validate_ingestion(fact)
            if decision.action == "quarantine":
                fact.status = "quarantine"
                logger.info("ingestion_quarantine: Fact %s placed in quarantine (%s)", fact.id, decision.reason)

        d = fact.to_dict()
        vec = None
        if fact.status in ("active", "dormant"):
            vec = self.vector.embed_text_only(fact.fact)

        with self.db.atomic_memory_transaction():
            self.db._insert_fact(d)
            if hasattr(self, "world_model") and self.world_model and fact.status in ("active", "dormant"):
                self.world_model.process_fact(fact, index_entities=False)
            if fact.status in ("active", "dormant"):
                if vec is not None:
                    self.vector.upsert_embedding(fact.fact, vec, content_type="fact", fact_id=fact.id)
                else:
                    self.vector.compute_and_cache(fact.fact, content_type="fact", fact_id=fact.id)

        if self.event_bus:
            from companion.memory.events.base import FactCreatedEvent
            self.event_bus.publish(FactCreatedEvent(fact_id=fact.id, fact_text=fact.fact, importance=fact.importance, source=fact.source))
        return fact

    def recover_index_consistency(self) -> dict[str, int]:
        from companion.memory.events.sync import recover_index_consistency
        return recover_index_consistency(self)

    def get_fact(self, fact_id: str) -> Fact | None:
        row = self.db.get_fact(fact_id)
        return Fact.from_dict(row) if row else None

    def update_fact(
        self,
        fact_id: str,
        *,
        fact: str | None = None,
        importance: int | None = None,
        confidence: float | None = None,
        tags: list[str] | None = None,
        memory_kind: str | None = None,
        date: str | None = None,
    ) -> bool:
        """Edit a fact in place. Keeps FAISS and DB in sync.

        If the fact text changes, the stale FAISS vector is dropped and the
        new text is (re)embedded — otherwise semantic search keeps serving the
        old wording.
        """
        old = self.get_fact(fact_id)
        if old is None:
            return False
        fields: dict[str, Any] = {"version": old.version + 1}
        old_text = old.fact
        if fact is not None:
            fields["fact"] = fact
        if importance is not None:
            fields["importance"] = max(1, min(10, int(importance)))
        if confidence is not None:
            fields["confidence"] = max(0.0, min(1.0, float(confidence)))
        if tags is not None:
            fields["tags"] = tags
        if memory_kind is not None:
            fields["memory_kind"] = memory_kind
        if date is not None:
            fields["date"] = date
        new_text_changed = fact is not None and fact.strip() and fact.strip() != old_text.strip()
        vec_new = None
        if new_text_changed and fact is not None:
            vec_new = self.vector.embed_text_only(fact)

        with self.db.atomic_memory_transaction():
            self.db.update_fact_fields(fact_id, fields, expected_version=old.version)
            if new_text_changed and fact is not None:
                if old_text.strip():
                    self.vector.delete_for_content(old_text)
                if vec_new is not None:
                    self.vector.upsert_embedding(fact, vec_new, content_type="fact", fact_id=fact_id)
                else:
                    self.vector.compute_and_cache(fact, content_type="fact", fact_id=fact_id)

        if self.event_bus:
            from companion.memory.events.base import FactUpdatedEvent
            self.event_bus.publish(FactUpdatedEvent(fact_id=fact_id, old_state={"fact": old_text}, new_state=fields, reason="update_fact"))
        return True

    def delete_fact(self, fact_id: str) -> bool:
        """Hard-delete a fact and its FAISS vector + relations.

        Prefer marking `superseded`/`dormant` for knowledge that merely went
        stale; reserve this for genuinely wrong/duplicate data.
        """
        old = self.get_fact(fact_id)
        if old is None:
            return False
        with self.db.atomic_memory_transaction():
            if old.fact.strip():
                self.vector.delete_for_content(old.fact)
            return self.db.delete_fact(fact_id)

    def archive_fact(self, fact_id: str, reason: str = "archived") -> bool:
        """Archive a fact (never deletes from DB, but removes from FAISS index and marks archived in DB)."""
        old = self.get_fact(fact_id)
        if old is None or old.status == "archived":
            return False
        with self.db.atomic_memory_transaction():
            if old.fact.strip():
                self.vector.delete_for_content(old.fact)
            self.db.update_fact_fields(fact_id, {"status": "archived", "archived": 1}, expected_version=old.version)
        if self.event_bus:
            from companion.memory.events.base import FactUpdatedEvent
            self.event_bus.publish(
                FactUpdatedEvent(
                    fact_id=fact_id,
                    old_state={"status": old.status},
                    new_state={"status": "archived"},
                    reason=reason,
                )
            )
        return True

    def get_fact_relations(self, fact_id: str) -> list[dict[str, Any]]:
        return self.db.get_fact_relations(fact_id)

    def list_facts(self, status: str = "active") -> list[Fact]:
        rows = self.db.list_facts(status=status)
        return [Fact.from_dict(r) for r in rows]

    def recent_facts(self, limit: int = 50, status: str = "active") -> list[Fact]:
        return self.list_facts(status)[:limit]

    def list_all_facts(self) -> list[Fact]:
        rows = self.db.list_all_facts()
        return [Fact.from_dict(r) for r in rows]

    def get_random_fact(self) -> Fact | None:
        """Fetch one random active fact from SQLite DB to use as a conversation anchor."""
        with self.db._conn() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE (superseded_by IS NULL OR superseded_by = '') "
                "AND status = 'active' ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return Fact.from_dict(self.db._row_fact(row))

    def revive_dormant_fact(self, fact_id: str) -> None:
        """Promote a dormant fact back to active status."""
        fact = self.get_fact(fact_id)
        if not fact or fact.status != "dormant":
            logger.warning("Attempted to revive non-dormant fact %s (status: %s)", fact_id, fact.status if fact else "None")
            return
        from companion.memory.policies.base import PolicyDecision
        decision = PolicyDecision(
            approved=True,
            action="revive",
            updates={"status": "active", "facts_sent_count": 0},
            reason="dormant_auto_revival",
            policy_name="DormantRevivalPolicy",
        )
        self.persistence.apply_decision(fact_id, decision, reason="dormant_auto_revival", initiator="governor")
        logger.info("dormant_auto_revival: Fact %s promoted to active", fact_id)

    def search_facts(self, query: str, limit: int = 20) -> list[tuple[Fact, float]]:
        active_facts = self.list_facts("active")
        dormant_facts = self.list_facts("dormant")
        
        try:
            from companion.memory.hyde import should_use_hyde, generate_hypothetical_fact
            vector_query = generate_hypothetical_fact(query) if should_use_hyde(query) else query
            results = self.vector.search(vector_query, top_k=limit * 2, content_type="fact")
            if results:
                by_hash_active = {self.vector._content_hash(f.fact): f for f in active_facts}
                by_hash_dormant = {self.vector._content_hash(f.fact): f for f in dormant_facts}
                seen: set[str] = set()
                hits: list[tuple[Fact, float]] = []
                
                # First search only "active"
                for r in results:
                    if len(hits) >= limit:
                        break
                    f = by_hash_active.get(r["content_hash"])
                    if f and f.id not in seen:
                        seen.add(f.id)
                        hits.append((f, r["score"]))
                        
                # Then check dormant facts via FAISS (pure read-only search)
                for r in results:
                    if len(hits) >= limit:
                        break
                    f = by_hash_dormant.get(r["content_hash"])
                    if f and f.id not in seen:
                        from companion.config import DORMANT_REVIVAL_THRESHOLD
                        if r["score"] >= DORMANT_REVIVAL_THRESHOLD:
                            seen.add(f.id)
                            hits.append((f, r["score"]))
                                
                if hits:
                    return self.semantic_ranker.rerank(hits, query_text=query)[:limit]
        except Exception as exc:
            logger.debug("Vector search unavailable, falling back to keyword: %s", exc)

        q = query.lower()
        hits_fallback = [
            f for f in active_facts
            if q in f.fact.lower()
            or any(q in t.lower() for t in f.tags)
        ]
        if not hits_fallback:
            hits_fallback = [
                f for f in active_facts
                if any(w in f.fact.lower() for w in q.split() if len(w) > 3)
            ]
        
        return [(f, 0.0) for f in hits_fallback[:limit]]

    def add_relation(self, rel: FactRelation) -> None:
        from_fact = self.get_fact(rel.from_id)
        to_fact = self.get_fact(rel.to_id)
        if not from_fact or not to_fact:
            return
        allowed_statuses = {"active", "dormant"} if rel.relation in {"summarizes", "summarized_by"} else {"active"}
        if from_fact.status not in allowed_statuses or to_fact.status not in allowed_statuses:
            logger.warning(
                "Relation check failed: either source %s (%s) or target %s (%s) is not in %s",
                rel.from_id, from_fact.status, rel.to_id, to_fact.status, allowed_statuses
            )
            return

        d = rel.to_dict()
        self.db._insert_relation(d)
        if rel.relation == "supersedes":
            self.db.update_fact_status(rel.to_id, "superseded")
            old_fact = self.get_fact(rel.to_id)
            if old_fact and old_fact.fact:
                self.db.update_fact_fields(
                    rel.to_id,
                    {"superseded_by": rel.from_id, "version": old_fact.version + 1},
                )
                self.vector.delete_for_content(old_fact.fact)
        elif rel.relation == "contradicts":
            old_fact = self.get_fact(rel.to_id)
            if not old_fact:
                return
            protected = old_fact.memory_kind == "permanent" or any(
                t.lower() in {"anchor", "core_identity", "pinned"} for t in old_fact.tags
            )
            if protected:
                # Новый факт противоречит защищенному. Защищенный побеждает.
                self.db.update_fact_status(rel.from_id, "superseded")
                new_fact = self.get_fact(rel.from_id)
                if new_fact:
                    self.db.update_fact_fields(
                        rel.from_id,
                        {"superseded_by": rel.to_id, "version": new_fact.version + 1},
                    )
                    self.vector.delete_for_content(new_fact.fact)
                return
            # Newer fact wins: the old one is superseded (autonomous memory,
            # no human review). Mirror the `supersedes` branch — hide it AND
            # drop its stale vector, otherwise FAISS keeps serving it.
            self.db.update_fact_status(rel.to_id, "superseded")
            if old_fact and old_fact.fact:
                self.db.update_fact_fields(
                    rel.to_id,
                    {"superseded_by": rel.from_id, "version": old_fact.version + 1},
                )
                self.vector.delete_for_content(old_fact.fact)

    def get_connected_facts(
        self,
        fact_ids: list[str],
        max_hops: int = 2,
        max_facts: int = 10,
        min_confidence: float = 0.6,
        exclude_relations: set[str] | None = None,
    ) -> list[tuple[Fact, int, str]]:
        """Multi-hop GraphRAG traversal: find active facts connected to anchor fact_ids.

        Returns:
            List of (Fact, hop_distance, relation_description) tuples.
        """
        if not fact_ids or max_hops < 1 or max_facts < 1:
            return []

        visited: set[str] = set(fact_ids)
        queue: list[tuple[str, int, str]] = [(fid, 0, "anchor") for fid in fact_ids]
        results: list[tuple[Fact, int, str]] = []

        inv_map = {
            "supersedes": "superseded_by",
            "superseded_by": "supersedes",
            "caused_by": "causes",
            "causes": "caused_by",
            "supports": "supported_by",
            "summarized_by": "summarizes",
            "summarizes": "summarized_by",
        }

        while queue and len(results) < max_facts:
            curr_id, curr_hop, _ = queue.pop(0)
            if curr_hop >= max_hops:
                continue
            rels = self.db.get_fact_relations(curr_id)
            for rel in rels:
                if float(rel.get("confidence", 0.8)) < min_confidence:
                    continue
                from_id = rel.get("from_id", "")
                to_id = rel.get("to_id", "")
                rel_type = str(rel.get("relation", "related_to"))

                if from_id == curr_id:
                    neighbor_id = to_id
                    rel_desc = rel_type
                elif to_id == curr_id:
                    neighbor_id = from_id
                    rel_desc = inv_map.get(rel_type, f"inverse_{rel_type}")
                else:
                    continue

                if exclude_relations and (rel_type in exclude_relations or rel_desc in exclude_relations):
                    continue

                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                neighbor_fact = self.get_fact(neighbor_id)
                allowed_statuses = {"active", "dormant"} if rel_desc in {"summarizes", "summarized_by", "inverse_summarizes", "inverse_summarized_by"} else {"active"}
                if not neighbor_fact or neighbor_fact.status not in allowed_statuses:
                    continue

                results.append((neighbor_fact, curr_hop + 1, rel_desc))
                if len(results) >= max_facts:
                    break
                queue.append((neighbor_id, curr_hop + 1, rel_desc))

        return results

    def get_active_fact_texts(self) -> list[str]:
        return [f.fact for f in self.list_facts("active")]

    def find_similar_fact(self, text: str, threshold: float = 0.85) -> Fact | None:
        """Dedup via FAISS vector search cosine similarity."""
        results = self.vector.search(text, top_k=1, content_type="fact", hybrid=False)
        if results and results[0]["score"] >= threshold:
            match_hash = results[0]["content_hash"]
            for f in self.list_facts("active"):
                if self.vector._content_hash(f.fact) == match_hash:
                    return f
        return None

    def find_similar_fact_any_status(self, text: str, threshold: float = 0.88) -> Fact | None:
        """Dedup check across active and dormant facts only."""
        from companion.memory.text_sim import text_overlap
        norm = self._normalize(text)
        candidates = [f for f in self.list_all_facts() if f.status in {"active", "dormant"}]
        best: Fact | None = None
        best_score = 0.0
        for f in candidates:
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

    def search_reflections(self, query: str, limit: int = 10) -> list[Reflection]:
        active = self.list_reflections("active")
        if not query:
            return active[:limit]
        try:
            results = self.vector.search(query, top_k=limit, content_type="reflection")
            if results:
                hit_hashes = {r["content_hash"] for r in results}
                found = [r for r in active if self.vector._content_hash(r.insight) in hit_hashes]
                if found:
                    return found[:limit]
        except Exception as exc:
            logger.debug("Reflection vector search unavailable: %s", exc)

        q_norm = self._normalize(query)
        fallback = [r for r in active if q_norm in self._normalize(r.insight)]
        return fallback[:limit]

    def search_summaries(self, query: str, limit: int = 3) -> list[str]:
        if not query:
            return self.load_recent_summaries(limit)
        results = self.vector.search(query, top_k=limit, content_type="summary")
        if not results:
            return []
        return [r["content"] for r in results]

    def load_recent_summaries(self, limit: int = 10) -> list[str]:
        return [r["content"] for r in self.db.list_summaries(status="active", limit=limit)]

    # ── Patterns (Уровень 2: inferences over facts) ──────────────────

    def add_pattern(self, pat: "Pattern") -> Pattern:
        # Уровень 2 — важный момент: паттерны эволюционируют.
        # 1) Почти идентичный текст (>0.85) — чистый дубль, пропускаем.
        dup = self.find_similar_pattern(pat.pattern, threshold=0.85)
        if dup is not None:
            # Не дубль-запись, а ПОДТВЕРЖДЕНИЕ: наблюдение повторилось.
            # Именно повторяемость во времени, а не суждение LLM за один
            # проход, делает вывод чертой — поэтому bump'аем свежесть.
            self.touch_pattern(dup.id)
            return dup
        # 2) Та же тема, но ДРУГОЙ вывод (0.5..0.85) — старый паттерн
        #    устарел/противоречит. Помечаем superseded + выкидываем из FAISS,
        #    чтобы retrieval не кормил LLM противоречивыми выводами.
        related = self.find_similar_pattern(pat.pattern, threshold=0.5)
        if related is not None:
            self._supersede_pattern(related, pat)
        self.db.add_pattern(pat.to_dict())
        self.vector.compute_and_cache(pat.pattern, content_type="pattern", fact_id=pat.id)
        return pat

    def _supersede_pattern(self, old: "Pattern", new: "Pattern") -> None:
        self.db.update_pattern_fields(
            old.id, {"status": "superseded", "superseded_by": new.id}
        )
        if old.pattern.strip():
            self.vector.delete_for_content(old.pattern)

    def list_patterns(self, status: str = "active") -> list["Pattern"]:
        return [Pattern.from_dict(r) for r in self.db.list_patterns(status=status)]

    def get_pattern(self, pattern_id: str) -> "Pattern | None":
        from companion.models import Pattern
        for p in self.db.list_patterns(status=None):
            if p["id"] == pattern_id:
                return Pattern.from_dict(p)
        return None

    def search_patterns(self, query: str, limit: int = 10) -> list[tuple["Pattern", float]]:
        active = self.list_patterns("active")
        try:
            results = self.vector.search(query, top_k=limit, content_type="pattern")
            if results:
                by_hash = {self.vector._content_hash(p.pattern): p for p in active}
                hits = []
                for r in results:
                    p = by_hash.get(r["content_hash"])
                    if p:
                        self.touch_pattern(p.id)  # подтверждение при реальном use
                        hits.append((p, r["score"]))
                if hits:
                    return hits[:limit]
        except Exception as exc:
            logger.debug("Pattern vector search unavailable, falling back to keyword: %s", exc)
        q = query.lower()
        fallback = [p for p in active if q in p.pattern.lower()]
        for p in fallback[:limit]:
            self.touch_pattern(p.id)
        return [(p, 0.0) for p in fallback[:limit]]

    def find_similar_pattern(self, text: str, threshold: float = 0.85) -> "Pattern | None":
        results = self.vector.search(text, top_k=1, content_type="pattern", hybrid=False)
        if results and results[0]["score"] >= threshold:
            match_hash = results[0]["content_hash"]
            for p in self.list_patterns("active"):
                if self.vector._content_hash(p.pattern) == match_hash:
                    return p
        # Lexical fallback: pattern confirmation must not depend on the
        # embedding provider being reachable, or a model/API swap silently
        # turns every repeat observation into a fresh "trait".
        from companion.memory.text_sim import text_overlap
        norm = self._normalize(text)
        for p in self.list_patterns("active"):
            if self._normalize(p.pattern) == norm or text_overlap(text, p.pattern) >= threshold:
                return p
        return None

    def update_pattern(
        self, pattern_id: str, *, pattern: str | None = None,
        importance: int | None = None, confidence: float | None = None,
        category: str | None = None, evidence: list[str] | None = None,
    ) -> bool:
        old = self.get_pattern(pattern_id)
        if old is None:
            return False
        fields = {"version": old.version + 1}
        old_text = old.pattern
        if pattern is not None:
            fields["pattern"] = pattern
        if importance is not None:
            fields["importance"] = max(1, min(10, int(importance)))
        if confidence is not None:
            fields["confidence"] = max(0.0, min(1.0, float(confidence)))
        if category is not None:
            fields["category"] = category
        if evidence is not None:
            fields["evidence"] = evidence
        self.db.update_pattern_fields(pattern_id, fields)
        if pattern is not None and pattern.strip() and pattern.strip() != old_text.strip():
            if old_text.strip():
                self.vector.delete_for_content(old_text)
            self.vector.compute_and_cache(pattern, content_type="pattern", fact_id=pattern_id)
        return True

    def delete_pattern(self, pattern_id: str) -> bool:
        old = self.get_pattern(pattern_id)
        if old is None:
            return False
        if old.pattern.strip():
            self.vector.delete_for_content(old.pattern)
        return self.db.delete_pattern(pattern_id)

    def touch_pattern(self, pattern_id: str) -> None:
        """Reliability Layer: подтверждение паттерна (bump last_confirmed_at).

        Вызывается при реальном использовании/нахождении паттерна в retrieval,
        чтобы он не 'старел' зря. Не меняет сам вывод, только свежесть.
        """
        old = self.get_pattern(pattern_id)
        if old is None:
            return
        self.db.update_pattern_fields(pattern_id, {
            "last_confirmed_at": datetime.now().isoformat(),
            "version": old.version + 1,
        })

    # ── Communication prefs (Уровень 4) ─────────────────────────────

    def get_comm_pref(self) -> "CommPref":
        from companion.models import CommPref
        row = self.db.get_comm_pref("global")
        if row is None:
            return CommPref()
        return CommPref.from_dict(row)

    def upsert_comm_pref(self, delta: "CommPref") -> None:
        """Merge delta-обновление предпочтений общения (авто-эволюция).

        Пустые поля delta НЕ затирают существующие значения — merge
        'накопительный до заполнения, заменяющий при явном указании'.
        Списочные поля (liked/avoided topics) заменяются целиком, когда
        delta приносит непустой список. Версия инкрементируется.
        """
        from companion.models import CommPref
        current = self.get_comm_pref()
        merged = CommPref(
            style=delta.style or current.style,
            formality=delta.formality or current.formality,
            humor=delta.humor or current.humor,
            language=delta.language or current.language,
            liked_topics=delta.liked_topics if delta.liked_topics else current.liked_topics,
            avoided_topics=delta.avoided_topics if delta.avoided_topics else current.avoided_topics,
            updated_at=datetime.now().isoformat(),
            version=current.version + 1,
        )
        self.db.upsert_comm_pref(merged.to_dict())

    # ── Human model (Уровень 6) ──────────────────────────────────

    def get_human_model(self) -> "HumanModel":
        from companion.models import HumanModel
        row = self.db.get_human_model("global")
        if row is None:
            return HumanModel()
        return HumanModel.from_dict(row)

    def upsert_human_model(self, delta: "HumanModel") -> None:
        """Merge delta-выводов в модель человека (Reliability Layer).

        Каждый элемент delta — HumanModelInsight. Логика:
        - совпадение по нормализованному тексту в той же dimension →
          ПОДТВЕРЖДЕНИЕ: bump last_supported_at + evidence_count, НЕ дубликат;
        - иначе → новый инсайт (status active).
        Старение (active→aging→stale) считается лениво в compute_insight_status,
        без мутации здесь — поэтому история не теряется. Версия растёт.
        """
        from companion.models import HumanModel, HumanModelInsight
        current = self.get_human_model()
        dims = ("goals", "fears", "strengths", "recurring_mistakes", "long_term_trends")
        now = datetime.now().isoformat()

        def _norm(t: str) -> str:
            return self._normalize(t or "")

        merged = HumanModel(version=current.version + 1, updated_at=now)
        for dim in dims:
            existing: list[HumanModelInsight] = list(getattr(current, dim))
            seen = {_norm(e.text) for e in existing}
            for inc in getattr(delta, dim):
                n = _norm(inc.text)
                match = next((e for e in existing if _norm(e.text) == n), None)
                if match is not None:
                    # подтверждение: обновляем свежесть, не плодим дубликат
                    match.last_supported_at = now
                    # evidence_count — это ЧИСЛО НАБЛЮДЕНИЙ, а не число
                    # прогонов ночной задачи. Если источник принёс явный
                    # счётчик (promotion передаёт confirmations паттерна),
                    # берём максимум: он выведен из данных. Инкрементим
                    # только когда счётчика нет — тогда это действительно
                    # новое, отдельное наблюдение.
                    incoming = int(getattr(inc, "evidence_count", 1) or 1)
                    if incoming > 1:
                        match.evidence_count = max(match.evidence_count or 1, incoming)
                    else:
                        match.evidence_count = (match.evidence_count or 1) + 1
                    # Refuted-инсайт не воскресает от повторного прогона:
                    # его опровергли источники, и только источники могут
                    # его вернуть (revalidate снимет refuted, если ожили).
                    if inc.confidence and match.status != "refuted":
                        match.confidence = max(match.confidence, inc.confidence)
                    # Provenance накапливается: каждое подтверждение может
                    # опираться на новые факты, и все они должны остаться
                    # проверяемыми. Порядок сохраняем, дубли убираем.
                    if inc.evidence:
                        merged_ev = list(match.evidence or [])
                        merged_ev.extend(e for e in inc.evidence if e not in merged_ev)
                        match.evidence = merged_ev[:50]
                else:
                    seen.add(n)
                    inc.last_supported_at = now
                    existing.append(inc)
            # мягкий кап роста, без удаления статусов
            setattr(merged, dim, existing[:50])
        self.db.upsert_human_model(merged.to_dict())

    # ── Life Continuity Engine (LCE) ─────────────────────────────

    def add_transition(self, t: "LifeTransition") -> "LifeTransition":
        self.db.add_life_transition(t.to_dict())
        return t

    def update_transition(self, transition_id: str, fields: dict[str, Any]) -> None:
        self.db.update_life_transition(transition_id, fields)

    def get_transition(self, transition_id: str) -> "LifeTransition | None":
        from companion.models import LifeTransition
        row = self.db.get_life_transition(transition_id)
        return LifeTransition.from_dict(row) if row else None

    def get_active_transitions(self) -> list["LifeTransition"]:
        from companion.models import LifeTransition
        return [LifeTransition.from_dict(r) for r in self.db.list_life_transitions("active")]

    def get_pending_transitions(self) -> list["LifeTransition"]:
        from companion.models import LifeTransition
        return [LifeTransition.from_dict(r) for r in self.db.list_life_transitions("pending_review")]

    def get_recent_transitions(self, limit: int = 10) -> list["LifeTransition"]:
        from companion.models import LifeTransition
        return [LifeTransition.from_dict(r) for r in self.db.list_life_transitions(None)[:limit]]

    def touch_transition(self, transition_id: str) -> None:
        """Подтверждение перехода при реальном использовании в retrieval."""
        old = self.get_transition(transition_id)
        if old is None:
            return
        self.db.update_life_transition(transition_id, {
            "last_confirmed_at": datetime.now().isoformat(),
            "version": old.version + 1,
        })

    def confirm_or_review_transition(
        self, t: "LifeTransition", confidence_threshold: float = 0.65
    ) -> "LifeTransition":
        """Низкая уверенность → pending_review (карантин, как у фактов).

        LLM склонен придумывать красивую историю там, где просто совпали
        два факта. Поэтому переход с confidence < порога НЕ попадает в промпт
        до ручного подтверждения."""
        if t.confidence < confidence_threshold and t.status != "pending_review":
            t.status = "pending_review"
        return t

    def save_summary(self, content: str) -> None:
        summary_id = f"summary_{uuid.uuid4().hex[:10]}"
        self.db._insert_summary({
            "id": summary_id,
            "content": content,
            "created_at": datetime.now().isoformat(),
            "status": "active"
        })
        self.vector.compute_and_cache(content, content_type="summary")
        
        # Enforce 50 active limit
        active_summaries = self.db.list_summaries(status="active")
        if len(active_summaries) > 50:
            to_archive = active_summaries[50:]
            for s in to_archive:
                self.db.update_summary_status(s["id"], "archived")

    def update_reflection(self, reflection: Reflection) -> None:
        with self.db._conn() as conn:
            conn.execute(
                "UPDATE reflections SET importance=?, created_at=? WHERE id=?",
                (reflection.importance, reflection.created_at, reflection.id)
            )

    # ── Beliefs ───────────────────────────────────────────────────────

    def add_belief(self, belief: str, based_on: list[str], importance: int = 6) -> None:
        from companion.security.sanitizer import sanitize_markup, _looks_like_injection
        belief = sanitize_markup(belief).strip() if belief else ""
        if not belief:
            return
        # Дедуп: не вставляем убеждение, если идентичное (нормализованное) уже есть.
        # Раньше каждый compress переписывал те же beliefs → 405 строк при 20 уникальных.
        norm = self._normalize(belief)
        for existing in self.list_beliefs():
            if self._normalize(existing.get("belief", "")) == norm:
                return
        status = "pending_review" if _looks_like_injection(belief) else "active"
        d = {
            "id": f"belief_{uuid.uuid4().hex[:10]}",
            "belief": belief,
            "based_on": based_on,
            "importance": importance,
            "status": status,
            "created_at": datetime.now().isoformat(),
        }
        self.db._insert_belief(d)
        self.vector.compute_and_cache(belief, content_type="belief")

    def list_beliefs(self) -> list[dict[str, Any]]:
        return self.db.list_beliefs()


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
        """Phase 5: Dormant Memory System — never delete, set to dormant."""
        to_dormant: list[str] = []

        for f in self.list_facts("active"):
            if f.memory_kind == "permanent" or any(
                t.lower() in ["anchor", "core_identity", "pinned"] for t in f.tags
            ):
                continue
            age = days_since(f.date or f.created_at)
            # Both old thresholds now just move to dormant
            if age > 90 and f.importance <= 4 and f.status == "active":
                to_dormant.append(f.id)

        for fid in to_dormant:
            from companion.memory.policies.base import PolicyDecision
            decision = PolicyDecision(
                approved=True,
                action="dormant",
                updates={"status": "dormant", "facts_sent_count": 0},
                reason="dormant_aging_policy",
                policy_name="DormantAgingPolicy",
            )
            self.persistence.apply_decision(fid, decision, reason="dormant_aging_policy", initiator="hygiene_service")

        return len(to_dormant)

    def compress_dormant_episodes(self, batch_size: int = 10, min_facts: int = 3) -> list[Fact]:
        """Phase 3: Run episodic compression on unsummarized dormant facts."""
        from companion.memory.episodic_compression import EpisodicMemoryCompressor
        compressor = EpisodicMemoryCompressor(self, batch_size=batch_size, min_facts_to_compress=min_facts)
        return compressor.run_compression()

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
        # REMOVED: Reflections should not be in semantic search to prevent recursive self-contamination
        # self.vector.compute_and_cache_batch(refl_texts, content_type="reflection")
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
                     counts["facts"], counts["beliefs"], counts["reflections"], counts["causal_links"])
        return counts

    def stats(self) -> dict[str, int]:
        from companion.memory.vector_index import get_embedding_stats
        estats = get_embedding_stats()
        return {
            "facts_active": self.db.count_facts("active"),
            "facts_total": self.db.count_facts(None),
            "messages": self.db.count_messages(),
            "reflections": len(self.db.list_reflections()),
            "beliefs": len(self.db.list_beliefs()),
            "compress_count": self.get_compress_count(),
            "embedding_failures": estats.get("failures", 0),
            "embedding_zero_vectors": estats.get("zero_vectors_generated", 0),
        }

    def analyze_retrieval_effectiveness(self) -> dict[str, int]:
        """Analyze retrieval metrics and adjust fact importance based on usage patterns."""
        adjusted = {"boosted": 0, "lowered": 0}
        try:
            with self.db._conn() as conn:
                rows = conn.execute("SELECT id, importance, facts_sent_count, facts_used_count, tags, memory_kind FROM facts WHERE status='active'").fetchall()
                for row in rows:
                    fid = row["id"]
                    imp = int(row["importance"])
                    sent = int(row["facts_sent_count"])
                    used = int(row["facts_used_count"])
                    tags = str(row["tags"] or "").lower()
                    kind = str(row["memory_kind"] or "").lower()
                    
                    if kind == "permanent" or any(t in tags for t in ["anchor", "core_identity", "pinned"]):
                        continue
                        
                    if sent > 20 and used == 0:
                        pass  # Preserve cumulative retrieved_count
                    elif sent > 5 and used > 3:
                        new_imp = min(8, imp + 1)
                        if new_imp != imp:
                            from companion.memory.policies.base import PolicyDecision
                            decision = PolicyDecision(
                                approved=True,
                                action="boost",
                                updates={"importance": new_imp},
                                reason="retrieval_effectiveness_analysis",
                                policy_name="RetrievalEffectivenessPolicy",
                            )
                            self.persistence.apply_decision(fid, decision, reason="retrieval_effectiveness_analysis", initiator="feedback_loop")
                            logger.info("memory_feedback_loop_applied: %s boosted from %d to %d", fid, imp, new_imp)
                            adjusted["boosted"] += 1
        except Exception as e:
            logger.error("Retrieval effectiveness analysis failed: %s", e)
        return adjusted

    async def async_add_fact(self, fact: Fact) -> Fact:
        import asyncio
        return await asyncio.to_thread(self.add_fact, fact)

    async def async_update_fact(self, fact_id: str, **kwargs) -> bool:
        import asyncio
        return await asyncio.to_thread(self.update_fact, fact_id, **kwargs)

    async def async_search_facts(self, query: str, limit: int = 20) -> list[tuple[Fact, float]]:
        import asyncio
        return await asyncio.to_thread(self.search_facts, query, limit=limit)

    async def async_add_relation(self, rel: FactRelation) -> None:
        import asyncio
        return await asyncio.to_thread(self.add_relation, rel)
    
    async def async_add_pattern(self, pat: "Pattern") -> "Pattern":
        import asyncio
        return await asyncio.to_thread(self.add_pattern, pat)
    
    async def async_search_patterns(self, query: str, limit: int = 10) -> list[tuple["Pattern", float]]:
        import asyncio
        return await asyncio.to_thread(self.search_patterns, query, limit=limit)

    async def async_search_reflections(self, query: str, limit: int = 10) -> list[Reflection]:
        import asyncio
        return await asyncio.to_thread(self.search_reflections, query, limit=limit)
    
    async def async_add_reflection(self, reflection: Reflection) -> Reflection:
        import asyncio
        return await asyncio.to_thread(self.add_reflection, reflection)
