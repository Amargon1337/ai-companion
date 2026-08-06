"""Theory of Mind engine (R6) — layered social cognition, derived read-model.

Cognitive function: model what the user believes/values/expects at three
levels, all DERIVED (never authoritative — the vault/facts remain the truth):
  L1 (behavior):   what the user does/says — from facts with entity mentions.
  L2 (values):     what the user cares about — from patterns + human_model.
  L3 (meta):       what the user thinks the bot understands — from reflected
                   statements, lowest confidence, shortest TTL.

Anti-hype note: this is not "mind reading"; it is a relational derivation over
observed memory with explicit confidence and provenance (basis_ids). Every
claim is stored with epistemic_class != DIRECT_FACT (it is always an
inference about the other agent).

Iron Law #5: superseded/refuted claims are kept, never deleted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# TTL per level (days) — L3 meta-perception ages fastest.
_TOM_TTL_DAYS = {1: 365, 2: 180, 3: 30}


class TheoryOfMindEngine:
    def __init__(self, db: Any, store: Any = None) -> None:
        self.db = db
        self.store = store

    # ── level builders (pure derivations) ─────────────────────────────────

    def build_level1(self, entity_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """L1: what the user does — facts mentioning the entity."""
        claims: list[dict[str, Any]] = []
        try:
            mentions = self.db.get_mentions_for_entity(entity_id)
            seen: set[str] = set()
            for m in mentions:
                fid = str(m.get("fact_id", ""))
                if fid in seen:
                    continue
                seen.add(fid)
                row = self.db.get_fact(fid)
                if not row or str(row.get("status", "")) not in ("active", "dormant"):
                    continue
                claims.append({
                    "claim": str(row.get("fact", ""))[:256],
                    "confidence": min(0.9, float(row.get("confidence", 0.6))),
                    "basis_ids": [fid],
                    "level": 1,
                })
                if len(claims) >= limit:
                    break
        except Exception as exc:
            logger.debug("ToM L1 failed for %s: %s", entity_id, exc)
        return claims

    def build_level2(self, entity_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """L2: what the user values — patterns + human-model insights."""
        claims: list[dict[str, Any]] = []
        try:
            for p in self.store.list_patterns("active") if self.store else []:
                claims.append({
                    "claim": str(p.pattern)[:256],
                    "confidence": min(0.9, float(getattr(p, "confidence", 0.7))),
                    "basis_ids": list(getattr(p, "evidence", None) or []),
                    "level": 2,
                })
                if len(claims) >= limit:
                    break
        except Exception as exc:
            logger.debug("ToM L2 failed for %s: %s", entity_id, exc)
        return claims

    def build_level3(self, entity_id: str, reflected_text: str,
                     confidence: float = 0.5) -> dict[str, Any] | None:
        """L3: meta-perception — ONLY from an explicit reflected statement.

        Never derived automatically; the caller must supply the text the user
        said about the bot's understanding. Lowest confidence by default.
        """
        text = (reflected_text or "").strip()
        if not text:
            return None
        return {
            "claim": text[:256],
            "confidence": max(0.1, min(0.7, float(confidence))),
            "basis_ids": [],
            "level": 3,
        }

    # ── persistence & refresh ─────────────────────────────────────────────

    def refresh(self, entity_id: str) -> dict[str, int]:
        """Re-derive L1+L2 for an entity; supersede stale active claims.

        Deterministic (no LLM). New claims are inserted; previously-active
        claims whose text no longer appears are superseded (kept, not deleted).
        """
        fresh = (self.build_level1(entity_id) + self.build_level2(entity_id))
        fresh_norm = {" ".join(c["claim"].lower().split()): c for c in fresh}
        stats = {"inserted": 0, "superseded": 0, "kept": 0}

        existing = self.db.list_tom_claims(entity_id, status="active")
        existing_by_norm = {
            " ".join(str(c["claim"] or "").lower().split()): c for c in existing
        }

        # Supersede active claims no longer supported by derivation.
        for norm, claim in existing_by_norm.items():
            if norm not in fresh_norm:
                try:
                    self.db.update_tom_status(int(claim["id"]), "superseded")
                    stats["superseded"] += 1
                except Exception as exc:
                    logger.debug("ToM supersede failed: %s", exc)
            else:
                stats["kept"] += 1

        # Insert new claims.
        for norm, claim in fresh_norm.items():
            if norm not in existing_by_norm:
                try:
                    self.db.insert_tom_claim({
                        "subject_entity_id": entity_id,
                        "level": int(claim["level"]),
                        "claim": claim["claim"],
                        "confidence": claim["confidence"],
                        "basis_ids": claim["basis_ids"],
                    })
                    stats["inserted"] += 1
                except Exception as exc:
                    logger.debug("ToM insert failed: %s", exc)

        return stats

    def active_for(self, entity_id: str, level: int | None = None) -> list[dict[str, Any]]:
        """Active claims for the entity, TTL-aware (aged claims excluded)."""
        claims = self.db.list_tom_claims(entity_id, level=level, status="active")
        now = datetime.now()
        live = []
        for c in claims:
            try:
                created = datetime.fromisoformat(str(c.get("created_at", "")))
            except (ValueError, TypeError):
                created = now
            ttl = timedelta(days=_TOM_TTL_DAYS.get(int(c.get("level", 2)), 180))
            if now - created <= ttl:
                live.append(c)
        return live

    def to_prompt_block(self, entity_id: str, limit_per_level: int = 3) -> str:
        """Compact prompt block: [L1] observed, [L2] values, [L3] meta."""
        parts: list[str] = []
        for level in (1, 2, 3):
            claims = self.active_for(entity_id, level=level)[:limit_per_level]
            if not claims:
                continue
            label = {1: "Наблюдаемое поведение", 2: "Ценности/паттерны",
                     3: "Мета-восприятие (что думает о моём понимании)"}[level]
            lines = [f"• {c['claim']}" for c in claims]
            parts.append(f"[ToM L{level} — {label}]\n" + "\n".join(lines))
        return "\n\n".join(parts)
