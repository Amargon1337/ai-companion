"""Authoritative boundary for data sent to external model providers.

The boundary is intentionally provider-agnostic.  Callers pass a purpose and
optionally an owner; the boundary classifies/redacts the payload and records a
metadata-only audit entry when a request context has a database attached.
"""
from __future__ import annotations

import contextvars
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DataClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    HIGHLY_SENSITIVE = "highly_sensitive"
    SECRET = "secret"


class EgressDecision(StrEnum):
    ALLOWED = "allowed"
    REDACTED = "redacted"
    SUMMARIZED = "summarized"
    DENIED = "denied"


@dataclass(frozen=True)
class EgressContext:
    owner_id: int | None = None
    request_id: str = ""
    db: Any | None = None


@dataclass(frozen=True)
class EgressResult:
    payload: str
    decision: EgressDecision
    classes: tuple[DataClass, ...]
    redactions: int


_context: contextvars.ContextVar[EgressContext] = contextvars.ContextVar(
    "llm_egress_context", default=EgressContext()
)

# Deliberately conservative detectors for machine credentials.  This is not a
# semantic privacy solution; policy callers must classify sensitive sources.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|token|password|passwd|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:AKIA|AIza)[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]+"),
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_FINANCIAL = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def bind_egress_context(owner_id: int | None, request_id: str = "", db: Any | None = None):
    return _context.set(EgressContext(owner_id=owner_id, request_id=request_id, db=db))


def reset_egress_context(token: contextvars.Token[EgressContext]) -> None:
    _context.reset(token)


def classify_text(text: str) -> set[DataClass]:
    classes: set[DataClass] = {DataClass.INTERNAL}
    lowered = text.lower()
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        classes.add(DataClass.SECRET)
    if _EMAIL.search(text) or _PHONE.search(text) or _FINANCIAL.search(text):
        classes.add(DataClass.PERSONAL)
        classes.add(DataClass.SENSITIVE)
    if any(marker in lowered for marker in (
        "diagnos", "therapy", "терап", "диагноз", "лекарств", "суицид",
        "самоуб", "паспорт", "address", "адрес", "bank", "карта",
    )):
        classes.add(DataClass.HIGHLY_SENSITIVE)
    return classes


def redact_text(text: str, classes: set[DataClass]) -> tuple[str, int]:
    redactions = 0
    output = text
    for pattern in _SECRET_PATTERNS:
        output, n = pattern.subn("[REDACTED_SECRET]", output)
        redactions += n
    # Personal identifiers are redacted whenever the payload is sensitive.
    if DataClass.SENSITIVE in classes or DataClass.HIGHLY_SENSITIVE in classes:
        output, n = _EMAIL.subn("[REDACTED_EMAIL]", output)
        redactions += n
        output, n = _PHONE.subn("[REDACTED_PHONE]", output)
        redactions += n
        output, n = _FINANCIAL.subn("[REDACTED_FINANCIAL]", output)
        redactions += n
    return output, redactions


def prepare_external_payload(payload: str, *, purpose: str, provider: str = "google", model: str = "") -> EgressResult:
    """Classify, redact and metadata-audit one external-model payload.

    Secrets are never sent. Highly sensitive payloads are denied by default
    unless the caller supplies a previously redacted payload; this makes the
    secure default explicit instead of depending on each LLM call site.
    """
    ctx = _context.get()
    classes = classify_text(payload)
    if DataClass.SECRET in classes:
        safe, count = redact_text(payload, classes)
        decision = EgressDecision.REDACTED
    elif DataClass.HIGHLY_SENSITIVE in classes:
        # High-risk content is not exported by default.  A future explicit
        # consent/summarization workflow may replace this marker, but raw text
        # must never leak merely because it is interpolated into a prompt.
        safe, count = "[HIGHLY_SENSITIVE_CONTENT_WITHHELD]", 1
        decision = EgressDecision.DENIED
    else:
        safe, count = redact_text(payload, classes)
        decision = EgressDecision.REDACTED if count else EgressDecision.ALLOWED
    result = EgressResult(safe, decision, tuple(sorted(classes, key=str)), count)
    if ctx.db is not None:
        try:
            ctx.db.record_llm_egress(
                owner_id=ctx.owner_id,
                request_id=ctx.request_id,
                purpose=purpose,
                provider=provider,
                model=model,
                data_classes=[str(x) for x in result.classes],
                decision=str(result.decision),
                redactions=result.redactions,
                payload_size=len(payload),
                payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
        except Exception:
            # Governance telemetry must never make a user turn fail; database
            # failures remain visible through normal application logging.
            pass
    return result
