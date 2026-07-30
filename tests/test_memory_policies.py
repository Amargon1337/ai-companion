"""Unit tests for memory policies in companion/memory/policies/."""
from companion.memory.governor import (
    ArchiveRecommendation,
    BoostRecommendation,
    DecayRecommendation,
    MergeRecommendation,
)
from companion.memory.policies import (
    ArchivePolicy,
    BoostPolicy,
    DecayPolicy,
    ImmunityPolicy,
    MergePolicy,
)
from companion.models import Fact


def test_immunity_policy() -> None:
    policy = ImmunityPolicy()

    # Normal fact is not immune
    normal = Fact(
        id="1",
        fact="Люблю чай",
        date="2026-07-28",
        importance=5,
        confidence=0.8,
        source="test",
    )
    dec = policy.evaluate(None, normal)
    assert dec.approved is True
    assert dec.action == "PASS_IMMUNITY"

    # Core identity fact is immune
    immune = Fact(
        id="2",
        fact="Я родился в Москве",
        date="2026-07-28",
        importance=5,
        confidence=0.9,
        source="test",
        meta={"category": "core_identity"},
    )
    dec_imm = policy.evaluate(None, immune)
    assert dec_imm.approved is False
    assert dec_imm.action == "REJECT_IMMUNE"


def test_archive_policy() -> None:
    policy = ArchivePolicy()
    rec = ArchiveRecommendation(fact_id="f1", reason="old", source="test")

    normal = Fact(
        id="f1",
        fact="Старая погода",
        date="2026-07-28",
        importance=4,
        confidence=0.8,
        source="test",
    )
    dec = policy.evaluate(rec, normal)
    assert dec.approved is True
    assert dec.action == "ARCHIVE"
    assert dec.updates == {"status": "archived", "archived": 1}

    # Immune fact cannot be archived
    immune = Fact(
        id="f2",
        fact="Семья в Москве",
        date="2026-07-28",
        importance=5,
        confidence=0.9,
        source="test",
        meta={"category": "family"},
    )
    dec2 = policy.evaluate(rec, immune)
    assert dec2.approved is False


def test_boost_and_decay_policies_alter_bias_not_importance() -> None:
    boost = BoostPolicy()
    decay = DecayPolicy()

    normal = Fact(
        id="f1",
        fact="Факт",
        date="2026-07-28",
        importance=5,
        confidence=0.8,
        source="test",
    )
    rec_boost = BoostRecommendation(fact_id="f1", amount=2, reason="frequent", source="test")
    dec_b = boost.evaluate(rec_boost, normal)
    assert dec_b.approved is True
    assert dec_b.action == "BOOST_BIAS"
    assert dec_b.updates["meta"]["retrieval_bias"] == 0.2
    # Ensure importance is NOT in updates
    assert "importance" not in dec_b.updates

    rec_decay = DecayRecommendation(fact_id="f1", amount=1, reason="unused", source="test")
    dec_d = decay.evaluate(rec_decay, normal)
    assert dec_d.approved is True
    assert dec_d.action == "DECAY_BIAS"
    assert dec_d.updates["meta"]["retrieval_bias"] == -0.1
    assert "importance" not in dec_d.updates


def test_merge_policy() -> None:
    policy = MergePolicy()
    f1 = Fact(
        id="f1",
        fact="Дубликат 1",
        date="2026-07-28",
        importance=5,
        confidence=0.8,
        source="test",
    )
    f2 = Fact(
        id="f2",
        fact="Дубликат 2",
        date="2026-07-28",
        importance=5,
        confidence=0.9,
        source="test",
    )

    rec = MergeRecommendation(
        fact_id="f1",
        target_fact_id="f2",
        reason="dup",
        source="test",
    )
    dec = policy.evaluate(rec, f1, target_fact=f2)
    assert dec.approved is True
    assert dec.action == "MERGE"
    assert dec.updates == {"status": "superseded", "superseded_by": "f2"}
