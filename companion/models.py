"""Data models for memory architecture."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

MemoryKind = Literal["permanent", "state", "event"]
FactStatus = Literal["active", "superseded", "inactive", "archived"]
RelationType = Literal["supersedes", "contradicts", "confirms", "related_to"]
QueryIntent = Literal["memory", "world", "mixed"]


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

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("fact")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.valid_from:
            self.valid_from = self.date

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


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
    personality_snapshot: str = ""
    recent_messages: list[str] = field(default_factory=list)
    active_goals: list[str] = field(default_factory=list)
    causal_links: list[str] = field(default_factory=list)
    predictions: list[str] = field(default_factory=list)
    world_model_context: str = ""
    user_model_context: str = ""

    def to_prompt_block(self) -> str:
        parts: list[str] = []
        if self.personality_snapshot:
            parts.append(self.personality_snapshot)
        if self.user_model_context:
            parts.append(self.user_model_context)
        if self.permanent_notes:
            parts.append(f"[Постоянная память]\n{self.permanent_notes}")
        if self.recent_messages:
            parts.append("[Недавние реплики]\n" + "\n".join(self.recent_messages))
        if self.active_goals:
            parts.append("[Активные цели]\n" + "\n".join(self.active_goals))
        if self.causal_links:
            parts.append("[Причинно-следственный контекст]\n" + "\n".join(self.causal_links))
        if self.predictions:
            parts.append("[Прогнозы и ожидания]\n" + "\n".join(self.predictions))
        if self.world_model_context:
            parts.append(f"[Модель мира]\n{self.world_model_context}")
        if self.reflections:
            lines = [f"• {r.insight}" for r in self.reflections]
            parts.append("[Выводы о пользователе]\n" + "\n".join(lines))
        if self.facts:
            lines = [
                f"• [{f.memory_kind}|{f.importance}/10] {f.fact}"
                for f in self.facts
            ]
            parts.append("[Релевантные факты]\n" + "\n".join(lines))
        if self.summaries:
            parts.append("[Контекст саммари]\n" + "\n".join(self.summaries))
        return "\n\n".join(parts)
