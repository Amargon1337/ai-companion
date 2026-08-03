"""Tests for personality consolidation and confidence aging."""
from __future__ import annotations

from datetime import datetime, timedelta

from companion.memory.consolidation import (
    SNAPSHOT_MODEL,
    consolidate,
    decay_fact_confidence,
    explain_insight,
    promote_patterns_to_insights,
    revalidate_insight_provenance,
    snapshot_text,
)
from companion.models import Fact, Pattern


def _pattern(store, text, *, category="behavior", born_days_ago=0, confirmations=1):
    """Create a pattern with an explicit confirmation history.

    `version` is the confirmation counter (touch_pattern bumps it), the gap
    between created_at and last_confirmed_at is the time span.
    """
    now = datetime.now()
    pat = Pattern(pattern=text, category=category, confidence=0.8)
    store.db.add_pattern(pat.to_dict())
    store.db.update_pattern_fields(pat.id, {
        "version": confirmations,
        "last_confirmed_at": now.isoformat(),
    })
    with store.db._conn() as conn:
        conn.execute(
            "UPDATE patterns SET created_at=? WHERE id=?",
            ((now - timedelta(days=born_days_ago)).isoformat(), pat.id),
        )
    return pat


class TestPatternPromotion:
    """A trait must be earned by repetition ACROSS TIME, never by one day."""

    def test_one_intense_day_does_not_create_a_trait(self, memory_store):
        # Five confirmations, all today — a mood, not a trait.
        _pattern(memory_store, "много раз за один вечер", born_days_ago=0, confirmations=5)
        assert promote_patterns_to_insights(memory_store) == 0
        assert not any(memory_store.get_human_model().all_insights())

    def test_pattern_confirmed_over_months_becomes_a_trait(self, memory_store):
        _pattern(memory_store, "возвращается к музыке при перегрузе",
                 born_days_ago=90, confirmations=3)
        assert promote_patterns_to_insights(memory_store) == 1
        insights = memory_store.get_human_model().all_insights()
        assert len(insights) == 1
        assert insights[0].evidence_count >= 3

    def test_single_observation_never_promotes(self, memory_store):
        _pattern(memory_store, "сказано однажды давно", born_days_ago=200, confirmations=1)
        assert promote_patterns_to_insights(memory_store) == 0

    def test_promotion_is_idempotent(self, memory_store):
        """Rerunning promotion must NOT inflate how earned a trait looks.

        This test previously asserted the opposite (that each pass strengthens
        the trait) — that was wrong: it made the cron frequency, not the
        observations, decide confidence. A trait may only grow when the
        pattern is actually confirmed again.
        """
        pat = _pattern(memory_store, "предпочитает архитектуру быстрым решениям",
                       born_days_ago=120, confirmations=4)
        promote_patterns_to_insights(memory_store)
        first = memory_store.get_human_model().all_insights()[0]
        count_before, conf_before = first.evidence_count, first.confidence

        for _ in range(3):
            promote_patterns_to_insights(memory_store)

        insights = memory_store.get_human_model().all_insights()
        assert len(insights) == 1, "must not duplicate the trait"
        assert insights[0].evidence_count == count_before, (
            "no new observation happened, so nothing was earned"
        )
        assert insights[0].confidence == conf_before

        # A real new confirmation DOES strengthen it.
        memory_store.db.update_pattern_fields(pat.id, {"version": 6})
        promote_patterns_to_insights(memory_store)
        assert memory_store.get_human_model().all_insights()[0].evidence_count > count_before

    def test_stale_pattern_is_not_promoted(self, memory_store):
        # Confirmed often, but not for a long time -> aged out, not a trait.
        pat = _pattern(memory_store, "давно не подтверждался",
                       born_days_ago=500, confirmations=9)
        old = (datetime.now() - timedelta(days=400)).isoformat()
        memory_store.db.update_pattern_fields(pat.id, {"last_confirmed_at": old})
        assert promote_patterns_to_insights(memory_store) == 0


