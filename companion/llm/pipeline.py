"""Memory pipeline: compress → facts → consolidation → reflections → personality."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from companion.config import REFLECTION_EVERY_N, LCE_EVERY_N
from companion.llm import client as llm
from companion.llm.prompts import (
    CONSOLIDATION_PROMPT,
    FACT_EXTRACTION_PROMPT,
    PERSONALITY_PIPELINE_PROMPT,
    REFLECTION_PROMPT,
    PATTERN_PROMPT,
    COMM_PREF_PROMPT,
    HUMAN_MODEL_PROMPT,
    LIFE_TRANSITION_PROMPT,
    SUMMARY_PROMPT,
)
from companion.memory.store import MemoryStore
from companion.memory.text_sim import text_overlap
from companion.models import Fact, FactRelation, Reflection, Pattern, CommPref, HumanModel, HumanModelInsight, LifeTransition

logger = logging.getLogger(__name__)

from companion.security.sanitizer import _looks_like_injection


def extract_facts(
    store: MemoryStore,
    summary: str,
    message_ids: list[str] | None = None,
) -> list[Fact]:
    known = store.get_active_fact_texts()[:40]
    msgs = store.recent_messages(min_importance=3, limit=80)
    msg_text = "\n".join(f"- [{m.id}] [{m.role.upper()}] [{m.importance}/10] {m.text[:300]}" for m in msgs)

    prompt = FACT_EXTRACTION_PROMPT.format(
        known_facts="\n".join(f"- {f}" for f in known) or "нет",
        summary=summary,
        messages=msg_text or "нет",
    )
    try:
        result = llm.oneshot_structured(prompt, llm.FactExtractionResult)
        raw = [f.model_dump() for f in result.facts]
    except Exception as e:
        logger.error(f"Fact extraction failed: {e}")
        return []

    created: list[Fact] = []
    source = message_ids[0] if message_ids else f"summary_{datetime.now().strftime('%Y%m%d')}"

    for item in raw:
        if not isinstance(item, dict) or not item.get("fact"):
            continue
        text = str(item["fact"]).strip()
        from companion.security.sanitizer import sanitize_markup
        text = sanitize_markup(text).strip()
        if not text:
            continue
        if store.find_similar_fact(text):
            continue

        # БЛОК 4: IDENTITY ANCHORS 2.0
        # Автоматическое тегирование anchors и core_identity
        tags = [str(t) for t in item.get("tags", [])][:5]
        fact_lower = text.lower()

        # Определяем anchor facts (причины жить, важные обещания)
        if any(kw in fact_lower for kw in ["морзик", "пёс", "собак", "обещан", "не выпил", "якор", "причина жить"]):
            if "anchor" not in tags:
                tags.append("anchor")

        # Определяем core_identity (имя, работа, основные характеристики)
        if any(kw in fact_lower for kw in ["зовут", "работа", "qa", "тестировщик", "возраст", "город"]):
            if "core_identity" not in tags:
                tags.append("core_identity")

        evidence_list = [str(e) for e in item.get("evidence_messages", [])]
        status = "pending_review" if _looks_like_injection(text) else "active"

        # World Model: слой знания (user|world|system), по умолчанию — user.
        domain = str(item.get("domain", "user") or "user").lower()
        if domain not in ("user", "world", "system"):
            domain = "user"

        fact = Fact(
            fact=text,
            date=datetime.now().strftime("%Y-%m-%d"),
            importance=max(1, min(10, int(item.get("importance", 5)))),
            confidence=float(item.get("confidence", 0.75)),
            source=source,
            source_type="compress",
            memory_kind=item.get("memory_kind", "event"),
            tags=tags,
            evidence=evidence_list,
            status=status,
            domain=domain,
        )
        store.add_fact(fact)
        created.append(fact)
    return created


def consolidate_facts(store: MemoryStore, new_facts: list[Fact]) -> None:
    active_new_facts = [f for f in new_facts if f.status == "active"]
    if not active_new_facts:
        return
    existing = store.recent_facts(60)
    prompt = CONSOLIDATION_PROMPT.format(
        new_facts=json.dumps(
            [{"index": i, "fact": f.fact, "id": f.id} for i, f in enumerate(active_new_facts)],
            ensure_ascii=False,
        ),
        existing_facts=json.dumps(
            [{"id": f.id, "fact": f.fact} for f in existing if f.id not in {n.id for n in active_new_facts}],
            ensure_ascii=False,
        ),
    )
    try:
        result = llm.oneshot_structured(prompt, llm.ConsolidationResult)
        relations = [r.model_dump() for r in result.relations]
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        return

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        idx = rel.get("new_fact_index")
        existing_id = rel.get("existing_fact_id")
        relation = rel.get("relation", "related_to")
        if idx is None or not existing_id or idx >= len(active_new_facts):
            continue
        new_f = active_new_facts[int(idx)]
        store.add_relation(
            FactRelation(
                from_id=new_f.id,
                to_id=existing_id,
                relation=relation,
                reason=str(rel.get("reason", "")),
                confidence=float(rel.get("confidence", 0.8)),
            )
        )

def extract_causal_links(store: MemoryStore, new_facts: list[Fact], summary: str) -> None:
    active_new_facts = [f for f in new_facts if f.status == "active"]
    if not active_new_facts:
        return
    from companion.llm.prompts import CAUSAL_EXTRACTION_PROMPT
    from companion.reasoning import CausalLink, reasoning_engine
    
    msgs = store.recent_messages(min_importance=5, limit=15)
    msg_text = "\n".join(f"- [{m.id}] [{m.importance}/10] {m.text[:300]}" for m in msgs)
    
    prompt = CAUSAL_EXTRACTION_PROMPT.format(
        new_facts=json.dumps(
            [{"id": f.id, "fact": f.fact} for f in active_new_facts],
            ensure_ascii=False
        ),
        summary=summary,
        messages=msg_text or "нет"
    )
    
    try:
        result = llm.oneshot_structured(prompt, llm.CausalLinkExtractionResult)
        raw = [l.model_dump() for l in result.links]
    except Exception as e:
        logger.error(f"Causal link extraction failed: {e}")
        return
        
    for item in raw:
        if not isinstance(item, dict) or not item.get("cause") or not item.get("effect"):
            continue
        link = CausalLink(
            cause=str(item["cause"]).strip(),
            effect=str(item["effect"]).strip(),
            confidence=float(item.get("confidence", 0.75)),
            evidence=[str(e) for e in item.get("evidence", [])],
            mechanism=str(item.get("mechanism", ""))
        )
        reasoning_engine.add_causal_link(link)

def generate_reflections(
    store: MemoryStore, summary: str, period: str | None = None
) -> list[Reflection]:
    period = period or datetime.now().strftime("%Y-%m")
    facts = store.facts_for_period(period, min_importance=5)
    fact_text = "\n".join(f"- {f.fact}" for f in facts[:30])

    prompt = REFLECTION_PROMPT.format(
        period=period,
        facts=fact_text or "мало данных",
        summary=summary,
    )
    logger.info(f"[DIAG] generate_reflections started for period {period}. Facts count: {len(facts)}")
    try:
        result = llm.oneshot_structured(prompt, llm.ReflectionResult)
        raw = [r.model_dump() for r in result.reflections]
        logger.info(f"[DIAG] generate_reflections LLM result reflections count: {len(raw)}")
    except Exception as e:
        logger.error(f"Reflection failed: {e}")
        return []

    created: list[Reflection] = []
    based_on = [f.id for f in facts[:10]]
    for item in raw:
        if not isinstance(item, dict) or not item.get("insight"):
            continue
        # Phase 3.4: Deduplicate reflections using FAISS
        from companion.security.sanitizer import sanitize_markup, _looks_like_injection
        new_insight = str(item["insight"]).strip()
        new_insight = sanitize_markup(new_insight).strip()
        if not new_insight:
            continue
        results = store.vector.search(new_insight, top_k=1, content_type="reflection")
        is_duplicate = False
        
        if results and results[0]["score"] > 0.85:
            dup_content = results[0]["content"]
            existing_reflections = store.list_reflections()
            for existing in existing_reflections:
                if existing.insight == dup_content:
                    is_duplicate = True
                    logger.info("Skipping duplicate reflection (updating existing): %.50s...", new_insight)
                    existing.importance = min(10, existing.importance + 1)
                    existing.created_at = datetime.now().isoformat()
                    store.update_reflection(existing)
                    break

        if not is_duplicate:
            new_norm = store._normalize(new_insight)
            for existing in store.list_reflections():
                if text_overlap(new_norm, store._normalize(existing.insight)) > 0.72:
                    is_duplicate = True
                    existing.importance = min(10, existing.importance + 1)
                    existing.created_at = datetime.now().isoformat()
                    store.update_reflection(existing)
                    break
                    
        if is_duplicate:
            logger.info(f"[DIAG] Reflection duplicate discarded: {new_insight[:30]}...")
            continue
        status = "pending_review" if _looks_like_injection(new_insight) else "active"
        refl = Reflection(
            insight=new_insight,
            based_on=based_on,
            period=period,
            importance=max(1, min(10, int(item.get("importance", 7)))),
            confidence=float(item.get("confidence", 0.75)),
            status=status,
        )
        try:
            stored = store.add_reflection(refl)
            logger.info(f"[DIAG] add_reflection success. status: {status}, content: {new_insight[:30]}...")
            created.append(refl)
        except Exception as e:
            logger.error(f"[DIAG] add_reflection failed: {e}")
    
    logger.info(f"[DIAG] generate_reflections returning {len(created)} created reflections.")
    return created


def extract_patterns(
    store: MemoryStore, summary: str, period: str | None = None
) -> list[Pattern]:
    """Уровень 2: вывод паттернов поведения поверх фактов.

    Паттерн — это НЕ факт и НЕ обобщение-вывод (reflection), а инференция:
    как/почему пользователь действует (напр. 'курит, чтобы справляться со
    стрессом'). Хранится как отдельная сущность с FAISS-индексом.
    """
    period = period or datetime.now().strftime("%Y-%m")
    facts = store.facts_for_period(period, min_importance=5)
    fact_text = "\n".join(f"- [{f.id}] {f.fact}" for f in facts[:30])
    known_ids = {f.id for f in facts[:30]}

    prompt = PATTERN_PROMPT.format(
        period=period,
        facts=fact_text or "мало данных",
        summary=summary,
    )
    try:
        result = llm.oneshot_structured(prompt, llm.PatternExtractionResult)
        raw = [r.model_dump() for r in result.patterns]
    except Exception as e:
        logger.error(f"Pattern extraction failed: {e}")
        return []

    created: list[Pattern] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("pattern"):
            continue
        # Sanitize + injection guard (same policy as reflections/facts)
        from companion.security.sanitizer import sanitize_markup, _looks_like_injection
        pat_text = str(item["pattern"]).strip()
        pat_text = sanitize_markup(pat_text).strip()
        if not pat_text:
            continue
        if _looks_like_injection(pat_text):
            logger.warning("Pattern looks like injection, dropping: %.50s...", pat_text)
            continue
        # Cross-entity dedup: a pattern must not merely restate an existing
        # reflection or belief, or we triple-store the same insight and bloat
        # the context (pattern + reflection + belief saying one thing).
        if _pattern_redundant(store, pat_text):
            logger.info("Skipping pattern that mirrors existing reflection/belief: %.50s...", pat_text)
            continue
        pat = Pattern(
            pattern=pat_text,
            category=str(item.get("category", "behavior")).lower(),
            # Only ids the model was actually shown survive. A weak model
            # happily invents plausible-looking ids, and an unresolvable
            # reference is worse than none: it fakes provenance.
            evidence=[str(e) for e in item.get("evidence", []) if str(e) in known_ids],
            importance=max(1, min(10, int(item.get("importance", 6)))),
            confidence=float(item.get("confidence", 0.7)),
            status="active",
        )
        # add_pattern dedups + supersedes related patterns internally
        stored = store.add_pattern(pat)
        created.append(stored)
    return created


def extract_comm_prefs(
    store: MemoryStore, summary: str, messages: list[str] | None = None
) -> CommPref | None:
    """Уровень 4: извлечь/обновить предпочтения общения пользователя.

    Возвращает None при ошибке. Само сохранение (merge) делает store.upsert_comm_pref,
    чтобы пустые поля не затирали ранее накопленные предпочтения.
    """
    facts = store.facts_for_period(datetime.now().strftime("%Y-%m"), min_importance=4)
    fact_text = "\n".join(f"- {f.fact}" for f in facts[:40])
    msg_text = "\n".join(f"- {m}" for m in (messages or [])[:15])
    prompt = COMM_PREF_PROMPT.format(
        facts=fact_text or "мало данных",
        messages=msg_text or "нет",
        summary=summary or "нет",
    )
    try:
        result = llm.oneshot_structured(prompt, llm.CommPrefExtractionResult)
        item = result.comm_pref
        delta = CommPref(
            style=(item.style or "").strip(),
            formality=(item.formality or "").strip(),
            humor=(item.humor or "").strip(),
            language=(item.language or "").strip(),
            liked_topics=[str(t).strip() for t in (item.liked_topics or []) if str(t).strip()],
            avoided_topics=[str(t).strip() for t in (item.avoided_topics or []) if str(t).strip()],
        )
        # Не сохраняем пустышку — нет сигнала.
        if not any([delta.style, delta.formality, delta.humor, delta.language,
                    delta.liked_topics, delta.avoided_topics]):
            logger.info("CommPref: no signal in this window, skipping.")
            return None
        store.upsert_comm_pref(delta)
        return delta
    except Exception as e:
        logger.error(f"CommPref extraction failed: {e}")
        return None


def extract_human_model(
    store: MemoryStore, summary: str, messages: list[str] | None = None
) -> HumanModel | None:
    """Уровень 6: извлечь/обновить модель человека (выводы).

    Возвращает None при ошибке/отсутствии сигнала. Сохранение (union-merge)
    делает store.upsert_human_model — накопительное, выводы не теряются.
    """
    # Берём широкий срез фактов + саммери за долгий период — для тенденций
    # нужна длинная дистанция, а не только текущее окно.
    facts = store.facts_for_period(datetime.now().strftime("%Y-%m"), min_importance=3)
    fact_text = "\n".join(f"- {f.fact}" for f in facts[:60])
    msg_text = "\n".join(f"- {m}" for m in (messages or [])[:15])
    prompt = HUMAN_MODEL_PROMPT.format(
        facts=fact_text or "мало данных",
        messages=msg_text or "нет",
        summary=summary or "нет",
    )
    try:
        result = llm.oneshot_structured(prompt, llm.HumanModelExtractionResult)
        item = result.human_model
        def _clean(lst):
            return [str(t).strip() for t in (lst or []) if str(t).strip()]
        def _ins(ls, dim):
            return [HumanModelInsight(text=t, dimension=dim, confidence=0.7,
                                      created_at=datetime.now().isoformat(),
                                      last_supported_at=datetime.now().isoformat())
                    for t in _clean(ls)]
        delta = HumanModel(
            goals=_ins(item.goals, "goals"),
            fears=_ins(item.fears, "fears"),
            strengths=_ins(item.strengths, "strengths"),
            recurring_mistakes=_ins(item.recurring_mistakes, "recurring_mistakes"),
            long_term_trends=_ins(item.long_term_trends, "long_term_trends"),
        )
        if not any([delta.goals, delta.fears, delta.strengths,
                    delta.recurring_mistakes, delta.long_term_trends]):
            logger.info("HumanModel: no signal in this window, skipping.")
            return None
        store.upsert_human_model(delta)
        return delta
    except Exception as e:
        logger.error(f"HumanModel extraction failed: {e}")
        return None


def extract_life_transitions(
    store: MemoryStore, summary: str, messages: list[str] | None = None
) -> list[LifeTransition]:
    """Life Continuity Engine: найти устойчивые переходы состояния человека.

    Это НЕ факты и НЕ снимок (HumanModel) — это траектория: от состояния A
    к состоянию B. Дорогой отдельный запрос к LLM, поэтому вызывается
    редко (раз в LCE_EVERY_N compress), НЕ на каждом сжатии.

    Защита: confidence < 0.65 → статус pending_review (карантин, как у фактов),
    чтобы LLM не впаривал красивую историю на пустом месте. pending_review
    НЕ попадает в промпт до ручного подтверждения.
    """
    from companion.config import LCE_CONFIDENCE_THRESHOLD
    # Широкий срез: переходы видны только на длинной дистанции.
    facts = store.facts_for_period(datetime.now().strftime("%Y-%m"), min_importance=4)
    fact_text = "\n".join(f"- {f.fact}" for f in facts[:100])
    hm = store.get_human_model()
    from companion.models import HumanModel
    hm_text = "\n".join(
        f"[{dim}] " + "; ".join(i.text for i in getattr(hm, dim))
        for dim in ("goals", "fears", "strengths", "recurring_mistakes", "long_term_trends")
        if getattr(hm, dim)
    )
    pats = store.list_patterns("active")
    pat_text = "\n".join(f"- {p.pattern}" for p in pats[:15])
    prev = store.get_active_transitions()
    prev_text = "\n".join(f"- [{t.domain}] {t.from_state} → {t.to_state}" for t in prev[:15]) or "нет"
    summaries = "\n".join(s[:400] for s in store.load_recent_summaries(5))
    prompt = LIFE_TRANSITION_PROMPT.format(
        human_model=hm_text or "нет",
        patterns=pat_text or "нет",
        facts=fact_text or "мало данных",
        previous=prev_text,
        summaries=summaries or "нет",
    )
    logger.info(f"[DIAG] extract_life_transitions started. Facts count: {len(facts)}")
    try:
        result = llm.oneshot_structured(prompt, llm.LifeTransitionExtractionResult)
        created: list[LifeTransition] = []
        
        parsed_transitions = result.transitions or []
        logger.info(f"[DIAG] extract_life_transitions LLM parsed transitions: {len(parsed_transitions)}")
        
        for item in parsed_transitions:
            dom = str(getattr(item, "domain", "identity") or "identity").lower()
            fs = str(getattr(item, "from_state", "") or "").strip()
            ts = str(getattr(item, "to_state", "") or "").strip()
            if not (fs and ts):
                logger.info("[DIAG] Transition discarded: empty from_state or to_state")
                continue
            from companion.security.sanitizer import sanitize_markup
            conf = float(getattr(item, "confidence", 0.7) or 0.7)
            t = LifeTransition(
                domain=dom,
                from_state=sanitize_markup(fs).strip(),
                to_state=sanitize_markup(ts).strip(),
                explanation=sanitize_markup(str(getattr(item, "explanation", "") or "")).strip(),
                trigger_events=[str(e).strip() for e in (getattr(item, "trigger_events", []) or []) if str(e).strip()],
                confidence=max(0.0, min(1.0, conf)),
                importance=7,
                status="active",
            )
            # Защита от красивой выдумки: низкая уверенность → карантин.
            t = store.confirm_or_review_transition(t, LCE_CONFIDENCE_THRESHOLD)
            logger.info(f"[DIAG] confirm_or_review_transition gave status: {t.status}")
            
            # Deduplication
            import difflib
            prev = store.db.list_life_transitions()
            is_dup = False
            for existing in prev:
                existing_domain = str(existing.get("domain", ""))
                existing_from = str(existing.get("from_state", ""))
                existing_to = str(existing.get("to_state", ""))
                if existing_domain.lower() == t.domain.lower():
                    from_sim = difflib.SequenceMatcher(None, existing_from.lower(), t.from_state.lower()).ratio()
                    to_sim = difflib.SequenceMatcher(None, existing_to.lower(), t.to_state.lower()).ratio()
                    if from_sim > 0.6 and to_sim > 0.6:
                        logger.info(f"[DIAG] Skipping duplicate transition: {t.from_state} -> {t.to_state}")
                        is_dup = True
                        break
            if is_dup:
                continue
            
            try:
                stored = store.add_transition(t)
                logger.info(f"[DIAG] add_transition success. ID: {stored.id}")
                created.append(stored)
            except Exception as e:
                logger.error(f"[DIAG] add_transition failed: {e}")
                
        logger.info(f"[DIAG] extract_life_transitions returning {len(created)} transitions.")
        return created
    except Exception as e:
        logger.error(f"LifeTransition extraction failed: {e}")
        return []


def _pattern_redundant(store: MemoryStore, pat_text: str) -> bool:
    """True if the pattern text closely mirrors an existing reflection/belief."""
    q_norm = store._normalize(pat_text)
    
    # 1. FAISS Cosine Similarity check for reflections
    results = store.vector.search(pat_text, top_k=1, content_type="reflection")
    if results and results[0]["score"] >= 0.85:
        return True
        
    # 2. String matching fallback
    for refl in store.list_reflections("active"):
        if q_norm in store._normalize(refl.insight):
            return True
            
    for belief in store.db.list_beliefs("active"):
        b = (belief.get("belief") or "").strip()
        if b and q_norm in store._normalize(b):
            return True
    return False


def _personality_critical_section(store: MemoryStore, updated: dict[str, Any]) -> dict[str, Any]:
    """Sync critical section for personality update — runs in thread.

    Phase 1.3: Moved out of asyncio.Lock to avoid freezing event loop
    during blocking file I/O and embedding API calls.
    """
    fresh = store.load_personality()
    merged = _merge_personality(fresh, updated)
    merged["last_updated"] = datetime.now().isoformat()
    store.save_personality(merged)
    
    new_beliefs = []
    for belief in merged.get("beliefs", [])[:20]:
        if isinstance(belief, str) and belief.strip():
            store.add_belief(belief.strip(), [f"personality_{merged['last_updated'][:10]}"])
            new_beliefs.append(belief.strip())
            
    # Phase 4 hook: Sync "dead" beliefs
    active_beliefs = store.db.list_beliefs(status="active")
    new_beliefs_norm = {store._normalize(b) for b in new_beliefs}

    for b in active_beliefs:
        b_text = b.get("belief", "")
        if store._normalize(b_text) not in new_beliefs_norm:
            b_id = b.get("id")
            if b_id:
                with store.db._conn() as conn:
                    conn.execute("UPDATE beliefs SET status = 'inactive' WHERE id = ?", (b_id,))
                store.vector.delete_for_content(b_text)
                
    return merged


async def generate_personality_snapshot(store: MemoryStore, summary: str) -> dict[str, Any]:
    current = store.load_personality()
    facts = store.list_facts("active")
    top_facts = sorted(facts, key=lambda f: f.importance, reverse=True)[:40]
    reflections = store.list_reflections()[:10]
    beliefs = store.list_beliefs()[:15]

    prompt = PERSONALITY_PIPELINE_PROMPT.format(
        current=json.dumps(current, ensure_ascii=False),
        facts=json.dumps([f.fact for f in top_facts], ensure_ascii=False),
        reflections=json.dumps([r.insight for r in reflections], ensure_ascii=False),
        beliefs=json.dumps([b["belief"] for b in beliefs], ensure_ascii=False),
        summary=summary[:2000],
    )
    try:
        result = await llm.oneshot_structured_async(prompt, llm.PersonalityPipelineResult)
        updated = result.model_dump()

        # P0-8 fix: sync_lock (threading.Lock) inside to_thread, NOT asyncio.Lock
        # around it. asyncio.Lock cannot serialize different OS threads.
        def _locked_personality_update(store_ref, updated_data):
            with store_ref.sync_lock:
                return _personality_critical_section(store_ref, updated_data)
        merged = await asyncio.to_thread(_locked_personality_update, store, updated)
        return merged
    except Exception as e:
        logger.error(f"Personality pipeline failed: {e}")
        return current


def _merge_personality(old: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Merge personality delta-обновлений с глобальным старением (decay)."""
    merged = dict(old)

    # 1. Interests & Decay
    old_interests = dict(old.get("interests", {}))
    interests_delta = delta.get("interests_delta", {})
    if not isinstance(interests_delta, dict):
        interests_delta = {}

    new_interests = {}
    # Phase 4.2: Intelligent interest aging with conditional decay.
    for topic, val in old_interests.items():
        weight = float(val)
        if topic in interests_delta:
            weight += float(interests_delta[topic])
        else:
            # No mention in this window — gentle decay.
            # Deep interests (ever reached >= 7) have a floor of 4.0;
            # this preserves long-term passions while letting abandoned hobbies fade.
            floor = 4.0 if weight >= 7.0 else 1.0
            weight = max(floor, weight - 0.1)
        new_interests[topic] = max(1.0, min(10.0, weight))

    # Добавляем новые интересы из дельты.
    for topic, val in interests_delta.items():
        if topic not in old_interests:
            try:
                weight = max(1.0, min(10.0, float(val)))
            except (TypeError, ValueError):
                continue
            if weight >= 2.0:
                new_interests[topic] = weight

    merged["interests"] = new_interests

    # 2. Lists: Add / Remove
    for list_field in ["beliefs", "values", "fears", "motivation", "strengths", "weaknesses"]:
        old_list = list(old.get(list_field, []))
        
        add_items = delta.get(f"{list_field}_to_add", [])
        if not isinstance(add_items, list):
            add_items = []
        remove_items = delta.get(f"{list_field}_to_remove", [])
        if not isinstance(remove_items, list):
            remove_items = []

        normalized_remove = {str(x).lower().strip() for x in remove_items}
        old_list = [x for x in old_list if str(x).lower().strip() not in normalized_remove]
        
        existing_normalized = {str(x).lower().strip() for x in old_list}
        for item in add_items:
            item_str = str(item).strip()
            if item_str and item_str.lower() not in existing_normalized:
                old_list.append(item_str)
                existing_normalized.add(item_str.lower())
                
        merged[list_field] = old_list

    # 3. Dicts: Habits, Relationships, Addictions
    old_habits = dict(old.get("habits", {}))
    habits_delta = delta.get("habits_delta", {})
    if isinstance(habits_delta, dict):
        for habit, status in habits_delta.items():
            if status == "исчезла" or not status:
                old_habits.pop(habit, None)
            else:
                old_habits[habit] = status
    merged["habits"] = old_habits

    # Relationships
    old_relationships = dict(old.get("relationships", {}))
    relationships_delta = delta.get("relationships_delta", {})
    if isinstance(relationships_delta, dict):
        for name, desc in relationships_delta.items():
            if not desc:
                old_relationships.pop(name, None)
            else:
                old_relationships[name] = desc
    merged["relationships"] = old_relationships

    # Addictions
    old_addictions = dict(old.get("addictions", {}))
    addictions_delta = delta.get("addictions_delta", {})
    if isinstance(addictions_delta, dict):
        for name, desc in addictions_delta.items():
            if not desc:
                old_addictions.pop(name, None)
            else:
                old_addictions[name] = desc
    merged["addictions"] = old_addictions

    # Changes
    old_changes = list(old.get("changes", []))
    new_changes = delta.get("changes", [])
    if isinstance(new_changes, list):
        merged["changes"] = old_changes + new_changes

    return merged


async def run_compress_pipeline(
    store: MemoryStore,
    chat: Any,
    user_id: int,
) -> str | None:
    """Full compress: summary → facts → consolidate → reflection? → personality.

    Все этапы с блокирующим I/O (SQLite, файлы, LLM-вызовы) запускаются через
    to_thread, чтобы не блокировать event loop. Критическая секция personality
    защищена store.lock.
    """
    def _sync_stages() -> tuple[list[Fact], int, str]:
        """Синхронные этапы, не требующие лока, — в одном потоке."""
        # compress_n отсчитывается из счётчика в БД (ранее был несвязанной
        # переменной → NameError и падение всего пайплайна на каждом compress).
        compress_n = store.get_compress_count()
        new_facts = extract_facts(store, summary)
        # Phase 2.3: Auto-promote high-value facts to permanent
        for fact in new_facts:
            if fact.status == "active" and fact.importance >= 8 and fact.confidence >= 0.8:
                fact.memory_kind = "permanent"
                if "auto_promoted" not in fact.tags:
                    fact.tags.append("auto_promoted")
                if any(t in {"core_identity", "anchor", "pinned"} for t in fact.tags):
                    fact.tags.append("profile_fact")
                # Use the canonical edit path so DB metadata stays in sync with
                # the FAISS entry already created by add_fact (text unchanged).
                store.update_fact(fact.id, memory_kind="permanent", tags=fact.tags)
                count = int(store.db.get_meta("permanent_promotion_count", "0")) + 1
                store.db.set_meta("permanent_promotion_count", str(count))
        promoted_count = sum(1 for f in new_facts if "auto_promoted" in f.tags)
        if promoted_count:
            logger.info("Auto-promoted %d high-value facts to permanent", promoted_count)
        consolidate_facts(store, new_facts)
        extract_causal_links(store, new_facts, summary)
        # Уровень 2: patterns extracted on the same cadence as reflections.
        if compress_n % REFLECTION_EVERY_N == 0:
            extract_patterns(store, summary)
        # Уровень 4: предпочтения общения — всегда, на каждом compress
        # (предпочтения эволюционируют независимо от каждых-N окна).
        try:
            extract_comm_prefs(store, summary)
        except Exception as e:
            logger.error("CommPref auto-update failed: %s", e)
        # Уровень 6: модель человека (выводы) — всегда, на каждом compress.
        # Накопительная: долгосрочные тенденции растут между окнами.
        try:
            extract_human_model(store, summary)
        except Exception as e:
            logger.error("HumanModel auto-update failed: %s", e)
        # Life Continuity Engine (LCE): извлечение переходов состояния —
        # редко (раз в LCE_EVERY_N compress), это отдельный дорогой запрос.
        if compress_n % LCE_EVERY_N == 0:
            try:
                extract_life_transitions(store, summary)
            except Exception as e:
                logger.error("LCE extraction failed: %s", e)
        compress_n_final = store.increment_compress_count()
        return new_facts, compress_n_final, summary

    try:
        response = await llm.run_llm(chat.send_message, SUMMARY_PROMPT)
        summary = response.text or ""
        if not summary:
            return None

        await asyncio.to_thread(store.save_summary, summary)

        new_facts, compress_n, summary = await asyncio.to_thread(_sync_stages)

        if compress_n % REFLECTION_EVERY_N == 0:
            await asyncio.to_thread(generate_reflections, store, summary)

        await generate_personality_snapshot(store, summary)
        from companion.memory.consolidation import consolidate_if_due, decay_fact_confidence
        await asyncio.to_thread(consolidate_if_due, store, 7)
        await asyncio.to_thread(decay_fact_confidence, store)
        await asyncio.to_thread(store.apply_importance_decay)
        await asyncio.to_thread(store.compress_dormant_episodes)
        # Episodic Memory: извлечь эпизоды из недавних важных фактов
        try:
            from companion.memory.episodes import EpisodeEngine
            ep_engine = EpisodeEngine(store)
            await ep_engine.extract_recent()
        except Exception as e:
            logger.error("Episode extraction failed: %s", e)
        await asyncio.to_thread(store.analyze_retrieval_effectiveness)

        # Обновить knowledge_domains на основе новых фактов
        await _update_knowledge_domains_async(new_facts)

        # БЛОК 3: AUTO-UPDATE MASTER SUMMARY
        from companion.llm.master_summary import update_master_summary
        await asyncio.to_thread(update_master_summary, summary)

        logger.info(
            "Compress #%d: %d new facts, summary saved, master summary updated",
            compress_n,
            len(new_facts),
        )
        return summary
    except Exception as e:
        logger.error("Compress pipeline error: %s", e)
        return None


async def _update_knowledge_domains_async(new_facts: list[Fact]) -> None:
    """Автоматически извлечь домены знаний с помощью LLM."""
    if not new_facts:
        return
    try:
        from companion.self_model import self_model
        
        prompt = (
            "Extract 3–5 core knowledge domains of the user based on these facts.\n"
            "Facts:\n" + "\n".join([f.fact for f in new_facts])
        )
        
        result = await llm.oneshot_structured_async(prompt, llm.KnowledgeDomainsExtractionResult)
        
        domains_list = [{"domain": str(d.domain), "confidence": float(d.confidence)} for d in result.domains]
        self_model.data["knowledge_domains"] = domains_list
        self_model.save()
        logger.info("Knowledge domains updated: %s", domains_list)
        
    except Exception as e:
        logger.error(f"Knowledge domains update error: {e}")
