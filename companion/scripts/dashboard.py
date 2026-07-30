#!/usr/8in/env python3
"""Phase 5: Metrics Dashboard — Visual statistics aggregator for Amargon's Void."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def render_bar(value: float, max_val: float, width: int = 20) -> str:
    """Renders a simple ASCII bar chart."""
    if max_val <= 0:
        return " " * width
    filled = int((value / max_val) * width)
    return "█" * filled + "░" * (width - filled)


def get_dashboard_stats(db_path: str) -> dict[str, float]:
    """Connects to SQLite and computes dashboard statistics."""
    if not Path(db_path).exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    stats = {}
    
    # Graph size (Entities + Relations)
    cur.execute("SELECT COUNT(*) as c FROM entities")
    stats["entities_count"] = cur.fetchone()["c"]
    
    cur.execute("SELECT COUNT(*) as c FROM entity_relations")
    stats["relations_count"] = cur.fetchone()["c"]
    
    # Beliefs & Facts
    cur.execute("SELECT COUNT(*) as c FROM beliefs")
    stats["beliefs_count"] = cur.fetchone()["c"]
    
    cur.execute("SELECT COUNT(*) as c FROM facts")
    stats["facts_count"] = cur.fetchone()["c"]
    
    # Prediction Accuracy
    cur.execute("SELECT COUNT(*) as c FROM predictions WHERE outcome = 'correct'")
    correct_preds = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM predictions WHERE outcome IN ('correct', 'wrong')")
    total_preds = cur.fetchone()["c"]
    stats["prediction_accuracy"] = (correct_preds / total_preds * 100) if total_preds > 0 else 0.0
    
    # Retrieval metrics (Average from memory_access_log or retrieval_metrics if they existed, we use counts for now)
    cur.execute("SELECT COUNT(*) as c FROM memory_access_log")
    stats["total_retrievals"] = cur.fetchone()["c"]
    
    conn.close()
    return stats


def print_dashboard(stats: dict[str, float]) -> None:
    print("\n" + "="*50)
    print(" 🧠 AMARGON'S VOID — METRICS DASHBOARD")
    print("="*50)
    
    print("\n[ Graph & Memory Size ]")
    print(f"Entities:      {int(stats.get('entities_count', 0)):>6}  | {render_bar(stats.get('entities_count', 0), 1000)}")
    print(f"Relations:     {int(stats.get('relations_count', 0)):>6}  | {render_bar(stats.get('relations_count', 0), 2000)}")
    print(f"Facts:         {int(stats.get('facts_count', 0)):>6}  | {render_bar(stats.get('facts_count', 0), 5000)}")
    print(f"Beliefs:       {int(stats.get('beliefs_count', 0)):>6}  | {render_bar(stats.get('beliefs_count', 0), 500)}")
    
    print("\n[ Cognitive Performance ]")
    acc = stats.get('prediction_accuracy', 0.0)
    print(f"Prediction Accuracy:  {acc:>5.1f}%  | {render_bar(acc, 100)}")
    
    print(f"Total Retrievals:     {int(stats.get('total_retrievals', 0)):>6}")
    
    print("\n" + "="*50 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory OS Metrics Dashboard")
    parser.add_argument("--db", type=str, default="memory.db", help="Path to SQLite database")
    args = parser.parse_args()
    
    stats = get_dashboard_stats(args.db)
    print_dashboard(stats)
