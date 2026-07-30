"""Projection Verification Layer for Phase C1.6."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from companion.memory.replay import ProjectionRebuilder
from companion.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Essential fields that must be present in replayed states to guard against
# "added a field to Fact model but forgot to include it in event payload".
REQUIRED_REPLAY_FIELDS = [
    "id",
    "fact",
    "status",
    "importance",
    "origin",
    "identity_layer",
    "confidence",
    "conf_observed",
    "conf_inferred",
    "conf_stability",
    "conf_verification",
]

FIELDS_TO_COMPARE = [
    "id",
    "fact",
    "status",
    "importance",
    "origin",
    "identity_layer",
    "confidence",
    "conf_observed",
    "conf_inferred",
    "conf_stability",
    "conf_verification",
    "source_message_id",
]


@dataclass
class VerificationResult:
    passed: bool
    missing: list[str] = field(default_factory=list)
    mismatched: list[dict[str, Any]] = field(default_factory=list)


def verify_projection_integrity(memory_store: MemoryStore) -> VerificationResult:
    """Verifies that the current SQLite facts projection matches the Replay from Event Store.

    Returns diagnostic VerificationResult with any missing IDs or field mismatches.
    """
    sqlite_facts = {f.id: f.to_dict() for f in memory_store.list_all_facts()}
    rebuilder = ProjectionRebuilder(memory_store.events)
    replayed_facts = rebuilder.build_snapshot()

    missing: list[str] = []
    mismatched: list[dict[str, Any]] = []

    # 1. Check for missing aggregate IDs
    for fid in sqlite_facts:
        if fid not in replayed_facts:
            missing.append(f"in_sqlite_missing_in_replay:{fid}")

    for fid in replayed_facts:
        if fid not in sqlite_facts:
            missing.append(f"in_replay_missing_in_sqlite:{fid}")

    # 2. Check field parity and required fields for each common ID
    for fid in sqlite_facts:
        if fid not in replayed_facts:
            continue
        sql_fact = sqlite_facts[fid]
        rep_fact = replayed_facts[fid]

        # Guard check: ensure all REQUIRED_REPLAY_FIELDS exist in replayed state
        for req_field in REQUIRED_REPLAY_FIELDS:
            if req_field not in rep_fact:
                mismatched.append({
                    "id": fid,
                    "field": req_field,
                    "sqlite_value": "REQUIRED_FIELD_PRESENT",
                    "replay_value": "MISSING_FROM_REPLAY_PAYLOAD",
                })

        # Compare values for FIELDS_TO_COMPARE
        for f_name in FIELDS_TO_COMPARE:
            sql_val = sql_fact.get(f_name)
            rep_val = rep_fact.get(f_name)

            # Normalize None / empty values
            if sql_val == "" or sql_val is None:
                sql_val = None
            if rep_val == "" or rep_val is None:
                rep_val = None

            if sql_val is None and rep_val is None:
                continue

            # Compare floats with epsilon tolerance
            if isinstance(sql_val, (int, float)) and isinstance(rep_val, (int, float)):
                if abs(float(sql_val) - float(rep_val)) > 1e-5:
                    mismatched.append({
                        "id": fid,
                        "field": f_name,
                        "sqlite_value": sql_val,
                        "replay_value": rep_val,
                    })
            elif str(sql_val) != str(rep_val):
                mismatched.append({
                    "id": fid,
                    "field": f_name,
                    "sqlite_value": sql_val,
                    "replay_value": rep_val,
                })

    passed = len(missing) == 0 and len(mismatched) == 0
    return VerificationResult(passed=passed, missing=missing, mismatched=mismatched)
