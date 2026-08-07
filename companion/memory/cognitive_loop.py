"""Phase 2.3: Cognitive Loop — Active world model update, planning, budget, fusion, and feedback.

Implements all 10 Cognitive Loop mechanisms without adding large tables:
1. Retrieval Planner
2. Retrieval Budget
3. Memory Fusion
4. Context Compression
5. Conflict Resolver
6. Prediction Feedback
7. Goal Feedback
8. Memory Write Planner
9. Importance Feedback
10. Pipeline Telemetry
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from companion.llm.token_budget import estimate_tokens
from companion.models import Entity, Fact

logger = logging.getLogger(__name__)


# =====================================================================
# 1. RETRIEVAL PLANNER
# =====================================================================

@dataclass
class RetrievalPlan:
    """Specifies which memory subsystems to query based on query intent."""
    fetch_entities: bool = True
    fetch_episodes: bool = True
    fetch_facts: bool = True
    fetch_beliefs: bool = True
    fetch_goals: bool = False
    fetch_predictions: bool = False
    reason: str = "default"


class RetrievalPlanner:
    """Analyzes query to select required memory subsystems before retrieval."""

    @classmethod
    def plan_retrieval(cls, query: str, intent: str = "") -> RetrievalPlan:
        lowered = query.lower()

        # Check goals
        fetch_goals = any(
            kw in lowered for kw in ("цел", "план", "задач", "достиг", "прогресс", "goal")
        )

        # Check predictions
        fetch_predictions = any(
            kw in lowered for kw in ("прогноз", "будущ", "ожидан", "предсказ", "prediction")
        )

        # Check entities (proper names or entity queries)
        fetch_entities = bool(
            re.search(r"\b[A-ZА-Я][a-zа-я]{2,}\b", query)
            or any(kw in lowered for kw in ("кто", "как там", "где", "что за"))
        )

        # Check episodes (past situations, events, timeline)
        fetch_episodes = any(
            kw in lowered
            for kw in ("помнишь", "случилось", "ситуац", "было", "когда", "раньше", "эпизод")
        )

        # Default for general questions: entities, episodes, facts, beliefs
        if not (fetch_goals or fetch_predictions):
            return RetrievalPlan(
                fetch_entities=True,
                fetch_episodes=fetch_episodes or True,
                fetch_facts=True,
                fetch_beliefs=True,
                fetch_goals=fetch_goals,
                fetch_predictions=fetch_predictions,
                reason="standard_cognition",
            )

        return RetrievalPlan(
            fetch_entities=fetch_entities or True,
            fetch_episodes=fetch_episodes,
            fetch_facts=True,
            fetch_beliefs=True,
            fetch_goals=fetch_goals,
            fetch_predictions=fetch_predictions,
            reason="targeted_cognition",
        )


# =====================================================================
# 2. RETRIEVAL BUDGET
# =====================================================================

@dataclass
class RetrievalBudget:
    """Token budget allocation across RAG memory subsystems."""
    total_tokens: int = 6000
    entities_budget: int = 800
    episodes_budget: int = 1800
    facts_budget: int = 2200
    beliefs_budget: int = 1200


class RetrievalBudgetAllocator:
    """Allocates token budget dynamically based on RetrievalPlan."""

    @classmethod
    def allocate(cls, plan: RetrievalPlan, total_tokens: int = 6000) -> RetrievalBudget:
        # Default baseline weights
        weights = {
            "entities": 0.15 if plan.fetch_entities else 0.0,
            "episodes": 0.30 if plan.fetch_episodes else 0.0,
            "facts": 0.35 if plan.fetch_facts else 0.0,
            "beliefs": 0.20 if plan.fetch_beliefs else 0.0,
        }
        total_w = sum(weights.values())
        if total_w == 0:
            return RetrievalBudget(total_tokens=total_tokens)

        # Normalize weights to sum to 1.0
        norm = {k: v / total_w for k, v in weights.items()}
        return RetrievalBudget(
            total_tokens=total_tokens,
            entities_budget=int(total_tokens * norm["entities"]),
            episodes_budget=int(total_tokens * norm["episodes"]),
            facts_budget=int(total_tokens * norm["facts"]),
            beliefs_budget=int(total_tokens * norm["beliefs"]),
        )


# =====================================================================
# 4. CONTEXT COMPRESSION
# =====================================================================

class ContextCompressor:
    """Compresses memory items that exceed token budgets instead of truncating."""

    @classmethod
    def compress_facts(
        cls, facts: list[Fact], max_tokens: int
    ) -> list[Fact | str]:
        if not facts:
            return []

        total_est = sum(estimate_tokens(f.fact) for f in facts)
        if total_est <= max_tokens:
            return facts

        # Keep top facts that fit in ~60% of budget, summarize the remaining 40%
        target_direct = int(max_tokens * 0.6)
        direct_items: list[Fact | str] = []
        current_tokens = 0
        overflow_facts: list[Fact] = []

        for f in facts:
            est = estimate_tokens(f.fact)
            if current_tokens + est <= target_direct:
                direct_items.append(f)
                current_tokens += est
            else:
                overflow_facts.append(f)

        if overflow_facts:
            # Cluster/summarize overflow facts
            summary_parts = [f"- {f.fact}" for f in overflow_facts[:8]]
            overflow_count = len(overflow_facts)
            summary_str = (
                f"[Сжатое резюме {overflow_count} дополнительных фактов]:\n"
                + "\n".join(summary_parts)
            )
            direct_items.append(summary_str)

        return direct_items


# =====================================================================
# 5. CONFLICT RESOLVER
# =====================================================================

class ConflictResolver:
    """Detects contradictions between incoming messages and existing facts/beliefs."""

    @classmethod
    def detect_conflicts(
        cls,
        query: str,
        retrieved_facts: list[Fact],
        retrieved_beliefs: list[Any],
        entities: list[Entity],
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        q_lower = query.lower()

        # Simple semantic contradiction heuristic: e.g. negations on known entity attributes or facts
        for f in retrieved_facts:
            f_lower = f.fact.lower()
            # Check if query contradicts an existing affirmative statement
            if ("не " in q_lower and "не " not in f_lower) or (
                "не " not in q_lower and "не " in f_lower
            ):
                # Check keyword overlap
                q_words = set(re.findall(r"\w{4,}", q_lower)) - {"было", "когда", "почему"}
                f_words = set(re.findall(r"\w{4,}", f_lower))
                overlap = len(q_words & f_words)
                if overlap >= 2:
                    conflicts.append(
                        {
                            "old": f.fact,
                            "new": query,
                            "confidence": float(f.confidence),
                            "type": "fact_contradiction",
                        }
                    )
        return conflicts[:3]  # Return top 3 conflicts


# =====================================================================
# 3. MEMORY FUSION & STRUCTURED CONTEXT
# =====================================================================

@dataclass
class MemoryContext:
    """Unified structured memory context generated by Memory Fusion."""
    entities: list[Entity] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    facts: list[Fact | str] = field(default_factory=list)
    beliefs: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    plan: RetrievalPlan | None = None
    budget: RetrievalBudget | None = None

    def to_prompt_block(self) -> str:
        parts = ["[MEMORY CONTEXT (FUSED)]"]

        if self.entities:
            ent_strs = [f"- {e.name} ({e.type}): imp={e.importance}" for e in self.entities]
            parts.append("<entities>\n" + "\n".join(ent_strs) + "\n</entities>")

        if self.episodes:
            ep_strs = [
                f"- [{ep.get('date', '')}] {ep.get('event', ep.get('title', ''))}"
                for ep in self.episodes
            ]
            parts.append("<episodes>\n" + "\n".join(ep_strs) + "\n</episodes>")

        if self.facts:
            f_strs = []
            for item in self.facts:
                if isinstance(item, Fact):
                    f_strs.append(f"- {item.fact} (imp={item.importance})")
                else:
                    f_strs.append(str(item))
            parts.append("<facts>\n" + "\n".join(f_strs) + "\n</facts>")

        if self.beliefs:
            b_strs = [f"- {b.get('belief', str(b))}" for b in self.beliefs]
            parts.append("<beliefs>\n" + "\n".join(b_strs) + "\n</beliefs>")

        if self.timeline:
            tl_strs = [f"- {t.get('date', '')}: {t.get('event', '')}" for t in self.timeline]
            parts.append("<timeline>\n" + "\n".join(tl_strs) + "\n</timeline>")

        if self.conflicts:
            c_strs = []
            for c in self.conflicts:
                c_strs.append(
                    f"Conflict:\nOld: {c.get('old')}\nNew: {c.get('new')}\nConfidence: {c.get('confidence')}"
                )
            parts.append("<conflicts>\n" + "\n".join(c_strs) + "\n</conflicts>")

        return "\n".join(parts)


class MemoryFusionService:
    """Retrieves items according to RetrievalPlan & Budget and merges into MemoryContext."""

    def __init__(self, db: Any, world_model: Any = None) -> None:
        self.db = db
        self.world_model = world_model

    def fuse_context(
        self,
        query: str,
        plan: RetrievalPlan,
        budget: RetrievalBudget,
        retrieved_facts: list[Fact],
        retrieved_beliefs: list[Any],
        retrieved_episodes: list[dict[str, Any]],
        retrieved_entities: list[Entity],
    ) -> MemoryContext:
        # Apply budget compression on facts
        compressed_facts = ContextCompressor.compress_facts(
            retrieved_facts, budget.facts_budget
        )

        # Check conflicts
        conflicts = ConflictResolver.detect_conflicts(
            query, retrieved_facts, retrieved_beliefs, retrieved_entities
        )

        # Load timeline events
        events = self.db.load_events()
        timeline_items = events[-10:] if events else []

        return MemoryContext(
            entities=retrieved_entities if plan.fetch_entities else [],
            episodes=retrieved_episodes if plan.fetch_episodes else [],
            facts=compressed_facts if plan.fetch_facts else [],
            beliefs=retrieved_beliefs if plan.fetch_beliefs else [],
            timeline=timeline_items,
            conflicts=conflicts,
            plan=plan,
            budget=budget,
        )


# =====================================================================
# 6. PREDICTION FEEDBACK
# =====================================================================

class PredictionFeedbackService:
    """Evaluates active predictions and updates confidence based on interaction outcome."""

    @classmethod
    def evaluate_predictions(
        cls, query: str, response: str, db: Any
    ) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        if not hasattr(db, "list_predictions"):
            return updates

        pending = db.list_predictions(outcome="pending", limit=10)
        q_lower = query.lower()

        for p in pending:
            hypo = str(p.get("hypothesis", "")).lower()
            pid = str(p.get("prediction_id", ""))
            if not pid or not hypo:
                continue

            hypo_words = set(re.findall(r"\w{3,}", hypo))
            q_words = set(re.findall(r"\w{3,}", q_lower))
            overlap = len(hypo_words & q_words)
            has_confirm = any(w in q_lower for w in ("да", "верно", "сделал", "получилось", "завершил", "сбылось", "исполнилось"))
            has_deny = any(w in q_lower for w in ("нет", "неверно", "не получилось", "провалил", "ошибка", "не сбылось"))
            
            if overlap >= 1 or has_confirm or has_deny:
                if has_confirm:
                    p["outcome"] = "correct"
                    p["confidence"] = min(1.0, float(p.get("confidence", 0.5)) + 0.1)
                    db.upsert_prediction(p)
                    updates.append({"prediction_id": pid, "outcome": "correct", "confidence": p["confidence"]})
                elif has_deny:
                    p["outcome"] = "wrong"
                    p["confidence"] = max(0.0, float(p.get("confidence", 0.5)) - 0.1)
                    db.upsert_prediction(p)
                    updates.append({"prediction_id": pid, "outcome": "wrong", "confidence": p["confidence"]})

        return updates


# =====================================================================
# 7. GOAL FEEDBACK
# =====================================================================

class GoalFeedbackService:
    """Automatically updates goal status and progress from interaction."""

    @classmethod
    def update_goals_from_interaction(
        cls, query: str, response: str, db: Any
    ) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        active_goals = db.list_goals(status="active")
        q_lower = query.lower()

        for g in active_goals:
            title = str(g.get("title", "")).lower()
            desc = str(g.get("description", "")).lower()
            gid = str(g.get("goal_id") or g.get("id", ""))
            if not gid or not title:
                continue

            goal_words = set(re.findall(r"\w{3,}", title + " " + desc))
            q_words = set(re.findall(r"\w{3,}", q_lower))
            overlap = len(goal_words & q_words)
            has_goal_ref = any(kw in q_lower for kw in ("цель", "задач", "goal"))
            
            if overlap >= 1 or (has_goal_ref and len(active_goals) == 1):
                if any(kw in q_lower for kw in ("сделал", "закончил", "завершил", "готово", "done")):
                    db.update_goal(gid, {"status": "completed"})
                    updates.append({"goal_id": gid, "new_status": "completed"})
                elif any(kw in q_lower for kw in ("застрял", "проблема", "мешает", "не могу", "blocked")):
                    db.update_goal(gid, {"status": "blocked"})
                    updates.append({"goal_id": gid, "new_status": "blocked"})
                elif any(kw in q_lower for kw in ("продвинулся", "делаю", "в процессе", "работаем")):
                    # Add progress marker
                    markers = g.get("progress_markers") or []
                    if isinstance(markers, list):
                        markers.append({"date": datetime.now().isoformat()[:10], "note": query[:50]})
                        db.update_goal(gid, {"progress_markers": markers})
                        updates.append({"goal_id": gid, "progress": "marker_added"})

        return updates


# =====================================================================
# 8. MEMORY WRITE PLANNER & EXECUTOR
# =====================================================================

@dataclass
class MemoryWritePlanItem:
    """A single planned write action for memory."""
    action: str  # save_fact, save_episode, save_entity, save_goal, save_prediction, save_belief, nothing
    payload: dict[str, Any]
    reason: str = ""


class MemoryWritePlanner:
    """Decides what to store after an exchange (Fact, Episode, Entity, Goal, Prediction, Belief, or Nothing)."""

    @classmethod
    def plan_write(
        cls,
        user_text: str,
        model_response: str,
        importance: float = 5.0,
        intent: str = "",
    ) -> list[MemoryWritePlanItem]:
        items: list[MemoryWritePlanItem] = []
        cleaned = user_text.strip()
        lowered = cleaned.lower()

        # 1. Nothing check for trivial small talk
        if importance < 3.0 or len(cleaned) < 3 or lowered in ("привет", "ок", "спасибо", "да", "нет", "ага", "хорошо"):
            return [MemoryWritePlanItem(action="nothing", payload={}, reason="trivial_interaction")]

        # 2. Check for Entity mentions
        words = re.findall(r"\b[A-ZА-Я][a-zа-я]{2,}\b", cleaned)
        for w in words:
            if w not in {"Иван", "Я", "Мне", "Мы", "Они", "Это", "Все", "Для", "Привет", "Спасибо", "Как"}:
                items.append(
                    MemoryWritePlanItem(
                        action="save_entity",
                        payload={"name": w, "type": "concept", "importance": min(1.0, importance / 10.0)},
                        reason="capitalized_entity",
                    )
                )

        # 3. Check for Goal statement
        if any(kw in lowered for kw in ("хочу сделать", "цель:", "планирую", "нужно завершить", "собираюсь")):
            items.append(
                MemoryWritePlanItem(
                    action="save_goal",
                    payload={"title": cleaned[:60], "description": cleaned, "status": "active"},
                    reason="goal_expression",
                )
            )

        # 4. Check for Prediction statement
        if any(kw in lowered for kw in ("думаю, что", "наверное,", "скорее всего", "ожидаю")):
            items.append(
                MemoryWritePlanItem(
                    action="save_prediction",
                    payload={"hypothesis": cleaned[:100], "confidence": 0.7, "outcome": "pending"},
                    reason="prediction_expression",
                )
            )

        # 5. Check for notable Episode
        if any(kw in lowered for kw in ("сегодня", "вчера", "произошло", "случилось", "сходил", "встретился")):
            items.append(
                MemoryWritePlanItem(
                    action="save_episode",
                    payload={"title": cleaned[:40], "event": cleaned, "date": datetime.now().isoformat()[:10]},
                    reason="episodic_event",
                )
            )

        # 6. Default save_fact if importance >= 5.0 and not already captured as goal/episode
        if importance >= 5.0 and not any(i.action in ("save_goal", "save_episode") for i in items):
            items.append(
                MemoryWritePlanItem(
                    action="save_fact",
                    payload={"fact": cleaned, "importance": importance / 10.0},
                    reason="standard_important_fact",
                )
            )

        return items if items else [MemoryWritePlanItem(action="nothing", payload={}, reason="no_salience")]


class MemoryWriteExecutor:
    """Executes planned memory write items into SQLite and Cognitive Graph."""

    def __init__(self, db: Any, world_model: Any = None) -> None:
        self.db = db
        self.world_model = world_model

    @classmethod
    def execute_plan(
        cls,
        items: list[MemoryWritePlanItem],
        store: Any,
        db: Any,
        world_model: Any = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for item in items:
            if item.action == "nothing":
                continue
            elif item.action == "save_fact":
                from companion.models import Fact
                from companion.models import _new_id
                fid = _new_id("fact")
                f = Fact(
                    id=fid,
                    fact=item.payload.get("fact", ""),
                    importance=int(float(item.payload.get("importance", 6))),
                    date=datetime.now().isoformat()[:10],
                    confidence=0.8,
                    source="cognitive_loop",
                )
                store.add_fact(f)
                results.append({"action": "save_fact", "id": fid})
            elif item.action == "save_entity":
                if world_model:
                    matches = world_model.search(item.payload.get("name", ""))
                    if not matches:
                        from companion.models import _new_id
                        eid = _new_id("ent")
                        db.upsert_world_entity(
                            {
                                "entity_id": eid,
                                "name": item.payload.get("name", ""),
                                "type": item.payload.get("type", "concept"),
                                "importance": item.payload.get("importance", 0.5),
                            }
                        )
                        results.append({"action": "save_entity", "id": eid})
            elif item.action == "save_goal":
                from companion.models import _new_id
                gid = _new_id("goal")
                db.upsert_goal(
                    {
                        "goal_id": gid,
                        "title": item.payload.get("title", ""),
                        "description": item.payload.get("description", ""),
                        "status": item.payload.get("status", "active"),
                        "created_at": datetime.now().isoformat(),
                    }
                )
                results.append({"action": "save_goal", "id": gid})
            elif item.action == "save_prediction":
                from companion.models import _new_id
                pid = _new_id("pred")
                db.upsert_prediction(
                    {
                        "prediction_id": pid,
                        "hypothesis": item.payload.get("hypothesis", ""),
                        "confidence": item.payload.get("confidence", 0.7),
                        "outcome": item.payload.get("outcome", "pending"),
                        "created_at": datetime.now().isoformat(),
                    }
                )
                results.append({"action": "save_prediction", "id": pid})
            elif item.action == "save_episode":
                # Save into timeline events
                events = db.load_events()
                events.append(
                    {
                        "date": item.payload.get("date", datetime.now().isoformat()[:10]),
                        "event": item.payload.get("event", ""),
                    }
                )
                db.save_events(events[-50:])
                results.append({"action": "save_episode", "event": item.payload.get("event")})

        return results


# =====================================================================
# 9. IMPORTANCE FEEDBACK
# =====================================================================

class ImportanceFeedbackService:
    """Adjusts importance dynamically based on retrieval frequency and decay."""

    @classmethod
    def apply_retrieval_feedback(cls, context: MemoryContext, db: Any) -> int:
        count = 0

        # Boost importance of retrieved entities
        for ent in context.entities:
            try:
                new_imp = min(1.0, round(ent.importance + 0.02, 2))
                if new_imp != ent.importance:
                    ent.importance = new_imp
                    db.upsert_world_entity(ent.to_dict(), expected_version=ent.version)
                    count += 1
            except Exception:
                pass

        # Increment usage for retrieved facts
        for item in context.facts:
            if isinstance(item, Fact):
                try:
                    db.increment_fact_usage(item.id, used=True)
                    count += 1
                except Exception:
                    pass

        return count

    @classmethod
    def decay_neglected(cls, db: Any, older_than_days: int = 30) -> int:
        """Decay importance of entities unreferenced for > older_than_days."""
        count = 0
        cutoff = datetime.now() - timedelta(days=older_than_days)
        all_ents = db.list_world_entities(limit=500)

        for d in all_ents:
            if d.get("entity_id") == "ent_user":
                continue
            last_dt_str = str(d.get("last_mentioned_at") or d.get("created_at") or "")
            try:
                if last_dt_str:
                    dt = datetime.fromisoformat(last_dt_str)
                    if dt < cutoff:
                        old_imp = float(d.get("importance", 0.5))
                        new_imp = max(0.1, round(old_imp * 0.9, 2))
                        if new_imp != old_imp:
                            d["importance"] = new_imp
                            db.upsert_world_entity(d, expected_version=d.get("version"))
                            count += 1
            except (ValueError, TypeError):
                continue
        return count


# =====================================================================
# 10. PIPELINE TELEMETRY & COGNITIVE LOOP SERVICE
# =====================================================================

@dataclass
class TurnTelemetry:
    """Telemetry log for a single cognitive loop turn."""
    retrieved_entities: int = 0
    retrieved_facts: int = 0
    retrieved_episodes: int = 0
    retrieved_beliefs: int = 0
    planner_ms: float = 0.0
    retrieval_ms: float = 0.0
    compression_ms: float = 0.0
    fusion_ms: float = 0.0
    reasoning_ms: float = 0.0
    write_planner_ms: float = 0.0


class PipelineTelemetry:
    """Logs pipeline metrics and latencies after each turn."""
    _logs: list[TurnTelemetry] = []

    @classmethod
    def log_turn(cls, record: TurnTelemetry) -> None:
        cls._logs.append(record)
        logger.info(
            f"[COGNITIVE LOOP TELEMETRY] Entities={record.retrieved_entities}, "
            f"Facts={record.retrieved_facts}, Episodes={record.retrieved_episodes}, "
            f"Beliefs={record.retrieved_beliefs} | "
            f"Planner={record.planner_ms:.1f}ms, Retrieval={record.retrieval_ms:.1f}ms, "
            f"Compression={record.compression_ms:.1f}ms, Fusion={record.fusion_ms:.1f}ms"
        )

    @classmethod
    def get_recent_telemetry(cls, limit: int = 10) -> list[TurnTelemetry]:
        return cls._logs[-limit:]


class CognitiveLoopService:
    """Orchestrates all 10 processes of the Phase 2.3 Cognitive Loop."""

    def __init__(
        self, db: Any, store: Any = None, world_model: Any = None, vector: Any = None
    ) -> None:
        self.db = db
        self.store = store
        self.world_model = world_model
        self.vector = vector
        self.fusion_service = MemoryFusionService(db, world_model=world_model)

    def plan_retrieval(self, query: str, intent: str = "") -> RetrievalPlan:
        return RetrievalPlanner.plan_retrieval(query, intent=intent)

    def allocate_budget(
        self, plan: RetrievalPlan, total_tokens: int = 6000
    ) -> RetrievalBudget:
        return RetrievalBudgetAllocator.allocate(plan, total_tokens=total_tokens)

    def retrieve_and_fuse(
        self, query: str, intent: str = "", importance: float = 5.0
    ) -> MemoryContext:
        t0 = time.perf_counter()
        plan = self.plan_retrieval(query, intent=intent)
        planner_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        budget = self.allocate_budget(plan)

        # Fetch facts from store
        retrieved_facts = []
        if self.store and plan.fetch_facts:
            limit_f = max(5, int(5 + (importance * 3)))
            search_res = self.store.search_facts(query, limit=limit_f) if query else []
            retrieved_facts = [f for f, _ in search_res]
            if not retrieved_facts:
                retrieved_facts = self.store.list_facts("active")[:limit_f]

        # Fetch entities from world model
        retrieved_entities = []
        if self.world_model and plan.fetch_entities and query:
            retrieved_entities = self.world_model.search(query)

        # Fetch beliefs
        retrieved_beliefs = []
        if hasattr(self.db, "list_beliefs") and plan.fetch_beliefs:
            retrieved_beliefs = self.db.list_beliefs("active")[:5]

        # Fetch episodes (events)
        retrieved_episodes = []
        if plan.fetch_episodes:
            events = self.db.load_events()
            retrieved_episodes = events[-5:] if events else []

        retrieval_ms = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        ctx = self.fusion_service.fuse_context(
            query=query,
            plan=plan,
            budget=budget,
            retrieved_facts=retrieved_facts,
            retrieved_beliefs=retrieved_beliefs,
            retrieved_episodes=retrieved_episodes,
            retrieved_entities=retrieved_entities,
        )
        fusion_ms = (time.perf_counter() - t2) * 1000

        # Apply importance feedback
        ImportanceFeedbackService.apply_retrieval_feedback(ctx, self.db)

        # Log telemetry
        telemetry = TurnTelemetry(
            retrieved_entities=len(ctx.entities),
            retrieved_facts=len(ctx.facts),
            retrieved_episodes=len(ctx.episodes),
            retrieved_beliefs=len(ctx.beliefs),
            planner_ms=planner_ms,
            retrieval_ms=retrieval_ms,
            fusion_ms=fusion_ms,
        )
        PipelineTelemetry.log_turn(telemetry)

        return ctx

    def process_turn_feedback(
        self,
        query: str,
        response: str,
        importance: float = 5.0,
        intent: str = "",
    ) -> dict[str, Any]:
        """Post-response feedback loop: predictions, goals, write planning, and decay."""
        t0 = time.perf_counter()
        # Evaluate predictions
        pred_updates = PredictionFeedbackService.evaluate_predictions(
            query, response, self.db
        )

        # Update goals
        goal_updates = GoalFeedbackService.update_goals_from_interaction(
            query, response, self.db
        )

        # Write planner
        write_items = MemoryWritePlanner.plan_write(
            query, response, importance=importance, intent=intent
        )
        write_results = []
        if self.store:
            write_results = MemoryWriteExecutor.execute_plan(
                write_items, self.store, self.db, world_model=self.world_model
            )

        write_ms = (time.perf_counter() - t0) * 1000
        logger.debug(f"[COGNITIVE FEEDBACK] Complete in {write_ms:.1f}ms")

        return {
            "prediction_updates": pred_updates,
            "goal_updates": goal_updates,
            "write_results": write_results,
            "write_ms": write_ms,
        }
