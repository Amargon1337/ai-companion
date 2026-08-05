"""Compact person-level consolidation built from existing memory entities."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOT_MODEL = "personality_snapshot_v2"


def _items(values: Any, limit: int = 8) -> list[str]:
    if isinstance(values, dict):
        return [f"{key}: {value}" for key, value in list(values.items())[:limit]]
    result = []
    for value in values or []:
        text = str(getattr(value, "text", value)).strip()
        status = str(getattr(value, "status", "active"))
        if text and status not in {"stale", "refuted", "archived"}:
            result.append(text)
    return result[:limit]


def build_snapshot(store: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    personality = store.load_personality()
    human = store.get_human_model()
    goals = store.db.list_goals("active")[:5]
    patterns = store.list_patterns("active")[:8]
    transitions = store.db.list_life_transitions("active")[:5]
    causal = store.db.list_causal_links(0.55)[:8]
    current = {
        "values": _items(personality.get("values")),
        "goals": [str(goal.get("title", "")) for goal in goals if goal.get("title")],
        "fears": _items(personality.get("fears")) or _items(getattr(human, "fears", [])),
        "conflicts": _items(personality.get("weaknesses")) + _items(getattr(human, "recurring_mistakes", [])),
        "important_people": _items(personality.get("relationships")),
        "emotional_background": {
            "baseline": personality.get("emotional_state", personality.get("baseline_state", "neutral")),
            "changes": _items(personality.get("changes"), 5),
        },
        "coping": _items(personality.get("habits")) + _items(personality.get("strengths")),
        "patterns": [str(pattern.pattern) for pattern in patterns],
        "transitions": [f"{item.get('from_state')} -> {item.get('to_state')}" for item in transitions],
        "causal_links": [
            f"{item.get('cause')} -> {item.get('effect')}"
            + (f" ({float(item.get('confidence', 0)):.0%})" if item.get("confidence") is not None else "")
            for item in causal
        ],
    }
    current["golden_memory"] = build_golden_memory(personality, patterns, causal)
    previous = previous or {}
    previous_data = previous.get("profile", {})
    changed = {
        key: {"added": [item for item in values if item not in previous_data.get(key, [])],
              "removed": [item for item in previous_data.get(key, []) if item not in values]}
        for key, values in current.items()
        if isinstance(values, list) and values != previous_data.get(key, [])
    }
    return {
        "version": 2,
        "generated_at": datetime.now().isoformat(),
        "profile": current,
        "changes": changed,
        "source": "memory_consolidation",
    }


def build_golden_memory(personality: dict[str, Any], patterns: list[Any], causal: list[dict[str, Any]]) -> list[str]:
    """Return only stable person-level meaning, never raw episodic facts."""
    result = []
    values = _items(personality.get("values"), 5)
    if values:
        result.append("Ключевые ценноcти: " + ", ".join(values))
    for pattern in patterns:
        category = str(getattr(pattern, "category", ""))
        confidence = float(getattr(pattern, "confidence", 0.0))
        evidence = getattr(pattern, "evidence", []) or []
        if category in {"coping", "trend", "behavior"} and confidence >= 0.75 and len(evidence) >= 2:
            result.append(str(pattern.pattern).strip())
    for link in causal:
        confidence = float(link.get("confidence", 0.0))
        observed = int(link.get("observed_count", 1))
        if confidence >= 0.70 and observed >= 2:
            mechanism = str(link.get("mechanism", "")).strip()
            text = f"{link.get('cause')} приводит к {link.get('effect')}"
            if mechanism:
                text += f": {mechanism}"
            result.append(text)
    return list(dict.fromkeys(item for item in result if item))[:10]


def snapshot_text(snapshot: dict[str, Any], max_chars: int = 12000) -> str:
    profile = snapshot.get("profile", {})
    labels = {
        "values": "Ценноcти", "goals": "Текущие цели", "fears": "Страхи",
        "conflicts": "Конфликты", "important_people": "Важные люди",
        "patterns": "Уcтойчивые паттерны", "transitions": "Изменения",
        "causal_links": "Причинные cвязи", "coping": "Споcобы cправлятьcя",
        "golden_memory": "Золотая память",
    }
    lines = ["[Personality Snapshot v2]"]
    for key, label in labels.items():
        values = profile.get(key, [])
        if values:
            lines.append(f"{label}:\n" + "\n".join(f"- {value}" for value in values[:8]))
    emotional = profile.get("emotional_background", {})
    if emotional:
        lines.append(f"Эмоциональный фон: {emotional.get('baseline', 'neutral')}")
    changes = snapshot.get("changes", {})
    if changes:
        lines.append("Изменения отноcительно прошлого snapshot:")
        for key, delta in list(changes.items())[:8]:
            if delta.get("added"):
                lines.append(f"+ {key}: {', '.join(delta['added'][:4])}")
            if delta.get("removed"):
                lines.append(f"- {key}: {', '.join(delta['removed'][:4])}")
    return "\n\n".join(lines)[:max_chars]


def promote_patterns_to_insights(store: Any) -> int:
    """Promote patterns that time has earned into HumanModel traits.

    This is the step where an observation becomes understanding. The rule is
    deliberately arithmetic, not model-driven: an LLM proposes the wording of
    a pattern, but only repetition spread across real time decides whether it
    describes the person. A pattern confirmed five times in one week is a
    phase; the same pattern confirmed across three months is a trait.

    Promotion is idempotent — `upsert_human_model` merges by normalised text,
    so re-promoting an existing insight bumps its evidence_count instead of
    duplicating it. That is exactly the desired behaviour: each nightly pass
    that still sees the pattern makes the trait a little better earned.
    """
    from companion.config import PROMOTION_MIN_OBSERVATIONS, PROMOTION_MIN_SPAN_DAYS
    from companion.memory.importance import days_since
    from companion.models import HumanModel, HumanModelInsight, compute_pattern_status

    # category -> HumanModel dimension. Patterns whose category has no
    # dimension (e.g. "relationship") stay patterns; not every observation
    # is a personality trait.
    dimension_of = {
        "coping": "strengths",
        "behavior": "long_term_trends",
        "mistake": "recurring_mistakes",
        "trend": "long_term_trends",
    }

    promoted: dict[str, list[HumanModelInsight]] = {}
    for pattern in store.list_patterns("active"):
        dimension = dimension_of.get(str(pattern.category).lower())
        if not dimension:
            continue
        # A pattern that stopped being confirmed must not become a trait.
        if compute_pattern_status(pattern) != "active":
            continue

        # touch_pattern() bumps version on every confirmation, so version-1
        # is the number of times this observation actually recurred.
        confirmations = max(0, int(getattr(pattern, "version", 1)) - 1) + 1
        if confirmations < PROMOTION_MIN_OBSERVATIONS:
            continue

        first_seen = getattr(pattern, "created_at", "")
        last_seen = getattr(pattern, "last_confirmed_at", "") or first_seen
        span_days = max(0.0, days_since(first_seen) - days_since(last_seen))
        if span_days < PROMOTION_MIN_SPAN_DAYS:
            continue

        promoted.setdefault(dimension, []).append(
            HumanModelInsight(
                text=pattern.pattern,
                dimension=dimension,
                # Confidence is DERIVED from the confirmation count, never
                # accumulated across runs — otherwise the nightly schedule,
                # not the observations, would decide how sure we are.
                # 0.6 floor / 0.05 step / 0.95 cap are hand-picked starting
                # values, not measured: they encode "3 confirmations is a
                # weak trait, 7 is a strong one". Calibrate against real
                # promotion data before trusting the absolute numbers.
                confidence=min(0.95, 0.6 + 0.05 * confirmations),
                evidence_count=confirmations,
                # Full chain: the pattern that was promoted plus the facts it
                # rested on. This is what makes "why do I believe this?"
                # answerable and the trait refutable.
                evidence=[pattern.id, *(getattr(pattern, "evidence", None) or [])],
            )
        )

    if not promoted:
        return 0

    store.upsert_human_model(HumanModel(**promoted))
    total = sum(len(v) for v in promoted.values())
    logger.info(
        "Promoted %d time-earned pattern(s) into HumanModel: %s",
        total,
        {k: len(v) for k, v in promoted.items()},
    )
    return total


def revalidate_insight_provenance(store: Any) -> dict[str, int]:
    """Recompute trait confidence against the current state of its sources.

    Without this, provenance is decoration. A trait promoted from facts that
    were later superseded or archived would keep its confidence forever —
    an old mistake becomes an immortal personality trait.

    CRITICAL: this must be a pure function of current state, never a mutation
    of the previous result. Scaling the already-scaled confidence would make
    the cron frequency — not the evidence — decide how sure we are: a single
    dead source would rot a trait to zero over a month of nightly passes.
    So the baseline is recomputed from evidence_count exactly as promotion
    derived it, then scaled by the surviving share.

    Rules, all arithmetic:
      * source still active            -> supports the trait
      * source superseded/archived     -> no longer supports it
      * every source invalidated       -> trait is refuted (kept, not deleted)
      * some sources invalidated       -> confidence scaled by surviving share
      * sources alive again            -> refutation lifted, confidence restored
      * no resolvable sources at all   -> left untouched (legacy insights
        predate provenance; absence of evidence is not evidence of absence)
    """
    from companion.models import HumanModel

    human = store.get_human_model()
    dims = ("goals", "fears", "strengths", "recurring_mistakes", "long_term_trends")
    stats = {"checked": 0, "weakened": 0, "refuted": 0}

    def _alive(source_id: str) -> bool | None:
        """True/False if resolvable, None if the id points at nothing."""
        if source_id.startswith("pat_"):
            pattern = store.get_pattern(source_id)
            if pattern is None:
                return None
            return str(pattern.status) not in ("superseded", "archived")
        row = store.db.get_fact(source_id)
        if row is None:
            return None
        return str(row.get("status")) in ("active", "pending_review")

    rebuilt = HumanModel(version=human.version + 1, updated_at=datetime.now().isoformat())
    for dim in dims:
        insights = list(getattr(human, dim))
        for insight in insights:
            sources = list(getattr(insight, "evidence", None) or [])
            if not sources:
                continue
            verdicts = [_alive(s) for s in sources]
            resolvable = [v for v in verdicts if v is not None]
            if not resolvable:
                continue

            stats["checked"] += 1
            surviving = sum(1 for v in resolvable if v)
            share = surviving / len(resolvable)
            # Baseline = what promotion would derive from the observation
            # count today. Recomputed, never carried over, so repeated runs
            # under unchanged conditions are a no-op.
            baseline = min(0.95, 0.6 + 0.05 * max(1, int(insight.evidence_count or 1)))
            if surviving == 0:
                # Never delete: the system says "no longer supported",
                # not "this never happened".
                if insight.status != "refuted":
                    stats["refuted"] += 1
                insight.status = "refuted"
                insight.confidence = min(baseline, 0.2)
            else:
                target = round(baseline * share, 4) if share < 1.0 else baseline
                target = max(0.1, min(0.95, target))
                # Sources came back to life: lift the refutation. Only the
                # evidence may resurrect a trait — never a scheduled rerun.
                if insight.status == "refuted":
                    insight.status = "active"
                    stats["restored"] = stats.get("restored", 0) + 1
                if abs(target - insight.confidence) > 1e-6:
                    if target < insight.confidence:
                        stats["weakened"] += 1
                    insight.confidence = target
        setattr(rebuilt, dim, insights)

    if stats["checked"]:
        store.db.upsert_human_model(rebuilt.to_dict())
        logger.info("Provenance revalidation: %s", stats)
    return stats


def explain_insight(store: Any, insight_text: str) -> dict[str, Any]:
    """Answer "why do I believe this about the person?" with actual sources.

    Returns the trait, how many times it was confirmed, and every source
    resolved to its current text and status — so a human can audit the chain
    rather than trust it.
    """
    human = store.get_human_model()
    target = None
    normalized = (insight_text or "").strip().lower()
    for insight in human.all_insights():
        if insight.text.strip().lower() == normalized:
            target = insight
            break
    if target is None:
        return {}

    sources: list[dict[str, Any]] = []
    for source_id in getattr(target, "evidence", None) or []:
        if source_id.startswith("pat_"):
            pattern = store.get_pattern(source_id)
            sources.append({
                "id": source_id,
                "kind": "pattern",
                "text": pattern.pattern if pattern else None,
                "status": pattern.status if pattern else "missing",
            })
        else:
            row = store.db.get_fact(source_id)
            sources.append({
                "id": source_id,
                "kind": "fact",
                "text": row.get("fact") if row else None,
                "status": row.get("status") if row else "missing",
            })
    return {
        "trait": target.text,
        "dimension": target.dimension,
        "confidence": target.confidence,
        "status": target.status,
        "confirmed_times": target.evidence_count,
        "first_seen": target.created_at,
        "last_supported_at": target.last_supported_at,
        "sources": sources,
    }


def consolidate(store: Any) -> dict[str, Any]:
    previous = store.db.get_state_model(SNAPSHOT_MODEL)
    snapshot = build_snapshot(store, previous)
    store.db.save_state_model(SNAPSHOT_MODEL, snapshot)
    golden = snapshot.get("profile", {}).get("golden_memory", [])
    if golden:
        store.identity.update_identity(
            "anchor_reason",
            "\n".join(f"- {item}" for item in golden),
            confidence=0.9,
            source="memory_consolidation",
            explicit_overwrite=True,
        )
    return snapshot


def consolidate_if_due(store: Any, interval_days: int = 7) -> dict[str, Any] | None:
    previous = store.db.get_state_model(SNAPSHOT_MODEL)
    try:
        generated = datetime.fromisoformat(str(previous.get("generated_at", "")))
    except (TypeError, ValueError):
        generated = datetime.min
    if datetime.now() - generated < timedelta(days=max(1, interval_days)):
        return None
    return consolidate(store)


def decay_fact_confidence(store: Any, *, half_life_days: int = 365, minimum: float = 0.2) -> int:
    """Decay stale non-permanent fact confidence once per calendar day."""
    today = datetime.now().date().isoformat()
    marker = store.db.get_state_model("memory_confidence_decay")
    if marker.get("date") == today:
        return 0
    changed = 0
    for fact in store.list_facts("active"):
        protected_tags = {"anchor", "pinned", "core_identity"}
        if fact.memory_kind == "permanent" or protected_tags & {tag.lower() for tag in fact.tags}:
            continue
        try:
            reference = datetime.fromisoformat(fact.updated_at or fact.created_at)
        except (TypeError, ValueError):
            continue
        age_days = max(0.0, (datetime.now() - reference).total_seconds() / 86400)
        if age_days < 30 or fact.confidence <= minimum:
            continue
        # Half-life must be computed from the ORIGINAL confidence, not from
        # the already-decayed value. Re-decaying the previous result compounds
        # every run: a 365-day half-life silently became ~20%/day, so a fact
        # could rot from 0.9 to 0.29 in ten passes. The baseline is stored
        # once in meta so the curve depends on age alone, not on how many
        # times maintenance happened to run.
        meta = dict(fact.meta or {})
        baseline = meta.get("confidence_baseline")
        if not isinstance(baseline, (int, float)) or not (0.0 < float(baseline) <= 1.0):
            baseline = fact.confidence
            meta["confidence_baseline"] = baseline
        new_confidence = max(minimum, float(baseline) * math.pow(0.5, age_days / half_life_days))
        if new_confidence < fact.confidence - 0.001:
            # Preserve the last content-confirmation timestamp. Otherwise this
            # maintenance update would make the fact look newly confirmed.
            store.db.update_fact_fields(
                fact.id,
                {
                    "confidence": new_confidence,
                    "version": fact.version + 1,
                    "updated_at": fact.updated_at,
                    "meta": meta,
                },
            )
            changed += 1
    store.db.save_state_model("memory_confidence_decay", {"date": today, "changed": changed})
    return changed


# ── R2 — Epistemic auditor (kernel K8 precursor) ───────────────────────────

def audit_provenance_cycles(store) -> list[list[str]]:
    """Detect circular derivation in the fact-relation graph.

    Cognitive function (K3): a provenance chain must be acyclic. A cycle means
    A justified B and B now justifies A — circular reasoning that survives
    compression would be undetectable by confidence scores alone. Returns the
    list of cycles found (each a list of fact ids); callers quarantine the
    members, not delete them (Iron Law #5).
    """
    edges: dict[str, set[str]] = {}
    for f in store.list_all_facts():
        for rel in store.db.get_fact_relations(f.id):
            if rel.get("relation") in ("supersedes", "supports", "summarizes") and rel.get("from_id"):
                edges.setdefault(rel["from_id"], set()).add(rel["to_id"])

    cycles: list[list[str]] = []
    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        on_stack.add(node)
        stack.append(node)
        for nbr in edges.get(node, ()):
            if nbr not in visited:
                dfs(nbr)
            elif nbr in on_stack:
                i = stack.index(nbr)
                cycles.append(stack[i:] + [nbr])
        stack.pop()
        on_stack.discard(node)

    for start in list(edges):
        if start not in visited:
            dfs(start)
    return cycles


def reconcile_genome_parity(store) -> dict[str, int]:
    """Backfill genome rows for facts created before R2.

    Cognitive function (K4): the 1:1 fact<->genome invariant is the substrate
    for survival scoring and compression lineage. Idempotent — safe to run
    nightly and on startup after migrations.
    """
    missing = store.db.facts_missing_genome(limit=1000)
    for fid in missing:
        row = store.db.get_fact(fid)
        if not row:
            continue
        store.db.upsert_memory_genome({
            "memory_id": fid,
            "origin": str(row.get("source") or "unknown"),
            "born_at": str(row.get("created_at") or ""),
        })
    return {"backfilled": len(missing)}

