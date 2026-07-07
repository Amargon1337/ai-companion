"""Data models for memory architecture."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

MemoryKind = Literal["permanent", "state", "event"]
FactStatus = Literal["active", "superseded", "inactive", "archived", "dormant", "pending_review"]
RelationType = Literal["supersedes", "contradicts", "confirms", "related_to"]


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
    updated_at: str = ""
    evidence: list[str] = field(default_factory=list)
    facts_sent_count: int = 0
    facts_used_count: int = 0
    version: int = 1
    superseded_by: str = ""

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
    version: int = 1
    superseded_by: str = ""

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
class Pattern:
    """An inference over facts — e.g. 'smokes to cope with stress'.

    Distinct from a Fact (raw observation) and a Reflection (a generalization).
    A Pattern is a behavioral/relational generalization the bot can reason about.
    """

    pattern: str
    category: str  # behavior | coping | mistake | relationship | trend
    evidence: list[str] = field(default_factory=list)
    importance: int = 6
    confidence: float = 0.7
    status: FactStatus = "active"
    id: str = ""
    created_at: str = ""
    version: int = 1
    superseded_by: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("pat")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pattern:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class CommPref:
    """Уровень 4: предпочтения общения — единая всегда-активная запись.

    Хранится как ОДНА строка (key="global"), а не список: предпочтения
    эволюционируют, а не накапливаются. Авто-обновляется на каждом compress
    через merge delta-обновлений (extract_comm_prefs), как personality-карта.
    Инжектится как жёсткий стиль-констрейнт в системный промпт (до фактов),
    поэтому всегда доходит до LLM и не вытесняется overflow-эвикшеном.
    """

    style: str = ""                 # желаемый стиль общения
    formality: str = ""             # уровень формальности
    humor: str = ""                 # отношение к юмору
    language: str = ""              # предпочтительный язык
    liked_topics: list[str] = field(default_factory=list)
    avoided_topics: list[str] = field(default_factory=list)
    updated_at: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommPref:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ContextBundle:
    """Selected context for a single LLM request."""
    facts: list[Fact] = field(default_factory=list)
    reflections: list[Reflection] = field(default_factory=list)
    patterns: list[Any] = field(default_factory=list)
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
    runtime_context_block: str = ""
    comm_prefs: "CommPref | None" = None

    def to_prompt_block(self) -> str:
        from companion.security.sanitizer import sanitize_markup

        parts: list[str] = []
        if self.runtime_context_block:
            parts.append(self.runtime_context_block)
        if self.identity_vault_block:
            sanitized_id = sanitize_markup(self.identity_vault_block) or ""
            parts.append(f"<system_identity>\n{sanitized_id}\n</system_identity>")
        # Уровень 4: предпочтения общения — ЖЁСТКИЙ стиль-констрейнт. До
        # <user_profile> и фактов, чтобы всегда доходить до LLM независимо от
        # режима (query/no-query) и не вытесняться overflow-эвикшеном.
        if self.comm_prefs is not None:
            cp = self.comm_prefs
            lines = []
            if cp.style:
                lines.append(f"Стиль общения: {sanitize_markup(cp.style) or ''}")
            if cp.formality:
                lines.append(f"Формальность: {sanitize_markup(cp.formality) or ''}")
            if cp.humor:
                lines.append(f"Юмор: {sanitize_markup(cp.humor) or ''}")
            if cp.language:
                lines.append(f"Язык: {sanitize_markup(cp.language) or ''}")
            if cp.liked_topics:
                topics = "; ".join(sanitize_markup(t) or "" for t in cp.liked_topics[:20] if t)
                if topics.strip():
                    lines.append(f"Любимые темы: {topics}")
            if cp.avoided_topics:
                topics = "; ".join(sanitize_markup(t) or "" for t in cp.avoided_topics[:20] if t)
                if topics.strip():
                    lines.append(f"Нежелательные темы: {topics}")
            if lines:
                parts.append("[Предпочтения общения пользователя]\n" + "\n".join(lines))

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
        if self.patterns:
            lines = []
            for p in self.patterns:
                lines.append(f"• {sanitize_markup(p.pattern) or ''}")
                ev = getattr(p, "evidence", None) or []
                if ev:
                    joined = "; ".join(str(sanitize_markup(str(e)) or "") for e in ev)
                    if joined.strip():
                        lines.append(f"  Основано на: {joined}")
            memory_parts.append("[Паттерны поведения пользователя]\n" + "\n".join(lines))
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
