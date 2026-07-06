"""CLI script to review all quarantined items pending review across the system."""
from __future__ import annotations

import json
import os
import sqlite3
import sys

# Add root folder to sys.path so we can import companion modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion.config import BASE_DIR, DATA_DIR, SQLITE_PATH


def review_quarantine() -> None:
    print("=" * 60)
    print(" QUARANTINE REVIEW - SYSTEM AUDIT ")
    print("=" * 60)

    # 1. Check SQLite Database
    if not os.path.exists(SQLITE_PATH):
        print(f"[SQLite] Database file does not exist at {SQLITE_PATH}.")
    else:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Facts
        cursor.execute("SELECT id, fact, date, importance FROM facts WHERE status = 'pending_review'")
        facts = cursor.fetchall()
        print(f"\n[SQLite] Facts pending review ({len(facts)}):")
        for f in facts:
            print(f"  • ID: {f['id']} | Date: {f['date']} | Imp: {f['importance']}\n    Content: {f['fact']}")

        # Reflections
        cursor.execute("SELECT id, insight, period, importance FROM reflections WHERE status = 'pending_review'")
        reflections = cursor.fetchall()
        print(f"\n[SQLite] Reflections pending review ({len(reflections)}):")
        for r in reflections:
            print(f"  • ID: {r['id']} | Period: {r['period']} | Imp: {r['importance']}\n    Content: {r['insight']}")

        # Beliefs
        cursor.execute("SELECT id, belief, importance FROM beliefs WHERE status = 'pending_review'")
        beliefs = cursor.fetchall()
        print(f"\n[SQLite] Beliefs pending review ({len(beliefs)}):")
        for b in beliefs:
            print(f"  • ID: {b['id']} | Imp: {b['importance']}\n    Content: {b['belief']}")

        conn.close()

    # 2. Check permanent_notes.pending_review.txt
    pending_notes_path = os.path.join(DATA_DIR, "permanent_notes.pending_review.txt")
    if os.path.exists(pending_notes_path):
        try:
            with open(pending_notes_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            print(f"\n[Files] Permanent Notes pending review ({len(lines)}):")
            for line in lines:
                print(f"  • {line}")
        except Exception as e:
            print(f"\n[Files] Error reading permanent_notes.pending_review.txt: {e}")
    else:
        print("\n[Files] No permanent notes quarantine file found.")

    # 3. Check world_model.json
    wm_path = os.path.join(DATA_DIR, "world_model.json")
    if os.path.exists(wm_path):
        try:
            with open(wm_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pending_contexts = data.get("pending_review_contexts", [])
            print(f"\n[Files] World Model contexts pending review ({len(pending_contexts)}):")
            for ctx in pending_contexts:
                print(f"  • {ctx}")
        except Exception as e:
            print(f"\n[Files] Error reading world_model.json: {e}")
    else:
        print("\n[Files] No world_model.json file found.")

    print("\n" + "=" * 60)
    print(" REVIEW COMPLETED ")
    print("=" * 60)


if __name__ == "__main__":
    review_quarantine()
