"""Manage the replay-to-golden retrieval learning loop."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from companion.memory.text_sim import text_overlap
from evaluation.learning import DEFAULT_WEIGHTS, benchmark, export_golden, learn_replays, load_golden, tune_weights


class ReplayStore:
    """Minimal adapter that reuses the existing replay table for offline learning."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = self

    def list_retrieval_replays(self, user_id: int | None = None, limit: int = 100):
        with sqlite3.connect(self.path) as conn:
            if user_id is None:
                rows = conn.execute(
                    "SELECT replay_id, user_id, created_at, payload FROM retrieval_replays "
                    "ORDER BY created_at DESC LIMIT ?", (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT replay_id, user_id, created_at, payload FROM retrieval_replays "
                    "WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit),
                ).fetchall()
        return [dict(zip(("replay_id", "user_id", "created_at", "payload"), row)) for row in rows]

    def update_retrieval_replay_payload(self, replay_id: str, payload: str) -> bool:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "UPDATE retrieval_replays SET payload=? WHERE replay_id=?",
                (payload, replay_id),
            )
        return cursor.rowcount > 0

    def search_facts(self, query: str, limit: int = 20):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT id, fact FROM facts WHERE status='active'").fetchall()
        ranked = sorted(rows, key=lambda row: text_overlap(query, row[1]), reverse=True)[:limit]
        return [(SimpleNamespace(id=row[0], fact=row[1]), text_overlap(query, row[1])) for row in ranked]


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay learning and retrieval benchmarking")
    parser.add_argument("command", choices=("learn", "export-golden", "benchmark", "tune"))
    parser.add_argument("--db", type=Path, default=Path("data/companion.db"))
    parser.add_argument("--golden", type=Path, default=Path("evaluation/golden.json"))
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply tuned weights only when quality improves")
    args = parser.parse_args()

    if args.command == "export-golden":
        print(f"Exported: {export_golden(args.db, args.golden, args.limit)}")
        return 0
    if args.command == "learn":
        store = ReplayStore(args.db)
        print(f"Learned: {asyncio.run(learn_replays(store, min(args.limit, 50)))}")
        return 0

    cases = load_golden(args.golden)
    if not cases:
        print(
            f"No labelled golden cases in {args.golden}. "
            "Run export-golden and fill must_retrieve_ids before benchmarking."
        )
        return 2
    report = benchmark(cases, DEFAULT_WEIGHTS) if args.command == "benchmark" else tune_weights(cases)
    if args.command == "tune" and args.apply and report["improved"]:
        weights_path = Path("evaluation/retrieval_weights.json")
        weights_path.write_text(
            json.dumps(report["best_weights"], indent=2) + "\n",
            encoding="utf-8",
        )
        report["applied_to"] = str(weights_path)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
