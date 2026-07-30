"""Тесты для политик безопасности и прав субъектов Governance (Phase C2.1.5)."""

import pytest
from companion.memory.governance_policy import GovernancePolicy
from companion.models import IdentityLayer, MemoryActor


@pytest.fixture
def policy() -> GovernancePolicy:
    return GovernancePolicy()


def test_core_value_cannot_archive_by_system(policy: GovernancePolicy) -> None:
    """Защищённый факт не может быть архивирован или погашен непривилегированным субъектом."""
    assert policy.can_modify_status(
        "active", "archived", actor="SYSTEM", identity_layer="core_value"
    ) is False
    assert policy.can_modify_status(
        "active", "dormant", actor=MemoryActor.SYSTEM, identity_layer="core_belief"
    ) is False
    assert policy.can_modify_status(
        "active", "quarantined", actor=MemoryActor.LLM, identity_layer="core_identity"
    ) is False


def test_core_value_can_be_changed_by_user(policy: GovernancePolicy) -> None:
    """Привилегированный субъект (USER, ADMIN) имеет право менять статус защищённого факта."""
    assert policy.can_modify_status(
        "active", "archived", actor="USER", identity_layer="core_value"
    ) is True
    assert policy.can_modify_status(
        "active", "dormant", actor=MemoryActor.ADMIN, identity_layer="core_belief"
    ) is True


def test_llm_cannot_archive_or_delete(policy: GovernancePolicy) -> None:
    """LLM не имеет права переводить память в dormant, archived, quarantined, deleted."""
    assert policy.can_modify_status("active", "dormant", actor="LLM") is False
    assert policy.can_modify_status("active", "archived", actor=MemoryActor.LLM) is False
    assert policy.can_modify_status("active", "quarantined", actor="LLM") is False
    assert policy.can_modify_status("active", "deleted", actor=MemoryActor.LLM) is False


def test_delete_only_by_privileged(policy: GovernancePolicy) -> None:
    """Удаление ('deleted') разрешено только привилегированным субъектам."""
    assert policy.can_modify_status("active", "deleted", actor="SYSTEM") is False
    assert policy.can_modify_status("active", "deleted", actor="LLM") is False
    assert policy.can_modify_status("active", "deleted", actor="USER") is True
    assert policy.can_modify_status("active", "deleted", actor=MemoryActor.ADMIN) is True


def test_enum_compatibility(policy: GovernancePolicy) -> None:
    """Проверка работы с Enum IdentityLayer и MemoryActor."""
    assert policy.can_modify_status(
        "active", "dormant", actor=MemoryActor.SYSTEM, identity_layer=IdentityLayer.CORE_VALUE
    ) is False
    assert policy.can_modify_status(
        "active", "dormant", actor=MemoryActor.SYSTEM, identity_layer=IdentityLayer.BIOGRAPHICAL
    ) is True


def test_validate_modification_raises(policy: GovernancePolicy) -> None:
    """Метод validate_modification возбуждает ValueError при нарушении политики."""
    with pytest.raises(ValueError, match="Защищённый слой личности"):
        policy.validate_modification(
            "active", "dormant", actor="SYSTEM", identity_layer="core_value"
        )

    with pytest.raises(ValueError, match="Субъект LLM .* не имеет права"):
        policy.validate_modification("active", "archived", actor="LLM")
