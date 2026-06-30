"""Memory pipeline: compress → facts → consolidation → reflections → personality."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from companion.config import REFLECTION_EVERY_N
from companion.llm import client as llm
from companion.llm.prompts import (
    CONSOLIDATION_PROMPT,
    FACT_EXTRACTION_PROMPT,
    PERSONALITY_PIPELINE_PROMPT,
    REFLECTION_PROMPT,
    SUMMARY_PROMPT,
)
from companion.memory.store import MemoryStore
from companion.models import Fact, FactRelation, Reflection

logger = logging.getLogger(__name__)


def extract_facts(
    store: MemoryStore,
    summary: str,
    message_ids: list[str] | None = None,
) -> list[Fact]:
    known = store.get_active_fact_texts()[-40:]
    msgs = store.recent_messages(min_importance=5, limit=15)
    msg_text = "\n".join(f"- [{m.importance}/10] {m.text[:300]}" for m in msgs)

    prompt = FACT_EXTRACTION_PROMPT.format(
        known_facts="\n".join(f"- {f}" for f in known) or "нет",
        summary=summary,
        messages=msg_text or "нет",
    )
    try:
        raw = llm.parse_json_array(llm.oneshot(prompt))
        if not raw:
            logger.warning(
                f"Fact extraction returned empty array. "
                f"Prompt length: {len(prompt)}, Known facts: {len(known)}"
            )
    except Exception as e:
        logger.error(f"Fact extraction failed: {e}")
        return []

    created: list[Fact] = []
    source = message_ids[0] if message_ids else f"summary_{datetime.now().strftime('%Y%m%d')}"

    for item in raw:
        if not isinstance(item, dict) or not item.get("fact"):
            continue
        text = str(item["fact"]).strip()
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

        fact = Fact(
            fact=text,
            date=datetime.now().strftime("%Y-%m-%d"),
            importance=max(1, min(10, int(item.get("importance", 5)))),
            confidence=float(item.get("confidence", 0.75)),
            source=source,
            source_type="compress",
            memory_kind=item.get("memory_kind", "event"),
            tags=tags,
        )
        store.add_fact(fact)
        created.append(fact)
    return created


def consolidate_facts(store: MemoryStore, new_facts: list[Fact]) -> None:
    if not new_facts:
        return
    existing = store.list_facts("active")[-60:]
    prompt = CONSOLIDATION_PROMPT.format(
        new_facts=json.dumps(
            [{"index": i, "fact": f.fact, "id": f.id} for i, f in enumerate(new_facts)],
            ensure_ascii=False,
        ),
        existing_facts=json.dumps(
            [{"id": f.id, "fact": f.fact} for f in existing if f.id not in {n.id for n in new_facts}],
            ensure_ascii=False,
        ),
    )
    try:
        relations = llm.parse_json_array(llm.oneshot(prompt))
        if not relations:
            logger.warning(
                f"Consolidation returned empty array. "
                f"New facts: {len(new_facts)}, Existing facts: {len(existing)}"
            )
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        return

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        idx = rel.get("new_fact_index")
        existing_id = rel.get("existing_fact_id")
        relation = rel.get("relation", "related_to")
        if idx is None or not existing_id or idx >= len(new_facts):
            continue
        new_f = new_facts[int(idx)]
        store.add_relation(
            FactRelation(
                from_id=new_f.id,
                to_id=existing_id,
                relation=relation,
                reason=str(rel.get("reason", "")),
                confidence=float(rel.get("confidence", 0.8)),
            )
        )

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
    try:
        raw = llm.parse_json_array(llm.oneshot(prompt))
        if not raw:
            logger.warning(
                f"Reflection generation returned empty array. "
                f"Period: {period}, Facts available: {len(facts)}"
            )
    except Exception as e:
        logger.error(f"Reflection failed: {e}")
        return []

    created: list[Reflection] = []
    based_on = [f.id for f in facts[:10]]
    for item in raw:
        if not isinstance(item, dict) or not item.get("insight"):
            continue
        refl = Reflection(
            insight=str(item["insight"]).strip(),
            based_on=based_on,
            period=period,
            importance=max(1, min(10, int(item.get("importance", 7)))),
            confidence=float(item.get("confidence", 0.75)),
        )
        store.add_reflection(refl)
        created.append(refl)
    return created


def generate_personality_snapshot(store: MemoryStore, summary: str) -> dict[str, Any]:
    current = store.load_personality()
    facts = store.list_facts("active")
    top_facts = sorted(facts, key=lambda f: f.importance, reverse=True)[:40]
    reflections = store.list_reflections()[:10]
    beliefs = store.list_beliefs()[:15]

    # Не передаем current в промпт — избегаем feedback loop
    prompt = PERSONALITY_PIPELINE_PROMPT.format(
        current="{}",  # пустой объект
        facts=json.dumps([f.fact for f in top_facts], ensure_ascii=False),
        reflections=json.dumps([r.insight for r in reflections], ensure_ascii=False),
        beliefs=json.dumps([b["belief"] for b in beliefs], ensure_ascii=False),
        summary=summary[:2000],
    )
    try:
        updated = llm.parse_json_object(llm.oneshot(prompt))
        if not isinstance(updated, dict):
            raise ValueError("personality not a dict")

        # Merge с текущей personality вместо полной замены
        merged = _merge_personality(current, updated)
        merged["last_updated"] = datetime.now().isoformat()
        store.save_personality(merged)

        # Sync beliefs from personality
        for belief in merged.get("beliefs", [])[:20]:
            if isinstance(belief, str) and belief.strip():
                store.add_belief(belief.strip(), [f"personality_{merged['last_updated'][:10]}"])
        return merged
    except Exception as e:
        logger.error(f"Personality pipeline failed: {e}")
        return current


def _merge_personality(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge personality без feedback loop amplification."""
    merged = dict(old)

    # Interests: усреднить веса старых и новых
    old_interests = old.get("interests", {})
    new_interests = new.get("interests", {})
    merged_interests = {}
    for topic in set(old_interests.keys()) | set(new_interests.keys()):
        old_weight = old_interests.get(topic, 0)
        new_weight = new_interests.get(topic, 0)
        if old_weight and new_weight:
            merged_interests[topic] = (old_weight + new_weight) / 2
        else:
            merged_interests[topic] = old_weight or new_weight
    merged["interests"] = merged_interests

    # Lists: union без дубликатов
    for list_field in ["beliefs", "values", "fears", "motivation", "strengths", "weaknesses"]:
        old_list = old.get(list_field, [])
        new_list = new.get(list_field, [])
        # Deduplication: normalize strings for comparison (case-insensitive, strip whitespace)
        existing_normalized = {str(item).lower().strip() for item in old_list}
        for item in new_list:
            item_normalized = str(item).lower().strip()
            if item_normalized not in existing_normalized:
                old_list.append(item)
                existing_normalized.add(item_normalized)
        merged[list_field] = old_list

    # Relationships, habits, addictions: merge dicts
    for dict_field in ["relationships", "habits", "addictions"]:
        old_dict = old.get(dict_field, {})
        new_dict = new.get(dict_field, {})
        old_dict.update(new_dict)
        merged[dict_field] = old_dict

    # Changes: append новые изменения
    old_changes = old.get("changes", [])
    new_changes = new.get("changes", [])
    merged["changes"] = old_changes + new_changes

    return merged


