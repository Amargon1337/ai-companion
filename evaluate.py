"""Run deterministic and replay-based quality evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.runner import run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate companion memory and retrieval quality")
    parser.add_argument("--data", type=Path, default=Path("evaluation"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--baseline", type=Path, default=Path("evaluation/baseline.json"))
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--summary", action="store_true", help="Print a compact release-style metric summary")
    args = parser.parse_args()

    baseline = None
    if args.baseline.exists() and not args.write_baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    report = run_evaluation(args.data, db_path=args.db, baseline=baseline)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.summary:
        metrics = report["metrics"]
        print(f"Memory Recall: {metrics['memory_recall']:.1%}")
        print(f"Retrieval Precision: {metrics['retrieval_precision']:.1%}")
        print(f"Average Latency: {metrics['average_latency_ms']:.2f} ms")
        print(f"Average Tokens: {metrics['average_tokens']:.0f}")
        print(f"Regression: {report['comparison']['regressions']}")
    else:
        print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.write_baseline:
        args.baseline.write_text(json.dumps(report["metrics"], indent=2) + "\n", encoding="utf-8")
    if args.fail_on_regression and report["comparison"]["regressions"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
