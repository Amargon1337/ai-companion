"""Memory Explainability API — answers 'why do you believe this?'.

For any memory entity (fact, pattern, insight, transition), this module
reconstructs the full provenance chain:

    Fact X
      ├── Created: 2026-01-15
      ├── Epistemic type: DIRECT_FACT
      ├── Confidence: 0.85
      ├── Evidence:
      │     ├── Original message msg_abc123
      │     └── Confirmed by pattern pat_xyz
      ├── Mutation history:
      │     ├── 2026-01-15: created (confidence 0.75)
      │     ├── 2026-03-01: confirmed (+0.05 confidence)
      │     └── 2026-06-15: confirmed (+0.05 confidence)
      ├── Relations:
      │     ├── supersedes: fact_old (reason: user moved)
      │     └── confirmed_by: fact_supporting
      └── Current status: active

This is what makes the Memory OS trustworthy: every belief is auditable.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def explain_memory(store: Any, entity_id: str) -> dict[str, Any]:
    """Build a complete explainability report for a memory entity.

    Args:
        store: MemoryStore instance.
        entity_id: ID of the entity to explain (fact_, pat_, refl_, hm_, lce_).

    Returns:
        Dict with keys:
          - entity_type: fact | pattern | reflection | insight | transition
          - id: entity id
          - text: human-readable text of the entity
          - created_at: ISO timestamp
          - status: current lifecycle status
          - confidence: 0.0-1.0
          - epistemic_class: DIRECT_FACT | HYPOTHESIS | LLM_INFERENCE | PREDICTION
          - evidence: list of source descriptions
          - relations: list of related entities
          - mutations: list of historical changes
          - validation: last validated date, validation status
    """
    # Determine entity type from ID prefix
    prefix = entity_id.split("_")[0] if "_" in entity_id else ""

    explainers = {
        "fact": _explain_fact,
        "pat": _explain_pattern,
        "refl": _explain_reflection,
        "hm": _explain_insight,
        "lce": _explain_transition,
        "rel": _explain_relation,
        "ent": _explain_entity,
        "episode": _explain_episode,
    }

    explainer = explainers.get(prefix)
    if explainer is None:
        # Try each explainer to find the entity
        for expl in explainers.values():
            result = expl(store, entity_id)
            if result is not None:
                return result
        return {"error": f"Entity {entity_id} not found", "entity_id": entity_id}

    result = explainer(store, entity_id)
    if result is None:
        return {"error": f"Entity {entity_id} not found", "entity_id": entity_id}
    return result


def _explain_fact(store: Any, fact_id: str) -> dict[str, Any] | None:
    """Explain a fact — the most common entity type."""
    fact = store.get_fact(fact_id)
    if fact is None:
        return None

    # Build evidence chain
    evidence = []
    if fact.evidence:
        for ev_id in fact.evidence:
            ev_fact = store.get_fact(ev_id)
            if ev_fact:
                evidence.append({
                    "id": ev_id,
                    "type": "fact",
                    "text": ev_fact.fact[:200],
                    "status": ev_fact.status,
                })
            else:
                # Check if it's a message ID
                evidence.append({
                    "id": ev_id,
                    "type": "message_reference",
                    "text": f"Original message {ev_id}",
                })

    # Build relations
    relations = []
    for rel in store.get_fact_relations(fact_id):
        other_id = rel["to_id"] if rel["from_id"] == fact_id else rel["from_id"]
        other_fact = store.get_fact(other_id)
        relations.append({
            "relation": rel["relation"],
            "other_id": other_id,
            "other_text": other_fact.fact[:200] if other_fact else "(deleted)",
            "confidence": rel.get("confidence", 0.8),
            "reason": rel.get("reason", ""),
        })

    # Build mutation history
    mutations = _get_mutations(store, fact_id)

    # Compute freshness status
    from companion.memory.importance import days_since
    ref_date = fact.updated_at or fact.created_at
    age_days = days_since(ref_date) if ref_date else 0

    return {
        "entity_type": "fact",
        "id": fact.id,
        "text": fact.fact,
        "created_at": fact.created_at,
        "date": fact.date,
        "status": fact.status,
        "confidence": fact.confidence,
        "importance": fact.importance,
        "epistemic_class": getattr(fact, "epistemic_class", "DIRECT_FACT"),
        "memory_kind": fact.memory_kind,
        "domain": getattr(fact, "domain", "user"),
        "tags": fact.tags,
        "source": fact.source,
        "source_type": fact.source_type,
        "evidence": evidence,
        "relations": relations,
        "mutations": mutations,
        "support_count": getattr(fact, "support_count", 0),
        "contradiction_count": getattr(fact, "contradiction_count", 0),
        "retrieval_stats": {
            "sent_count": fact.facts_sent_count,
            "used_count": fact.facts_used_count,
            "precision": fact.precision,
        },
        "age_days": round(age_days, 1),
        "version": fact.version,
    }


def _explain_pattern(store: Any, pattern_id: str) -> dict[str, Any] | None:
    """Explain a behavioral pattern."""
    pattern = store.get_pattern(pattern_id)
    if pattern is None:
        return None

    evidence = []
    for ev_id in (getattr(pattern, "evidence", None) or []):
        ev_fact = store.get_fact(ev_id)
        if ev_fact:
            evidence.append({
                "id": ev_id,
                "type": "fact",
                "text": ev_fact.fact[:200],
                "status": ev_fact.status,
            })

    mutations = _get_mutations(store, pattern_id)

    from companion.models import compute_pattern_status
    computed_status = compute_pattern_status(pattern)

    return {
        "entity_type": "pattern",
        "id": pattern.id,
        "text": pattern.pattern,
        "category": pattern.category,
        "created_at": pattern.created_at,
        "last_confirmed_at": getattr(pattern, "last_confirmed_at", ""),
        "status": pattern.status,
        "computed_freshness": computed_status,
        "confidence": pattern.confidence,
        "importance": pattern.importance,
        "evidence": evidence,
        "mutations": mutations,
        "version": pattern.version,
    }


def _explain_reflection(store: Any, reflection_id: str) -> dict[str, Any] | None:
    """Explain a reflection (insight derived from facts)."""
    refl = None
    for r in store.list_reflections(status=None):
        if r.id == reflection_id:
            refl = r
            break
    if refl is None:
        return None

    evidence = []
    for ev_id in (refl.based_on or []):
        ev_fact = store.get_fact(ev_id)
        if ev_fact:
            evidence.append({
                "id": ev_id,
                "type": "fact",
                "text": ev_fact.fact[:200],
                "status": ev_fact.status,
            })

    return {
        "entity_type": "reflection",
        "id": refl.id,
        "text": refl.insight,
        "created_at": refl.created_at,
        "period": refl.period,
        "status": refl.status,
        "confidence": refl.confidence,
        "importance": refl.importance,
        "evidence": evidence,
        "version": refl.version,
    }


def _explain_insight(store: Any, insight_text: str) -> dict[str, Any] | None:
    """Explain a HumanModel insight by text match."""
    # This is called when entity_id starts with "hm_" but we match by text
    # For now, delegate to consolidation.explain_insight
    from companion.memory.consolidation import explain_insight
    try:
        result = explain_insight(store, insight_text)
        if result:
            result["entity_type"] = "insight"
            return result
    except Exception:
        pass
    return None


def _explain_transition(store: Any, transition_id: str) -> dict[str, Any] | None:
    """Explain a life transition."""
    transition = store.get_transition(transition_id)
    if transition is None:
        return None

    # Resolve trigger events
    triggers = []
    for ev_id in (getattr(transition, "trigger_events", None) or []):
        ev_fact = store.get_fact(ev_id)
        if ev_fact:
            triggers.append({
                "id": ev_id,
                "text": ev_fact.fact[:200],
                "status": ev_fact.status,
            })
        else:
            triggers.append({"id": ev_id, "text": ev_id})

    from companion.models import compute_transition_status
    computed_status = compute_transition_status(transition)

    return {
        "entity_type": "transition",
        "id": transition.id,
        "domain": transition.domain,
        "from_state": transition.from_state,
        "to_state": transition.to_state,
        "explanation": transition.explanation,
        "created_at": transition.created_at,
        "last_confirmed_at": getattr(transition, "last_confirmed_at", ""),
        "status": transition.status,
        "computed_freshness": computed_status,
        "confidence": transition.confidence,
        "trigger_events": triggers,
        "version": transition.version,
    }


def _explain_relation(store: Any, relation_id: str) -> dict[str, Any] | None:
    """Explain a fact relation."""
    for fact in store.list_all_facts():
        for rel in store.get_fact_relations(fact.id):
            if rel.get("id") == relation_id:
                from_fact = store.get_fact(rel["from_id"])
                to_fact = store.get_fact(rel["to_id"])
                return {
                    "entity_type": "fact_relation",
                    "id": relation_id,
                    "from_id": rel["from_id"],
                    "from_text": from_fact.fact[:200] if from_fact else "(deleted)",
                    "to_id": rel["to_id"],
                    "to_text": to_fact.fact[:200] if to_fact else "(deleted)",
                    "relation": rel["relation"],
                    "confidence": rel.get("confidence", 0.8),
                    "reason": rel.get("reason", ""),
                    "created_at": rel.get("created_at", ""),
                }
    return None


def _explain_entity(store: Any, entity_id: str) -> dict[str, Any] | None:
    """Explain a World Model entity."""
    raw = store.db.get_world_entity(entity_id)
    if raw is None:
        return None

    mentions = store.db.get_mentions_for_entity(entity_id)
    relations = store.db.list_entity_relations(entity_id=entity_id)
    attributes = store.db.get_entity_attributes(entity_id)

    return {
        "entity_type": "entity",
        "id": entity_id,
        "name": raw.get("name", ""),
        "type": raw.get("type", ""),
        "importance": raw.get("importance", 0.5),
        "created_at": raw.get("created_at", ""),
        "last_mentioned_at": raw.get("last_mentioned_at", ""),
        "aliases": raw.get("aliases", []),
        "summary": raw.get("summary", ""),
        "mention_count": len(mentions),
        "relation_count": len(relations),
        "attribute_count": len(attributes),
        "attributes": [{"key": a["attribute_key"], "value": a["attribute_value"]}
                       for a in attributes[:20]],
    }


def _explain_episode(store: Any, episode_id: str) -> dict[str, Any] | None:
    """Explain an episodic memory."""
    raw = store.db.get_episode(episode_id)
    if raw is None:
        return None

    # Resolve linked facts
    linked_facts = []
    for fid in (raw.get("fact_ids") or []):
        fact = store.get_fact(fid)
        if fact:
            linked_facts.append({
                "id": fid,
                "text": fact.fact[:200],
                "status": fact.status,
            })

    return {
        "entity_type": "episode",
        "id": episode_id,
        "title": raw.get("title", ""),
        "narrative": raw.get("narrative", ""),
        "date": raw.get("date", ""),
        "participants": raw.get("participants", []),
        "emotions": raw.get("emotions", {}),
        "lesson": raw.get("lesson", ""),
        "linked_facts": linked_facts,
        "created_at": raw.get("created_at", ""),
        "confidence": raw.get("confidence", 0.8),
        "importance": raw.get("importance", 7),
    }


def _get_mutations(store: Any, entity_id: str) -> list[dict[str, Any]]:
    """Get mutation history for an entity from the mutation log."""
    try:
        raw_mutations = store.db.list_mutations(entity_id=entity_id, limit=50)
        mutations = []
        for m in raw_mutations:
            mutations.append({
                "timestamp": m.get("timestamp", ""),
                "action": m.get("action", ""),
                "reason": m.get("reason", ""),
                "state_before": m.get("state_before", {}),
                "state_after": m.get("state_after", {}),
                "initiator": m.get("initiator", ""),
            })
        return mutations
    except Exception:
        return []
