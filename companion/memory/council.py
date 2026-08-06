"""Internal Council (R5) — multi-role consensus before high-stakes mutations.

Cognitive function: a mutation to identity / beliefs / anchors must survive a
role-based vote, not just a single heuristic. The council is fully
deterministic (no LLM — Iron Law #1 on an 8GB box): each role reads SQLite
state and casts accept/reject/quarantine/abstain. The majority verdict is the
gate a caller can enforce for high-stakes writes.

Roles (blueprint S7):
  * Explorer  — novelty: is this mutation already represented in memory?
  * Critic    — conflict: does it contradict an existing supported belief?
  * Historian — churn: has the same subject mutated too often recently?
  * Predictor — stability: would the change disturb protected anchors?
  * Guardian  — safety: injection markers / forbidden category changes.

Failure modes: any role throwing is downgraded to abstain (the council must
never crash the caller); Guardian reject is a hard veto.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

ROLES = ("explorer", "critic", "historian", "predictor", "guardian")
VERDICTS = ("accept", "reject", "quarantine", "abstain")

# How many mutations of one subject in the window count as churn.
_HISTORIAN_WINDOW_HOURS = 24
_HISTORIAN_MAX_MUTATIONS = 5


class CouncilVerdict:
    def __init__(self, votes: list[dict[str, Any]]) -> None:
        self.votes = votes
        self.approved = self._decide()

    def _decide(self) -> bool:
        if not self.votes:
            return True  # empty council = no objection
        # Guardian is a hard veto.
        for v in self.votes:
            if v["role"] == "guardian" and v["verdict"] in ("reject", "quarantine"):
                return False
        accepts = sum(1 for v in self.votes if v["verdict"] == "accept")
        rejects = sum(1 for v in self.votes if v["verdict"] in ("reject", "quarantine"))
        return accepts > rejects


class CouncilService:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ── role implementations ──────────────────────────────────────────────

    def _explorer(self, subject_kind: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Novelty: reject if an active fact/belief already covers the text."""
        text = str(payload.get("text") or payload.get("belief") or payload.get("fact") or "")
        if not text:
            return {"role": "explorer", "verdict": "accept", "rationale": "no text to compare"}
        norm = " ".join(text.lower().split())
        from companion.memory.text_sim import text_overlap
        checked = 0
        for row in self.db.list_facts("active"):
            if row.get("fact") and text_overlap(norm, " ".join(str(row["fact"]).lower().split())) > 0.85:
                return {"role": "explorer", "verdict": "reject",
                        "rationale": f"already covered by fact {row.get('id')}"}
            checked += 1
        for b in self.db.list_beliefs("active"):
            if b.get("belief") and text_overlap(norm, " ".join(str(b["belief"]).lower().split())) > 0.85:
                return {"role": "explorer", "verdict": "reject",
                        "rationale": f"already covered by belief {b.get('id')}"}
        return {"role": "explorer", "verdict": "accept", "rationale": "novel"}

    def _critic(self, subject_kind: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Conflict: a mutation that contradicts a supported fact is suspect."""
        if subject_kind != "fact":
            return {"role": "critic", "verdict": "accept", "rationale": "not a fact subject"}
        target = self.db.get_fact(subject_id)
        if not target:
            return {"role": "critic", "verdict": "abstain", "rationale": "subject not found"}
        support = int(target.get("support_count", 0) or 0)
        contra = int(target.get("contradiction_count", 0) or 0)
        if contra > support and support < 2:
            return {"role": "critic", "verdict": "quarantine",
                    "rationale": f"contradictions ({contra}) exceed support ({support})"}
        return {"role": "critic", "verdict": "accept", "rationale": f"support={support} contra={contra}"}

    def _historian(self, subject_kind: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Churn: too many recent mutations of the same subject = instability."""
        try:
            votes = self.db.list_council_votes(subject_kind, subject_id, limit=100)
        except Exception:
            return {"role": "historian", "verdict": "abstain", "rationale": "history unavailable"}
        cutoff = (datetime.now() - timedelta(hours=_HISTORIAN_WINDOW_HOURS)).isoformat()
        recent = [v for v in votes if str(v.get("created_at", "")) >= cutoff]
        if len(recent) >= _HISTORIAN_MAX_MUTATIONS:
            return {"role": "historian", "verdict": "quarantine",
                    "rationale": f"{len(recent)} mutations in {_HISTORIAN_WINDOW_HOURS}h (churn)"}
        return {"role": "historian", "verdict": "accept", "rationale": f"{len(recent)} recent mutations"}

    def _predictor(self, subject_kind: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Stability: touching protected anchors is high-risk."""
        target = None
        if subject_kind == "fact":
            target = self.db.get_fact(subject_id)
        if not target:
            return {"role": "predictor", "verdict": "accept", "rationale": "no target"}
        tags = [str(t).lower() for t in (target.get("tags") or [])]
        if target.get("memory_kind") == "permanent" or any(
            t in tags for t in ("anchor", "core_identity", "pinned")
        ):
            return {"role": "predictor", "verdict": "quarantine",
                    "rationale": "mutation touches a protected anchor"}
        return {"role": "predictor", "verdict": "accept", "rationale": "non-protected"}

    def _guardian(self, subject_kind: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Safety: injection markers, invalid statuses, forbidden kinds."""
        from companion.security.sanitizer import _looks_like_injection
        text = str(payload.get("text") or payload.get("belief") or payload.get("fact") or "")
        if text and _looks_like_injection(text):
            return {"role": "guardian", "verdict": "reject", "rationale": "injection markers detected"}
        new_kind = str(payload.get("memory_kind", "")).lower()
        if new_kind == "permanent" and payload.get("auto_promote") is not True:
            return {"role": "guardian", "verdict": "quarantine",
                    "rationale": "permanent promotion requires explicit approval"}
        return {"role": "guardian", "verdict": "accept", "rationale": "safe"}

    # ── public API ─────────────────────────────────────────────────────────

    def evaluate(self, *, subject_kind: str, subject_id: str,
                 payload: dict[str, Any], persist: bool = True) -> CouncilVerdict:
        """Run the full council for a high-stakes mutation and (optionally)
        persist the votes for auditability."""
        votes: list[dict[str, Any]] = []
        for role in ROLES:
            try:
                fn = getattr(self, f"_{role}")
                vote = fn(subject_kind, subject_id, payload)
            except Exception as exc:
                logger.debug("Council role %s failed for %s/%s: %s",
                             role, subject_kind, subject_id, exc)
                vote = {"role": role, "verdict": "abstain", "rationale": "role error"}
            vote["subject_kind"] = subject_kind
            vote["subject_id"] = subject_id
            if persist:
                try:
                    self.db.insert_council_vote({
                        "vote_id": f"vote_{uuid.uuid4().hex[:10]}",
                        "subject_kind": subject_kind,
                        "subject_id": subject_id,
                        "role": vote["role"],
                        "verdict": vote["verdict"],
                        "rationale": vote.get("rationale", ""),
                        "created_at": datetime.now().isoformat(),
                    })
                except Exception as exc:
                    logger.debug("Council vote persist failed: %s", exc)
            votes.append(vote)
        return CouncilVerdict(votes)

    def history(self, subject_kind: str, subject_id: str) -> list[dict[str, Any]]:
        return self.db.list_council_votes(subject_kind, subject_id, limit=50)
