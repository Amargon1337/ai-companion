"""Legacy Import Pipeline — migrate legacy jsonl history through the NEW
memory architecture.

Design principle (per user directive): NO direct INSERT into facts. Legacy
data must flow through the exact same path as live data, so every new-architecture
invariant applies:

    messages.jsonl -> parser -> store.log_message          (sanitize + messages)
                        -> store.add_fact                  (dedup -> governor
                                                             quarantine -> txn
                                                             -> event journal
                                                             -> embeddings)
                        -> event_bus.publish(FactCreatedEvent) -> journal
                        -> IndexSyncService -> FAISS

Benefits of routing through the live path:
  * injection guard: _looks_like_injection -> pending_review/quarantine;
  * OCC/transaction semantics, mutation log, event journal, genome rows,
    working-memory eligibility — all identical to live facts;
  * idempotency: stable message ids (hash of ts|role|text) + INSERT OR IGNORE,
    and add_fact's dedup gate means re-imports add nothing.

Usage:
    python -m companion.legacy_import [--path data/messages.jsonl]
                                       [--dry-run] [--facts-only]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from companion.config import DATA_DIR

logger = logging.getLogger(__name__)

# Heuristic importance by message length (chars) when the source row has none.
_LENGTH_IMPORTANCE = ((0, 3), (30, 4), (60, 5), (140, 6), (280, 7))
# Messages shorter than this cannot become facts.
_MIN_FACT_CHARS = 30
# Idempotency marker: hash of the source file, stored in meta.
_IMPORT_MARKER_KEY = "legacy_import_done_v1"

# Trivial chit-chat that should never become a fact (deterministic filter).
_TRIVIAL = {
    "привет", "ок", "окей", "спасибо", "да", "нет", "ага", "угу",
    "хорошо", "понятно", "ладно", "спокойной ночи", "доброе утро",
    "привет!", "пока", "до завтра",
}


class LegacyImportPipeline:
    """Parses legacy jsonl and routes rows through the live MemoryStore."""

    def __init__(self, store: Any, *, user_id: int = 0) -> None:
        self.store = store
        self.user_id = user_id

    # ── stage 1: parse ─────────────────────────────────────────────────────

    def parse_messages(self, path: str) -> list[dict[str, Any]]:
        """Tolerant jsonl parser: skips malformed lines, normalizes keys."""
        messages: list[dict[str, Any]] = []
        if not os.path.exists(path):
            return messages
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Legacy import: skipped malformed line: %.80s", line)
                    continue
                msg = self._normalize_row(row)
                if msg is not None:
                    messages.append(msg)
        return messages

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        text = str(row.get("text") or row.get("message") or "").strip()
        if not text:
            return None
        role = str(row.get("role") or row.get("sender") or "user").lower()
        if role not in ("user", "assistant", "system", "model"):
            role = "user"
        ts = str(row.get("ts") or row.get("timestamp") or row.get("date") or "")
        try:
            importance = int(row.get("importance", 0) or 0)
        except (TypeError, ValueError):
            importance = 0
        return {
            "role": role,
            "text": text,
            "ts": ts,
            "importance": importance,
        }

    # ── stage 2: messages through the live path ────────────────────────────

    def import_messages(self, messages: list[dict[str, Any]]) -> dict[str, int]:
        """Route every message through store.log_message (sanitize + idempotent
        INSERT OR IGNORE with a stable id derived from content)."""
        imported = 0
        skipped = 0
        for m in messages:
            role = m["role"]
            text = m["text"]
            ts = m["ts"]
            importance = m["importance"]
            # Stable id: re-imports collide and are ignored (INSERT OR IGNORE).
            mid = f"msg_{hashlib.sha1(f'{ts}|{role}|{text}'.encode('utf-8')).hexdigest()[:20]}"
            try:
                self.store.log_message(
                    role,
                    text,
                    max(1, min(10, importance or 3)),
                    mode="legacy",
                    signals=["legacy_import"],
                    user_id=self.user_id,
                    msg_id=mid,
                    ts=ts,
                )
                imported += 1
            except Exception as exc:
                logger.debug("Legacy import: message skipped: %s", exc)
                skipped += 1
        return {"imported": imported, "skipped": skipped}

    # ── stage 3: deterministic fact candidates ─────────────────────────────

    def extract_fact_candidates(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deterministic, conservative heuristic fact extraction (no LLM —
        the import must be offline-capable and reproducible).

        Rules:
          * only 'user' messages;
          * length >= _MIN_FACT_CHARS;
          * not in the trivial chit-chat set;
          * importance estimate by length (importance >= 5 qualifies).
        """
        candidates: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] != "user":
                continue
            text = m["text"]
            if len(text) < _MIN_FACT_CHARS:
                continue
            lowered = " ".join(text.lower().split())
            if lowered in _TRIVIAL or any(t in lowered for t in _TRIVIAL if len(t) >= 5):
                continue
            imp = m["importance"]
            if not imp:
                for threshold, score in _LENGTH_IMPORTANCE:
                    if len(text) > threshold:
                        imp = score
            if imp < 5:
                continue
            candidates.append({
                "fact": text,
                "date": (m["ts"] or "")[:10],
                "importance": max(5, min(10, imp)),
                "source": "legacy_import",
            })
        return candidates

    # ── stage 4: facts through the live path ───────────────────────────────

    def import_facts(self, candidates: list[dict[str, Any]]) -> dict[str, int]:
        """Route candidate facts through store.add_fact.

        This is THE architectural gate: dedup -> governor ingestion validation
        -> atomic transaction -> FactCreatedEvent -> event journal -> FAISS
        embedding. Any quarantine/pending_review decision comes from the same
        code path live facts use — nothing is inserted directly.

        Idempotency: each candidate gets a STABLE fact id derived from its
        text. The dedup gate only sees active/dormant facts, but an imported
        fact may sit in pending_embedding (API down) or pending_review
        (injection) — so we check existence by id first. Re-imports then add
        nothing, regardless of status.
        """
        from companion.models import Fact

        stats = {"created": 0, "deduped": 0, "quarantined": 0, "failed": 0}
        for cand in candidates:
            stable_id = f"legacy_{hashlib.sha1(cand['fact'].encode('utf-8')).hexdigest()[:16]}"
            if self.store.get_fact(stable_id) is not None:
                stats["deduped"] += 1
                continue
            fact = Fact(
                id=stable_id,
                fact=cand["fact"],
                date=cand["date"] or datetime.now().strftime("%Y-%m-%d"),
                importance=cand["importance"],
                confidence=0.75,  # heuristic import, no LLM verification
                source="legacy_import",
                source_type="import",
                memory_kind="event",
            )
            result = self.store.add_fact(fact)
            if result.status in ("quarantine", "pending_review"):
                stats["quarantined"] += 1
            elif result.id == fact.id:
                stats["created"] += 1
            else:
                stats["deduped"] += 1
        return stats

    # ── orchestrator ───────────────────────────────────────────────────────

    def run(self, path: str | None = None, *, dry_run: bool = False,
            facts_only: bool = False) -> dict[str, Any]:
        """Full pipeline: parse -> messages -> facts. Idempotent by design;
        the marker meta key records the source file hash so a re-run of an
        unchanged file is a no-op."""
        path = path or os.path.join(DATA_DIR, "messages.jsonl")
        if not os.path.exists(path):
            return {"error": f"source not found: {path}"}

        file_hash = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if not dry_run and not facts_only:
            if self.store.db.get_meta(_IMPORT_MARKER_KEY, "") == file_hash:
                return {"status": "already_imported", "file": path}

        messages = self.parse_messages(path)
        report: dict[str, Any] = {
            "file": path,
            "parsed": len(messages),
            "dry_run": dry_run,
        }

        if facts_only:
            candidates = self.extract_fact_candidates(messages)
            if not dry_run:
                report["facts"] = self.import_facts(candidates)
            else:
                report["fact_candidates"] = len(candidates)
        else:
            if dry_run:
                report["messages_would_import"] = len(messages)
                report["fact_candidates"] = len(self.extract_fact_candidates(messages))
            else:
                report["messages"] = self.import_messages(messages)
                candidates = self.extract_fact_candidates(messages)
                report["facts"] = self.import_facts(candidates)
                self.store.db.set_meta(_IMPORT_MARKER_KEY, file_hash)
                report["status"] = "done"
        return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Legacy memory import pipeline")
    parser.add_argument("--path", default=None, help="path to messages.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="parse only, no writes")
    parser.add_argument("--facts-only", action="store_true",
                        help="skip messages stage, extract facts directly")
    args = parser.parse_args()

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from companion.memory.store import MemoryStore

    store = MemoryStore()
    try:
        pipeline = LegacyImportPipeline(store)
        report = pipeline.run(args.path, dry_run=args.dry_run, facts_only=args.facts_only)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