class TestProvenance:
    """A trait must know what it rests on, or it can never be refuted."""

    def _grounded_trait(self, store):
        """Build fact -> pattern -> trait with a real, resolvable chain."""
        f1 = Fact(fact="слушал металл когда было тяжело", date="2026-05-01",
                  importance=7, confidence=0.9, source="t")
        f2 = Fact(fact="написал трек после конфликта", date="2026-06-01",
                  importance=7, confidence=0.9, source="t")
        store.db._insert_fact(f1.to_dict())
        store.db._insert_fact(f2.to_dict())

        pat = _pattern(store, "использует музыку для регуляции состояния",
                       category="coping", born_days_ago=90, confirmations=4)
        store.db.update_pattern_fields(pat.id, {"evidence": [f1.id, f2.id]})
        promote_patterns_to_insights(store)
        return f1, f2, pat

    def test_promotion_records_full_chain(self, memory_store):
        f1, f2, pat = self._grounded_trait(memory_store)
        insight = memory_store.get_human_model().all_insights()[0]
        assert pat.id in insight.evidence
        assert f1.id in insight.evidence and f2.id in insight.evidence

    def test_explain_answers_why(self, memory_store):
        f1, _f2, _pat = self._grounded_trait(memory_store)
        insight = memory_store.get_human_model().all_insights()[0]

        explanation = explain_insight(memory_store, insight.text)
        assert explanation["confirmed_times"] >= 3
        kinds = {s["kind"] for s in explanation["sources"]}
        assert kinds == {"pattern", "fact"}
        assert any(s["text"] == f1.fact for s in explanation["sources"])

    def test_superseded_source_weakens_confidence(self, memory_store):
        f1, _f2, _pat = self._grounded_trait(memory_store)
        before = memory_store.get_human_model().all_insights()[0].confidence

        memory_store.db.update_fact_status(f1.id, "superseded")
        stats = revalidate_insight_provenance(memory_store)

        after = memory_store.get_human_model().all_insights()[0]
        assert stats["weakened"] == 1
        assert after.confidence < before
        assert after.status == "active", "partial loss must not refute outright"

    def test_all_sources_dead_refutes_but_keeps_the_trait(self, memory_store):
        f1, f2, pat = self._grounded_trait(memory_store)
        memory_store.db.update_fact_status(f1.id, "superseded")
        memory_store.db.update_fact_status(f2.id, "superseded")
        memory_store.db.update_pattern_fields(pat.id, {"status": "superseded"})

        stats = revalidate_insight_provenance(memory_store)
        insights = memory_store.get_human_model().all_insights()

        assert stats["refuted"] == 1
        assert len(insights) == 1, "refuted != deleted — history is preserved"
        assert insights[0].status == "refuted"
        assert insights[0].confidence <= 0.2

    def test_legacy_insight_without_evidence_is_untouched(self, memory_store):
        """Insights predating provenance must not be silently refuted."""
        from companion.models import HumanModel, HumanModelInsight

        memory_store.upsert_human_model(HumanModel(
            strengths=[HumanModelInsight(text="старый вывод без источников",
                                         dimension="strengths", confidence=0.8)]
        ))
        stats = revalidate_insight_provenance(memory_store)
        insight = memory_store.get_human_model().all_insights()[0]

        assert stats["checked"] == 0
        assert insight.status == "active"
        assert insight.confidence == 0.8

    def test_evidence_accumulates_across_confirmations(self, memory_store):
        from companion.models import HumanModel, HumanModelInsight

        memory_store.upsert_human_model(HumanModel(
            strengths=[HumanModelInsight(text="трейт", dimension="strengths",
                                         evidence=["fact_a"])]
        ))
        memory_store.upsert_human_model(HumanModel(
            strengths=[HumanModelInsight(text="трейт", dimension="strengths",
                                         evidence=["fact_b"])]
        ))
        insight = memory_store.get_human_model().all_insights()[0]
        assert insight.evidence == ["fact_a", "fact_b"]
        assert insight.evidence_count == 2


