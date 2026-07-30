"""Unified memory store — facts, messages, relations, reflections, beliefs.

Phase C0: Integrated with Event Sourcing for full audit trail.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from companion.config import (
    DATA_DIR,
)
from companion.memory.importance import days_since
from companion.memory.text_sim import text_overlap
from companion.memory.vector_index import VectorIndex
from companion.memory.identity_vault import IdentityVault
from companion.memory.events import MemoryEvent, MemoryEventType
from companion.memory.event_store import EventStore
from companion.memory.controller import MemoryGovernanceController
from companion.memory.governance import GovernanceContext, MemoryCapability
from companion.models import Fact, FactRelation, MessageRecord, Reflection
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self) -> None:
        self.db = MemoryDatabase()
        self.vector = VectorIndex()
        self.identity = IdentityVault(self.db.path)
        self.events = EventStore(self.db.path)
        self.governance = MemoryGovernanceController()
        import threading
        self._cache_lock = threading.Lock()

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
        """Load personality from SQLite DB (meta table). Migrates from personality.json if needed."""
        from companion.config import PERSONALITY_PATH, EMPTY_PERSONALITY
        val = self.db.get_meta("personality", "")
        if val:
            try:
                return json.loads(val)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse personality from DB: %s", e)
                
        # Migration
        if os.path.exists(PERSONALITY_PATH):
            try:
                with open(PERSONALITY_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                self.save_personality(data)
                try:
                    os.remove(PERSONALITY_PATH)
                except OSError:
                    pass
                return data
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load personality: %s", e)
        return dict(EMPTY_PERSONALITY)

    def save_personality(self, data: dict[str, Any]) -> None:
        """Save personality to SQLite DB."""
        self.db.set_meta("personality", json.dumps(data, ensure_ascii=False))

    def build_personality_snapshot_text(self) -> str:
        """Build a text representation of personality for prompts.

        Combines IdentityVault (core facts) with personality.json (interests/habits).
        """
        parts: list[str] = []
        # Primary: IdentityVault
        vault_block = self.identity.to_prompt_block()
        if vault_block:
            parts.append(vault_block)
        # Secondary: personality.json enrichment
        pers = self.load_personality()
        interests = pers.get("interests", {})
        if interests:
            top = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:7]
            parts.append("[Интересы]\n" + ", ".join(f"{k}({v})" for k, v in top))
        relationships = pers.get("relationships", {})
        if relationships:
            parts.append("[Отношения]\n" + "\n".join(f"- {k}: {v}" for k, v in relationships.items()))
        habits = pers.get("habits", {})
        if habits:
            parts.append("[Привычки]\n" + "\n".join(f"- {k}: {v}" for k, v in habits.items()))
        return "\n\n".join(parts)

    def load_master_summary(self) -> str:
        """Load master summary from SQLite DB (meta table). Migrates from file if needed."""
        from companion.config import BASE_DIR
        val = self.db.get_meta("master_summary", "")
        if val:
            return val
            
        master_path = os.path.join(BASE_DIR, "master_summary.txt")
        if os.path.exists(master_path):
            with open(master_path, encoding="utf-8") as f:
                content = f.read().strip()
            self.save_master_summary(content)
            try:
                os.remove(master_path)
            except OSError:
                pass
            return content
        return ""

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

    def add_fact(self, fact: Fact, actor: str = "SYSTEM", log_event: bool = True) -> Fact:
        """Add a fact with event logging (Phase C0).
        
        Args:
            fact: The fact to add.
            actor: Who initiated this (USER_DIRECT, LLM_EXTRACTOR, etc).
            log_event: Whether to log to event store (default True for Phase C0).
        """
        d = fact.to_dict()
        self.db._insert_fact(d)
        self.vector.compute_and_cache(fact.fact, content_type="fact")
        
        if log_event:
            event = MemoryEvent(
                aggregate_id=fact.id,
                event_type=MemoryEventType.FACT_CREATED,
                actor=actor,
                payload=d,
                metadata={
                    "origin": str(getattr(fact, 'origin', 'llm_extraction')),
                    "source_message_id": getattr(fact, 'source_message_id', None),
                },
            )
            self.events.append(event)
        
        return fact

    def get_fact(self, fact_id: str) -> Fact | None:
        row = self.db.get_fact(fact_id)
        return Fact.from_dict(row) if row else None

    def list_facts(self, status: str = "active") -> list[Fact]:
        rows = self.db.list_facts(status=status)
        return [Fact.from_dict(r) for r in rows]

    def list_all_facts(self) -> list[Fact]:
        rows = self.db.list_all_facts()
        return [Fact.from_dict(r) for r in rows]

    def revive_dormant_fact(self, fact_id: str, actor: str = "SYSTEM") -> None:
        """Promote a dormant fact back to active status."""
        fact = self.get_fact(fact_id)
        if not fact or fact.status != "dormant":
            logger.warning("Attempted to revive non-dormant fact %s (status: %s)", fact_id, fact.status if fact else "None")
            return

        ctx = GovernanceContext.create(
            actor=actor,
            capabilities={MemoryCapability.CHANGE_STATUS},
            reason="Reviving dormant fact",
            identity_layer=getattr(fact, "identity_layer", None),
        )
        decision = self.governance.authorize_status_transition("dormant", "active", ctx)
        if not decision.allowed:
            logger.warning("Governance denied revive for fact %s: %s", fact_id, decision.reason)
            return

        event = MemoryEvent(
            aggregate_id=fact_id,
            event_type=MemoryEventType.FACT_STATUS_CHANGED,
            actor=actor,
            payload={
                "aggregate_id": fact_id,
                "old_state": {"status": "dormant", "facts_sent_count": getattr(fact, "facts_sent_count", 0)},
                "new_state": {"status": "active", "facts_sent_count": 0},
                "changed_fields": ["status", "facts_sent_count"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.events.append(event)
        with self.db._conn() as conn:
            conn.execute("UPDATE facts SET status='active', facts_sent_count=0 WHERE id=?", (fact_id,))
        logger.info("dormant_auto_revival: Fact %s promoted to active", fact_id)

    def search_facts(self, query: str, limit: int = 20, return_scores: bool = False) -> list[tuple[Fact, float]]:
        active_facts = self.list_facts("active")
        dormant_facts = self.list_facts("dormant")
        
        try:
            results = self.vector.search(query, top_k=limit * 2, content_type="fact")
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
                        
                # Then check dormant facts via FAISS
                for r in results:
                    if len(hits) >= limit:
                        break
                    f = by_hash_dormant.get(r["content_hash"])
                    if f and f.id not in seen:
                        from companion.config import DORMANT_REVIVAL_THRESHOLD
                        if r["score"] >= DORMANT_REVIVAL_THRESHOLD:
                            # Б-7 FIX: факт прошёл порог revival — переводим в active,
                            # иначе он навсегда остаётся dormant и отфильтровывается
                            # последующими проверками status == "active".
                            self.revive_dormant_fact(f.id)
                            f.status = "active"
                            seen.add(f.id)
                            hits.append((f, r["score"]))
                                
                if hits:
                    return hits
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

    def add_relation(self, rel: FactRelation, actor: str = "SYSTEM", log_event: bool = True) -> None:
        from_fact = self.get_fact(rel.from_id)
        to_fact = self.get_fact(rel.to_id)
        if not from_fact or not to_fact:
            return
        if from_fact.status != "active" or to_fact.status != "active":
            logger.warning(
                "Relation check failed: either source %s (%s) or target %s (%s) is not active",
                rel.from_id, from_fact.status, rel.to_id, to_fact.status
            )
            return

        d = rel.to_dict()
        self.db._insert_relation(d)
        
        if log_event:
            event = MemoryEvent(
                aggregate_id=rel.id,
                event_type=MemoryEventType.FACT_SUPERSEDED if rel.relation == "supersedes" else MemoryEventType.FACT_UPDATED,
                actor=actor,
                payload=d,
                metadata={"relation_type": rel.relation, "superseded_by": rel.from_id},
            )
            self.events.append(event)
        
        if rel.relation == "supersedes":
            self.db.update_fact_status(rel.to_id, "superseded")
            old_fact = self.get_fact(rel.to_id)
            if old_fact and old_fact.fact:
                self.vector.delete_for_content(old_fact.fact)
            
            # Log status change event
            if log_event:
                status_event = MemoryEvent(
                    aggregate_id=rel.to_id,
                    event_type=MemoryEventType.FACT_STATUS_CHANGED,
                    actor=actor,
                    payload={
                        "aggregate_id": rel.to_id,
                        "old_state": {"status": "active"},
                        "new_state": {"status": "superseded", "superseded_by": rel.from_id},
                        "changed_fields": ["status", "superseded_by"],
                        "old_status": "active",
                        "new_status": "superseded",
                        "superseded_by": rel.from_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    metadata={"superseded_by": rel.from_id},
                )
                self.events.append(status_event)

    def get_active_fact_texts(self) -> list[str]:
        return [f.fact for f in self.list_facts("active")]

    def find_similar_fact(self, text: str, threshold: float = 0.52) -> Fact | None:
        """Dedup via FAISS vector search + n-grams."""
        norm = self._normalize(text)
        results = self.search_facts(text, limit=5)
        best: Fact | None = None
        best_score = 0.0
        for f, _ in results:
            score = text_overlap(norm, self._normalize(f.fact))
            if score > best_score:
                best_score = score
                best = f
        return best if best_score >= threshold else None

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().strip())

    # ── Reflections ─────────────────────────────────────────────────

    def add_reflection(self, reflection: Reflection, actor: str = "SYSTEM", log_event: bool = True) -> Reflection:
        d = reflection.to_dict()
        self.db._insert_reflection(d)
        
        if log_event:
            event = MemoryEvent(
                aggregate_id=reflection.id,
                event_type=MemoryEventType.PATTERN_FORMED,
                actor=actor,
                payload=d,
                metadata={"reflection_type": "insight"},
            )
            self.events.append(event)
        
        return reflection

    def list_reflections(self, status: str = "active") -> list[Reflection]:
        rows = self.db.list_reflections(status=status)
        return [Reflection.from_dict(r) for r in rows]

    def search_reflections(self, query: str, limit: int = 10) -> list[Reflection]:
        if not query:
            return self.list_reflections("active")[:limit]
        results = self.vector.search(query, top_k=limit, content_type="reflection")
        if not results:
            return []
        hit_hashes = {r["content_hash"] for r in results}
        return [r for r in self.list_reflections("active") if self.vector._content_hash(r.insight) in hit_hashes]

    def search_summaries(self, query: str, limit: int = 3) -> list[str]:
        if not query:
            return self.load_recent_summaries(limit)
        results = self.vector.search(query, top_k=limit, content_type="summary")
        if not results:
            return []
        return [r["content"] for r in results]

    def load_recent_summaries(self, limit: int = 10) -> list[str]:
        return [r["content"] for r in self.db.list_summaries(status="active", limit=limit)]
        
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

    def apply_importance_decay(self, actor: str = "SYSTEM") -> int:
        """Phase 5: Dormant Memory System — never delete, set to dormant."""
        to_dormant: list[Fact] = []

        for f in self.list_facts("active"):
            if f.memory_kind == "permanent" or any(
                t.lower() in ["anchor", "core_identity", "pinned"] for t in f.tags
            ):
                continue
            age = days_since(f.date or f.created_at)
            # Both old thresholds now just move to dormant
            if age > 90 and f.importance <= 4 and f.status == "active":
                to_dormant.append(f)

        decayed_count = 0
        with self.db._conn() as conn:
            for f in to_dormant:
                ctx = GovernanceContext.create(
                    actor=actor,
                    capabilities={MemoryCapability.RUN_DECAY, MemoryCapability.CHANGE_STATUS},
                    reason="Automatic importance decay",
                    identity_layer=getattr(f, "identity_layer", None),
                )
                decision = self.governance.authorize_status_transition("active", "dormant", ctx)
                if not decision.allowed:
                    logger.warning("Governance denied decay for fact %s: %s", f.id, decision.reason)
                    continue

                event = MemoryEvent(
                    aggregate_id=f.id,
                    event_type=MemoryEventType.FACT_STATUS_CHANGED,
                    actor="GOVERNANCE",
                    payload={
                        "aggregate_id": f.id,
                        "old_state": {"status": "active"},
                        "new_state": {"status": "dormant", "facts_sent_count": 0},
                        "changed_fields": ["status", "facts_sent_count"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self.events.append(event)
                conn.execute("UPDATE facts SET status='dormant', facts_sent_count=0 WHERE id=?", (f.id,))
                decayed_count += 1

        return decayed_count

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
                        
                    if sent > 10 and used == 0:
                        new_imp = max(3, imp - 1)
                        if new_imp != imp:
                            event = MemoryEvent(
                                aggregate_id=fid,
                                event_type=MemoryEventType.FACT_UPDATED,
                                actor="RETRIEVAL_FEEDBACK",
                                payload={
                                    "aggregate_id": fid,
                                    "old_state": {"importance": imp, "facts_sent_count": sent},
                                    "new_state": {"importance": new_imp, "facts_sent_count": 0},
                                    "changed_fields": ["importance", "facts_sent_count"],
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            self.events.append(event)
                            conn.execute("UPDATE facts SET importance=?, facts_sent_count=0 WHERE id=?", (new_imp, fid))
                            logger.info("memory_feedback_loop_applied: %s lowered from %d to %d", fid, imp, new_imp)
                            adjusted["lowered"] += 1
                    elif sent > 5 and used > 3:
                        new_imp = min(8, imp + 1)
                        if new_imp != imp:
                            event = MemoryEvent(
                                aggregate_id=fid,
                                event_type=MemoryEventType.FACT_UPDATED,
                                actor="RETRIEVAL_FEEDBACK",
                                payload={
                                    "aggregate_id": fid,
                                    "old_state": {"importance": imp},
                                    "new_state": {"importance": new_imp},
                                    "changed_fields": ["importance"],
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            self.events.append(event)
                            conn.execute("UPDATE facts SET importance=? WHERE id=?", (new_imp, fid))
                            logger.info("memory_feedback_loop_applied: %s boosted from %d to %d", fid, imp, new_imp)
                            adjusted["boosted"] += 1
        except Exception as e:
            logger.error("Retrieval effectiveness analysis failed: %s", e)
        return adjusted
