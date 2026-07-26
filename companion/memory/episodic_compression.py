"""Episodic Memory Compression (Phase 3).

Groups dormant facts by period/theme and compresses them into high-level summary facts,
linking original dormant facts via 'summarized_by' / 'summarizes' relations.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from companion.models import Fact, FactRelation

if TYPE_CHECKING:
    from companion.memory.store import MemoryStore

logger = logging.getLogger(__name__)

EPISODIC_COMPRESSION_PROMPT = """Ты — помощник по анализу долгосрочной памяти.
Ниже приведены устаревшие/спящие (dormant) факты о пользователе за определенный период или тему:
{facts}

Твоя задача — составить ЕДИНУЮ емкую сводку-эпизод (2-4 предложения), которая объединяет эти факты и сохраняет суть происходивших событий или состояния пользователя.
Не придумывай ничего лишнего. Пиши от третьего лица ("Иван ...")."""


class EpisodicMemoryCompressor:
    """Compresses dormant facts into episodic summary facts."""

    def __init__(self, store: "MemoryStore", batch_size: int = 10, min_facts_to_compress: int = 3) -> None:
        self.store = store
        self.batch_size = batch_size
        self.min_facts_to_compress = min_facts_to_compress

    def get_unsummarized_dormant_facts(self) -> list[Fact]:
        """Find dormant facts that don't yet have a 'summarized_by' relation."""
        dormant_facts = self.store.list_facts("dormant")
        if not dormant_facts:
            return []

        unsummarized: list[Fact] = []
        for f in dormant_facts:
            rels = self.store.db.get_fact_relations(f.id)
            if any(r.get("relation") == "summarized_by" and r.get("from_id") == f.id for r in rels):
                continue
            unsummarized.append(f)

        return unsummarized

    def group_facts(self, facts: list[Fact]) -> dict[str, list[Fact]]:
        """Group facts by YYYY-MM (or 'general' if date missing)."""
        groups: dict[str, list[Fact]] = defaultdict(list)
        for f in facts:
            period = (f.date or f.created_at or "")[:7]
            if len(period) < 7:
                period = "general"
            groups[period].append(f)
        return dict(groups)

    def _generate_summary_text(self, facts: list[Fact], period: str) -> str:
        """Generate summary text via LLM or deterministic fallback."""
        from companion.llm import client as llm
        fact_lines = "\n".join(f"- {f.fact}" for f in facts)
        prompt = EPISODIC_COMPRESSION_PROMPT.format(facts=fact_lines)

        try:
            res = llm.oneshot(prompt, temperature=0.3)
            text = (res or "").strip()
            if text and len(text) > 10:
                return text
        except Exception as e:
            logger.debug("LLM episodic summarization failed, using fallback: %s", e)

        titles = [f.fact.rstrip(".!?") for f in facts[:3]]
        return f"Эпизод ({period}): " + "; ".join(titles) + "."

    def compress_group(self, period: str, facts: list[Fact]) -> Fact | None:
        """Compress a group of dormant facts into a single summary fact."""
        if len(facts) < self.min_facts_to_compress:
            return None

        summary_text = self._generate_summary_text(facts, period)
        if not summary_text:
            return None

        summary_fact = Fact(
            fact=f"[Сводка за {period}] {summary_text}",
            date=period if period != "general" else datetime.now().strftime("%Y-%m"),
            importance=7,
            confidence=0.9,
            source="episodic_compression",
            source_type="system",
            memory_kind="summary",
            tags=["episodic_summary", period],
            status="active",
        )
        self.store.add_fact(summary_fact)

        for f in facts:
            rel1 = FactRelation(
                from_id=f.id,
                to_id=summary_fact.id,
                relation="summarized_by",
                reason=f"Episodic compression for {period}",
                confidence=0.9,
            )
            rel2 = FactRelation(
                from_id=summary_fact.id,
                to_id=f.id,
                relation="summarizes",
                reason=f"Episodic compression for {period}",
                confidence=0.9,
            )
            self.store.add_relation(rel1)
            self.store.add_relation(rel2)

        logger.info("Compressed %d dormant facts in period %s into summary fact %s", len(facts), period, summary_fact.id)
        return summary_fact

    def run_compression(self) -> list[Fact]:
        """Run episodic compression on all unsummarized dormant facts."""
        unsummarized = self.get_unsummarized_dormant_facts()
        if not unsummarized:
            return []

        groups = self.group_facts(unsummarized)
        created_summaries: list[Fact] = []

        for period, group_facts in groups.items():
            for i in range(0, len(group_facts), self.batch_size):
                chunk = group_facts[i : i + self.batch_size]
                if len(chunk) >= self.min_facts_to_compress:
                    summary_fact = self.compress_group(period, chunk)
                    if summary_fact:
                        created_summaries.append(summary_fact)

        return created_summaries