class TestMaintenanceIsPureFunctionOfState:
    """Periodic jobs must recompute from an immutable baseline, never mutate
    the previous result. Otherwise the cron schedule becomes the physics of
    memory: run twice as often and traits rot twice as fast."""

    def _grounded(self, store, sources=3):
        facts = [
            Fact(fact=f"источник {i}", date="2026-05-01", importance=7,
                 confidence=0.9, source="t")
            for i in range(sources)
        ]
        for f in facts:
            store.db._insert_fact(f.to_dict())
        pat = _pattern(store, "трейт с источниками", category="coping",
                       born_days_ago=90, confirmations=4)
        store.db.update_pattern_fields(pat.id, {"evidence": [f.id for f in facts]})
        promote_patterns_to_insights(store)
        return facts, pat

    def test_revalidation_is_stable_under_frozen_conditions(self, memory_store):
        """One dead source must cost a fixed amount, not compound nightly."""
        facts, _pat = self._grounded(memory_store)
        memory_store.db.update_fact_status(facts[0].id, "superseded")

        seen = []
        for _ in range(5):
            revalidate_insight_provenance(memory_store)
            seen.append(round(memory_store.get_human_model().all_insights()[0].confidence, 4))

        assert len(set(seen)) == 1, f"confidence drifted across runs: {seen}"

    def test_refuted_trait_is_not_resurrected_by_a_rerun(self, memory_store):
        facts, pat = self._grounded(memory_store, sources=1)
        for f in facts:
            memory_store.db.update_fact_status(f.id, "superseded")
        memory_store.db.update_pattern_fields(pat.id, {"status": "superseded"})
        revalidate_insight_provenance(memory_store)
        assert memory_store.get_human_model().all_insights()[0].status == "refuted"

        # A later nightly promotion pass must not undo the refutation.
        promote_patterns_to_insights(memory_store)
        after = memory_store.get_human_model().all_insights()[0]
        assert after.status == "refuted"
        assert after.confidence <= 0.2

    def test_revived_sources_lift_the_refutation(self, memory_store):
        """Only evidence may resurrect a trait — and it must be able to."""
        facts, pat = self._grounded(memory_store, sources=1)
        for f in facts:
            memory_store.db.update_fact_status(f.id, "superseded")
        memory_store.db.update_pattern_fields(pat.id, {"status": "superseded"})
        revalidate_insight_provenance(memory_store)

        for f in facts:
            memory_store.db.update_fact_status(f.id, "active")
        memory_store.db.update_pattern_fields(pat.id, {"status": "active"})
        revalidate_insight_provenance(memory_store)

        restored = memory_store.get_human_model().all_insights()[0]
        assert restored.status == "active"
        assert restored.confidence > 0.2

    def test_fact_decay_follows_half_life_not_run_count(self, memory_store):
        """Repeated maintenance passes must not compound the decay."""
        import math

        old = (datetime.now() - timedelta(days=60)).isoformat()
        fact = Fact(id="decay-target", fact="старый факт", date=old[:10],
                    importance=5, confidence=0.9, source="t",
                    created_at=old, updated_at=old)
        memory_store.db._insert_fact(fact.to_dict())
        expected = 0.9 * math.pow(0.5, 60 / 365)

        seen = []
        for _ in range(6):
            # Bypass the once-per-day guard to simulate many passes.
            memory_store.db.save_state_model("memory_confidence_decay", {"date": "forced"})
            decay_fact_confidence(memory_store, half_life_days=365)
            seen.append(round(memory_store.get_fact("decay-target").confidence, 4))

        assert len(set(seen)) == 1, f"decay compounded across runs: {seen}"
        assert abs(seen[0] - expected) < 0.002, f"{seen[0]} != half-life {expected:.4f}"


def test_snapshot_contains_person_profile_and_change_diff(memory_store):
    memory_store.save_personality({
        "values": ["cвобода"], "fears": ["выгорание"],
        "relationships": {"Морзик": "пёc"}, "interests": {"AI": 8},
        "changes": ["начал изучать архитектуру"],
    })
    snapshot = consolidate(memory_store)
    assert snapshot["version"] == 2
    assert "Морзик: пёc" in snapshot["profile"]["important_people"]
    assert "Personality Snapshot v2" in snapshot_text(snapshot)
    assert memory_store.db.get_state_model(SNAPSHOT_MODEL)["version"] == 2


def test_consolidation_promotes_only_supported_golden_memory(memory_store):
    memory_store.save_personality({"values": ["cвобода"], "relationships": {}, "changes": []})
    memory_store.add_pattern(Pattern(
        pattern="Музыка иcпользуетcя для cаморегуляции",
        category="coping",
        confidence=0.85,
        evidence=["fact-1", "fact-2"],
    ))
    memory_store.add_pattern(Pattern(
        pattern="Случайная гипотеза",
        category="coping",
        confidence=0.6,
        evidence=["fact-3"],
    ))

    snapshot = consolidate(memory_store)
    golden = snapshot["profile"]["golden_memory"]
    assert any("Музыка" in item for item in golden)
    assert all("Случайная" not in item for item in golden)
    vault = {item["category"]: item["value"] for item in memory_store.identity.get_all()}
    assert "Музыка" in vault["anchor_reason"]


def test_confidence_decay_skips_permanent_and_is_daily_idempotent(memory_store):
    old_date = (datetime.now() - timedelta(days=365)).isoformat()
    regular = Fact(
        id="decay-regular", fact="Старый интереc", date=old_date[:10], importance=5,
        confidence=0.9, source="test", created_at=old_date, updated_at=old_date,
    )
    permanent = Fact(
        id="decay-permanent", fact="Пcа зовут Морзик", date=old_date[:10], importance=9,
        confidence=0.9, source="test", memory_kind="permanent", created_at=old_date, updated_at=old_date,
    )
    memory_store.db._insert_fact(regular.to_dict())
    memory_store.db._insert_fact(permanent.to_dict())

    assert decay_fact_confidence(memory_store, half_life_days=365) == 1
    decayed = memory_store.get_fact("decay-regular")
    assert decayed.confidence < 0.9
    assert decayed.updated_at == old_date
    assert memory_store.get_fact("decay-permanent").confidence == 0.9
    assert decay_fact_confidence(memory_store, half_life_days=365) == 0
