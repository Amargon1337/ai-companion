"""Data models for memory architecture."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

MemoryKind = Literal["permanent", "state", "event"]
FactStatus = Literal["active", "superseded", "inactive", "archived", "dormant", "pending_review"]
RelationType = Literal[
    "supersedes",
    "contradicts",
    "confirms",
    "related_to",
    "caused_by",
    "causes",
    "supports",
    "summarized_by",
    "summarizes",
]
InsightStatus = Literal["active", "archived", "refuted", "aging", "stale"]


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
    last_confirmed_at: str = ""   # Reliability Layer: дата последнего подтверждения
    version: int = 1
    superseded_by: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("pat")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.last_confirmed_at:
            self.last_confirmed_at = self.created_at

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


# ── Life Continuity Engine (LCE): траектория изменений личности ──
TransitionStatus = Literal["active", "completed", "uncertain", "reversed", "pending_review"]

# Домены, в которых человек может меняться со временем.
TRANSITION_DOMAINS = (
    "identity", "career", "relationships", "habits",
    "interests", "worldview", "mental_state",
)


@dataclass
class LifeTransition:
    """Один устойчивый переход состояния человека между двумя точками во времени.

    НЕ хранит факты и НЕ дублирует HumanModel. Хранит ИЗМЕНЕНИЕ:
    от состояния A к состоянию B, с причиной и доказательствами (fact ids).
    Старение/подтверждение — через last_confirmed_at (как у Pattern).
    Низкая уверенность (LLM склонен придумывать красивую историю) → pending_review.
    """

    domain: str                       # см. TRANSITION_DOMAINS
    from_state: str
    to_state: str
    explanation: str = ""
    trigger_events: list[str] = field(default_factory=list)   # fact ids / описания
    confidence: float = 0.7
    importance: int = 6
    status: TransitionStatus = "active"
    id: str = ""
    created_at: str = ""
    last_confirmed_at: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("lce")
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.last_confirmed_at:
            self.last_confirmed_at = now
        self.domain = (self.domain or "identity").lower()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LifeTransition":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        d = {k: v for k, v in data.items() if k in known}
        for fld in ("confidence", "importance", "version"):
            if fld in d and not isinstance(d[fld], (int, float)):
                try:
                    d[fld] = float(d[fld]) if fld == "confidence" else int(d[fld])
                except (TypeError, ValueError):
                    d[fld] = 0.7 if fld == "confidence" else (6 if fld == "importance" else 1)
        if "trigger_events" in d and not isinstance(d["trigger_events"], list):
            try:
                d["trigger_events"] = list(d["trigger_events"])
            except TypeError:
                d["trigger_events"] = []
        return cls(**d)


def compute_transition_status(transition) -> str:
    """Лениво: явный статус (completed/reversed/uncertain/pending_review)
    приоритетнее; иначе — ленивое старение по last_confirmed_at (как паттерн)."""
    explicit = getattr(transition, "status", "active")
    if explicit in ("completed", "reversed", "uncertain", "pending_review"):
        return explicit
    from companion.config import PATTERN_AGING_DAYS, PATTERN_STALE_DAYS
    from companion.memory.importance import days_since
    ref = getattr(transition, "last_confirmed_at", "") or getattr(transition, "created_at", "")
    age = days_since(ref)
    if age >= PATTERN_STALE_DAYS:
        return "stale"
    if age >= PATTERN_AGING_DAYS:
        return "aging"
    return "active"


@dataclass
class HumanModelInsight:
    """Один вывод о человеке с метаданными свежести (Reliability Layer).

    Это НЕ факт. Это инференс. Система не говорит 'этого не было' — она
    говорит 'это больше не подтверждалось'. Поэтому выводы не удаляются,
    а переходят active → aging → stale по времени без подтверждения.
    """

    text: str
    dimension: str = "general"   # goals|fears|strengths|recurring_mistakes|long_term_trends
    confidence: float = 0.7
    created_at: str = ""
    last_supported_at: str = ""
    evidence_count: int = 1
    status: InsightStatus = "active"
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id("hm")
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.last_supported_at:
            self.last_supported_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HumanModelInsight":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        d = {k: v for k, v in data.items() if k in known}
        # int(json) / str guards for robustness across migrations
        if "evidence_count" in d and not isinstance(d["evidence_count"], int):
            try:
                d["evidence_count"] = int(d["evidence_count"])
            except (TypeError, ValueError):
                d["evidence_count"] = 1
        if "confidence" in d and not isinstance(d["confidence"], float):
            try:
                d["confidence"] = float(d["confidence"])
            except (TypeError, ValueError):
                d["confidence"] = 0.7
        return cls(**d)


@dataclass
class HumanModel:
    """Уровень 6 + Reliability Layer: самостоятельная модель человека.

    Это выводы (inferences), не факты. Единая запись (key="global"),
    но внутри — список HumanModelInsight с метаданными свежести.
    Авто-обновляется на каждом compress через merge delta-выводов
    (extract_human_model → upsert_human_model), где каждый новый вывод
    либо создаёт инсайт, либо подтверждает существующий (bump
    last_supported_at + evidence_count). Старение (active→aging→stale)
    считается лениво в compute_status() по last_supported_at, без
    мутации БД — поэтому история не теряется.
    """

    goals: list[HumanModelInsight] = field(default_factory=list)
    fears: list[HumanModelInsight] = field(default_factory=list)
    strengths: list[HumanModelInsight] = field(default_factory=list)
    recurring_mistakes: list[HumanModelInsight] = field(default_factory=list)
    long_term_trends: list[HumanModelInsight] = field(default_factory=list)
    updated_at: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def all_insights(self) -> list[HumanModelInsight]:
        return self.goals + self.fears + self.strengths + self.recurring_mistakes + self.long_term_trends

    def to_dict(self) -> dict[str, Any]:
        return {
            "goals": [i.to_dict() for i in self.goals],
            "fears": [i.to_dict() for i in self.fears],
            "strengths": [i.to_dict() for i in self.strengths],
            "recurring_mistakes": [i.to_dict() for i in self.recurring_mistakes],
            "long_term_trends": [i.to_dict() for i in self.long_term_trends],
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HumanModel":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        def _as_list(v):
            return [HumanModelInsight.from_dict(x) if isinstance(x, dict) else
                    HumanModelInsight(text=str(x)) for x in (v or [])]
        return cls(
            goals=_as_list(data.get("goals")),
            fears=_as_list(data.get("fears")),
            strengths=_as_list(data.get("strengths")),
            recurring_mistakes=_as_list(data.get("recurring_mistakes")),
            long_term_trends=_as_list(data.get("long_term_trends")),
            updated_at=data.get("updated_at", ""),
            version=int(data.get("version", 1) or 1),
        )


def compute_insight_status(insight: "HumanModelInsight") -> str:
    """Лениво считает актуальный статус старения по last_supported_at.

    Не мутирует БД — стареют только метаданные в памяти. explicit
    'superseded' сохраняется (его выставил extractor при прямом
    противоречии). active → aging → stale по дням без подтверждения.
    """
    if insight.status == "superseded":
        return "superseded"
    from companion.config import HM_AGING_DAYS, HM_STALE_DAYS
    from companion.memory.importance import days_since
    age = days_since(insight.last_supported_at or insight.created_at)
    if age >= HM_STALE_DAYS:
        return "stale"
    if age >= HM_AGING_DAYS:
        return "aging"
    return "active"


def compute_pattern_status(pattern, confirmed: bool = False) -> str:
    """Лениво считает статус старения паттерна по last_confirmed_at."""
    if getattr(pattern, "status", "active") == "superseded":
        return "superseded"
    from companion.config import PATTERN_AGING_DAYS, PATTERN_STALE_DAYS
    from companion.memory.importance import days_since
    ref = getattr(pattern, "last_confirmed_at", "") or getattr(pattern, "created_at", "")
    age = days_since(ref)
    if age >= PATTERN_STALE_DAYS:
        return "stale"
    if age >= PATTERN_AGING_DAYS:
        return "aging"
    return "active"


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
    human_model: "HumanModel | None" = None
    life_transitions: "list[Any]" = field(default_factory=list)  # LCE: траектория изменений

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

        # Уровень 6 + Reliability Layer: модель человека — выводы (не факты).
        # Всегда-активный блок, до <user_profile>, не вытесняется эвикшеном.
        # Группируем по уверенности: active (Высокая) vs aging/stale (Устаревающие).
        if self.human_model is not None:
            hm = self.human_model
            from companion.models import compute_insight_status  # local import guard
            def _label(ins: "Any") -> str:
                st = compute_insight_status(ins)
                if st == "stale":
                    return " [давно не подтверждалось]"
                if st == "aging":
                    return " [подтверждалось давно]"
                return ""
            confident: list[str] = []
            aging: list[str] = []
            for dim in ("goals", "fears", "strengths", "recurring_mistakes", "long_term_trends"):
                for ins in getattr(hm, dim):
                    text = sanitize_markup(ins.text) or ""
                    if not text:
                        continue
                    if compute_insight_status(ins) == "active":
                        confident.append(f"• {text}")
                    else:
                        aging.append(f"• {text}{_label(ins)}")
            hm_lines = ["ВНИМАНИЕ: это НЕ факты. Это долгосрочные выводы о пользователе с разной степенью уверенности."]
            if confident:
                hm_lines.append("Высокая уверенность (недавно подтверждалось):")
                hm_lines.extend(confident[:15])
            if aging:
                hm_lines.append("Устаревающие (давно не подтверждалось — не факт, что устарело):")
                hm_lines.extend(aging[:15])
            if hm_lines:
                parts.append("[Модель человека — выводы]\n" + "\n".join(hm_lines))

        # Life Continuity Engine (LCE): траектория изменений личности.
        # Это НЕ факты и НЕ снимок — это ПЕРЕХОДЫ (от состояния к состоянию).
        # Блок идёт сразу после модели человека, до <user_profile>, вне бюджета
        # эвикшена (как CommPref/HumanModel). pending_review не показываем (не
        # проверено) — только подтверждённые/важные переходы.
        if self.life_transitions:
            from companion.models import compute_transition_status  # local import
            t_lines = ["ВНИМАНИЕ: это траектория изменений человека, а не снимок состояния. 'Что изменилось' важнее 'кто он сейчас'."]
            shown = 0
            for t in self.life_transitions:
                st = compute_transition_status(t)
                if st in ("pending_review",):
                    continue  # не проверенные переходы не лезут в промпт
                if st in ("stale", "aging"):
                    tag = " [давно не подтверждалось]"
                elif st == "completed":
                    tag = " [завершён]"
                elif st == "reversed":
                    tag = " [обращён]"
                else:
                    tag = ""
                dom = getattr(t, "domain", "identity")
                fs = sanitize_markup(getattr(t, "from_state", "")) or ""
                ts = sanitize_markup(getattr(t, "to_state", "")) or ""
                expl = sanitize_markup(getattr(t, "explanation", "")) or ""
                if not (fs and ts):
                    continue
                line = f"• [{dom}] {fs} → {ts}{tag}"
                if expl:
                    line += f"\n    почему: {expl}"
                t_lines.append(line)
                shown += 1
                if shown >= 10:
                    break
            if shown:
                parts.append("[Жизненные переходы]\n" + "\n".join(t_lines))

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
            from companion.models import compute_pattern_status  # local import
            lines = []
            for p in self.patterns:
                st = compute_pattern_status(p)
                suffix = ""
                if st == "stale":
                    suffix = " [давно не подтверждалось]"
                elif st == "aging":
                    suffix = " [подтверждалось давно]"
                lines.append(f"• {sanitize_markup(p.pattern) or ''}{suffix}")
                ev = getattr(p, "evidence", None) or []
                if ev:
                    joined = "; ".join(str(sanitize_markup(str(e)) or "") for e in ev)
                    if joined.strip():
                        lines.append(f"  Основано на: {joined}")
            memory_parts.append("[Паттерны поведения пользователя]\n" + "\n".join(lines))

        if self.facts:
            from companion.temporal import format_relative_time
            lines = []
            for f in self.facts:
                date_str = getattr(f, "date", None) or getattr(f, "created_at", "")
                rel_time = format_relative_time(date_str) if date_str else ""
                time_label = rel_time or (date_str[:10] if isinstance(date_str, str) and len(date_str) >= 10 else "недавно")
                lines.append(f"• [{f.memory_kind}|{time_label}] {sanitize_markup(f.fact) or ''}")
            memory_parts.append("[Релевантные факты]\n" + "\n".join(lines))
        if self.summaries:
            sanitized_sum = [sanitize_markup(s) or "" for s in self.summaries]
            memory_parts.append("[Контекст саммари]\n" + "\n".join(sanitized_sum))
            
        if memory_parts:
            parts.append("<conversational_memory>\n" + "\n\n".join(memory_parts) + "\n</conversational_memory>")
            
        return "\n\n".join(parts)
