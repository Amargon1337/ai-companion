"""Data models for memory architecture."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

MemoryKind = Literal["permanent", "state", "event"]
FactStatus = Literal["active", "superseded", "inactive", "archived", "dormant", "pending_review"]
RelationType = Literal["supersedes", "contradicts", "confirms", "related_to"]
QueryIntent = Literal["memory", "world", "mixed"]


class MemoryOrigin(Enum):
    """Происхождение факта памяти."""
    USER_STATEMENT = "user_statement"           # Прямое утверждение пользователя
    LLM_EXTRACTION = "llm_extraction"           # Извлечено моделью из контекста
    LLM_INFERENCE = "llm_inference"             # Логический вывод модели
    CONSOLIDATION = "consolidation"             # Результат консолидации (сон)
    SYSTEM_MIGRATION = "system_migration"       # Миграция из legacy
    EXTERNAL_SOURCE = "external_source"         # Внешний источник (файл, ссылка)


class IdentityLayer(Enum):
    """Слой идентичности для классификации важности факта."""
    CORE_VALUE = "core_value"                   # Неизменные ценности
    BIOGRAPHICAL = "biographical"               # Факты биографии
    STABLE_HABIT = "stable_habit"               # Долгосрочные привычки
    PREFERENCE = "preference"                   # Предпочтения (еда, музыка)
    TEMPORARY_STATE = "temporary_state"         # Временное состояние (настроение)
    HYPOTHESIS = "hypothesis"                   # Недоказанное предположение
    LEGACY_UNKNOWN = "legacy_unknown"           # Для старых фактов без классификации


@dataclass
class MemoryConfidence:
    """Составная уверенность факта.
    
    Атрибуты:
        observed: Надёжность источника (1.0 для USER_STATEMENT, 0.7 для LLM_INFERENCE)
        inferred: Уверенность экстракции/вывода (score от LLM)
        stability: Временная стабильность (decay со временем)
        verification: Уровень подтверждения (растёт при повторных упоминаниях)
    """
    observed: float = 1.0
    inferred: float = 0.8
    stability: float = 1.0
    verification: float = 1.0
    
    @property
    def total(self) -> float:
        """Общая уверенность как взвешенное произведение."""
        return self.observed * self.inferred * self.stability * self.verification


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


@dataclass
class Fact:
    fact: str
    date: str
    importance: int
    confidence: float
    source: str
    memory_kind: MemoryKind = "event"
    source_type: str = "message"
    tags: list[str] = field(default_factory=list)
    status: FactStatus = "active"
    valid_from: str | None = None
    id: str = ""
    created_at: str = ""
    evidence: list[str] = field(default_factory=list)
    facts_sent_count: int = 0
    facts_used_count: int = 0
    
    # Phase C1: Provenance
    origin: MemoryOrigin = MemoryOrigin.LLM_EXTRACTION
    source_message_id: int | None = None
    identity_layer: IdentityLayer = IdentityLayer.LEGACY_UNKNOWN
    
    # Decomposed confidence (Phase C1)
    conf_observed: float = 1.0
    conf_inferred: float = 0.8
    conf_stability: float = 1.0
    conf_verification: float = 1.0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("fact")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.valid_from:
            self.valid_from = self.date

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # Convert enums to string values for JSON serialization
        if isinstance(self.origin, MemoryOrigin):
            result["origin"] = self.origin.value
        if isinstance(self.identity_layer, IdentityLayer):
            result["identity_layer"] = self.identity_layer.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        
        # Convert string values back to enums
        if "origin" in filtered and isinstance(filtered["origin"], str):
            try:
                filtered["origin"] = MemoryOrigin(filtered["origin"])
            except ValueError:
                filtered["origin"] = MemoryOrigin.LLM_EXTRACTION
        
        if "identity_layer" in filtered and isinstance(filtered["identity_layer"], str):
            try:
                filtered["identity_layer"] = IdentityLayer(filtered["identity_layer"])
            except ValueError:
                filtered["identity_layer"] = IdentityLayer.LEGACY_UNKNOWN
        
        return cls(**filtered)


@dataclass
class FactRelation:
    from_id: str
    to_id: str
    relation: RelationType
    reason: str = ""
    confidence: float = 0.8
    id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("rel")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactRelation:
        known = {f.name for f in FactRelation.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class MessageRecord:
    role: str
    text: str
    importance: int
    mode: str = "default"
    signals: list[str] = field(default_factory=list)
    id: str = ""
    ts: str = ""
    user_id: int | None = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("msg")
        if not self.ts:
            self.ts = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Reflection:
    insight: str
    based_on: list[str]
    period: str
    importance: int
    confidence: float
    status: FactStatus = "active"
    id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("refl")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reflection:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ContextBundle:
    """Selected context for a single LLM request."""
    facts: list[Fact] = field(default_factory=list)
    reflections: list[Reflection] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    permanent_notes: str = ""
    identity_vault_block: str = ""
    personality_snapshot: str = ""
    recent_messages: list[str] = field(default_factory=list)
    active_goals: list[str] = field(default_factory=list)
    causal_links: list[str] = field(default_factory=list)
    predictions: list[str] = field(default_factory=list)
    world_model_context: str = ""
    user_model_context: str = ""
    unified_profile_block: str = ""

    def to_prompt_block(self) -> str:
        from companion.security.sanitizer import sanitize_markup

        parts: list[str] = []
        if self.identity_vault_block:
            sanitized_id = sanitize_markup(self.identity_vault_block) or ""
            parts.append(f"<system_identity>\n{sanitized_id}\n</system_identity>")
        
        user_profile_parts = []
        if self.personality_snapshot:
            user_profile_parts.append(sanitize_markup(self.personality_snapshot) or "")
        if self.user_model_context:
            user_profile_parts.append(sanitize_markup(self.user_model_context) or "")
        if self.unified_profile_block:
            user_profile_parts.append(sanitize_markup(self.unified_profile_block) or "")
        if user_profile_parts:
            parts.append("<user_profile>\n" + "\n\n".join(user_profile_parts) + "\n</user_profile>")
            
        memory_parts = []
        if self.permanent_notes:
            sanitized_notes = sanitize_markup(self.permanent_notes) or ""
            memory_parts.append(f"[Постоянная память]\n{sanitized_notes}")
        if self.recent_messages:
            sanitized_msgs = [sanitize_markup(m) or "" for m in self.recent_messages]
            memory_parts.append("[Недавние реплики]\n" + "\n".join(sanitized_msgs))
        if self.active_goals:
            sanitized_goals = [sanitize_markup(g) or "" for g in self.active_goals]
            memory_parts.append("[Активные цели]\n" + "\n".join(sanitized_goals))
        if self.causal_links:
            sanitized_links = [sanitize_markup(c) or "" for c in self.causal_links]
            memory_parts.append("[Причинно-следственный контекст]\n" + "\n".join(sanitized_links))
        if self.predictions:
            sanitized_pred = [sanitize_markup(p) or "" for p in self.predictions]
            memory_parts.append("[Прогнозы и ожидания]\n" + "\n".join(sanitized_pred))
        if self.world_model_context:
            sanitized_wm = sanitize_markup(self.world_model_context) or ""
            memory_parts.append(f"[Модель мира]\n{sanitized_wm}")
        if self.reflections:
            lines = [f"• {sanitize_markup(r.insight) or ''}" for r in self.reflections]
            memory_parts.append("[Выводы о пользователе]\n" + "\n".join(lines))
        if self.facts:
            lines = [
                f"• [{f.memory_kind}|{f.importance}/10] {sanitize_markup(f.fact) or ''}"
                for f in self.facts
            ]
            memory_parts.append("[Релевантные факты]\n" + "\n".join(lines))
        if self.summaries:
            sanitized_sum = [sanitize_markup(s) or "" for s in self.summaries]
            memory_parts.append("[Контекст саммари]\n" + "\n".join(sanitized_sum))
            
        if memory_parts:
            parts.append("<conversational_memory>\n" + "\n\n".join(memory_parts) + "\n</conversational_memory>")
            
        return "\n\n".join(parts)
