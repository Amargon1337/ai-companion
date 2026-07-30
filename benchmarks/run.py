"""Benchmark Runner — discovers and executes all benchmark suites.

Usage:
    python -m benchmarks.run
"""
from __future__ import annotations

import json
import sys
import os
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def discover_benchmarks(base_dir: Path) -> list[dict]:
    """Discovers all .json benchmark fixtures in subdirectories."""
    benchmarks = []
    for json_file in sorted(base_dir.rglob("*.json")):
        if json_file.name.startswith("_"):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            data["_file"] = str(json_file.relative_to(base_dir))
            data["_suite"] = json_file.parent.name
            benchmarks.append(data)
        except Exception as e:
            print(f"  [!] Failed to load {json_file}: {e}")
    return benchmarks


def run_benchmark(bm: dict) -> dict:
    """Runs a single benchmark and returns a result dict."""
    name = bm.get("name", "unknown")
    suite = bm.get("_suite", "unknown")
    result = {"name": name, "suite": suite, "status": "pass", "details": ""}

    # Basic structural validation
    if "query" not in bm and "scenarios" not in bm:
        result["status"] = "skip"
        result["details"] = "No query or scenarios defined."
        return result

    # Validate expected fields exist
    if "expected_entities" in bm:
        if not isinstance(bm["expected_entities"], list) or len(bm["expected_entities"]) == 0:
            result["status"] = "fail"
            result["details"] = "expected_entities is empty or not a list."
            return result

    if "scenarios" in bm:
        for i, scenario in enumerate(bm["scenarios"]):
            if "query" not in scenario:
                result["status"] = "fail"
                result["details"] = f"Scenario {i} missing 'query'."
                return result

    result["details"] = f"Structural validation passed for '{name}'."
    return result


def main():
    base_dir = Path(__file__).parent
    print("\n" + "=" * 50)
    print("  AMARGON'S VOID -- BENCHMARK SUITE")
    print("=" * 50)

    benchmarks = discover_benchmarks(base_dir)
    print(f"\n  Discovered {len(benchmarks)} benchmarks.\n")

    results = {"pass": 0, "fail": 0, "skip": 0}
    for bm in benchmarks:
        res = run_benchmark(bm)
        status_icon = {"pass": "[OK]", "fail": "[FAIL]", "skip": "[SKIP]"}.get(res["status"], "?")
        print(f"  {status_icon} [{res['suite']}] {res['name']}: {res['details']}")
        results[res["status"]] += 1

    print(f"\n  Results: {results['pass']} passed, {results['fail']} failed, {results['skip']} skipped")
    print("=" * 50 + "\n")

    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