def run_compress_pipeline(
    store: MemoryStore,
    chat: Any,
    user_id: int,
) -> str | None:
    """Full compress: summary → facts → consolidate → reflection? → personality."""
    try:
        response = chat.send_message(SUMMARY_PROMPT)
        summary = response.text or ""
        if not summary:
            return None

        from companion.storage.legacy import LegacyStorage  # noqa: PLC0415

        LegacyStorage.save_summary(summary)

        new_facts = extract_facts(store, summary)
        consolidate_facts(store, new_facts)

        compress_n = store.increment_compress_count()
        if compress_n % REFLECTION_EVERY_N == 0:
            generate_reflections(store, summary)

        generate_personality_snapshot(store, summary)
        store.apply_importance_decay()

        # Обновить knowledge_map на основе новых фактов
        _update_knowledge_map(new_facts)

        # БЛОК 3: AUTO-UPDATE MASTER SUMMARY
        from companion.llm.master_summary import update_master_summary
        update_master_summary(summary)

        logger.info(
            "Compress #%d: %d new facts, summary saved, master summary updated",
            compress_n,
            len(new_facts),
        )
        return summary
    except Exception as e:
        logger.error("Compress pipeline error: %s", e)
        return None


def _update_knowledge_map(new_facts: list[Fact]) -> None:
    """Автоматически обновить карту знаний на основе новых фактов."""
    try:
        from companion.self_model import self_model

        # Анализируем топики в новых фактах
        topics_counter = {}
        for fact in new_facts:
            # Простая эвристика: ключевые слова
            text_lower = fact.fact.lower()

            # Определяем топик
            if any(kw in text_lower for kw in ["qa", "тестирование", "баг", "автотест"]):
                topics_counter["QA и тестирование"] = topics_counter.get("QA и тестирование", 0) + 1
            if any(kw in text_lower for kw in ["python", "код", "программ", "разработк"]):
                topics_counter["Python разработка"] = topics_counter.get("Python разработка", 0) + 1
            if any(kw in text_lower for kw in ["тревог", "паник", "f41", "амитриптилин", "рисперидон"]):
                topics_counter["Тревожное расстройство и лечение"] = topics_counter.get("Тревожное расстройство и лечение", 0) + 1
            if any(kw in text_lower for kw in ["аня", "аню", "ани", "тульп"]):
                topics_counter["Отношения с Аней (тульпа)"] = topics_counter.get("Отношения с Аней (тульпа)", 0) + 1
            if any(kw in text_lower for kw in ["морзик", "пёс", "собак"]):
                topics_counter["Морзик (пёс как якорь)"] = topics_counter.get("Морзик (пёс как якорь)", 0) + 1
            if any(kw in text_lower for kw in ["музык", "песн", "группа", "альбом"]):
                topics_counter["Музыкальные предпочтения"] = topics_counter.get("Музыкальные предпочтения", 0) + 1

        # Обновляем knowledge_map
        for topic, count in topics_counter.items():
            # Если топик упоминается часто - считаем глубоким знанием
            if count >= 2:
                self_model.update_knowledge_map(topic, "deep_knowledge")
            elif count == 1:
                self_model.update_knowledge_map(topic, "surface_knowledge")

    except Exception as e:
        logger.error(f"Knowledge map update error: {e}")
