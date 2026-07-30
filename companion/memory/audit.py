"""Memory Audit CLI for Phase C1.7: System-wide Memory Integrity Report."""
from __future__ import annotations

import sys
from collections import Counter
from companion.memory.store import MemoryStore
from companion.memory.verification import verify_projection_integrity


def run_memory_audit(store: MemoryStore | None = None) -> bool:
    """Runs a full audit of Memory OS components and prints a structured report.

    Returns True if replay verification passed, False otherwise.
    """
    if store is None:
        store = MemoryStore()

    all_facts = store.list_all_facts()
    all_events = store.events.get_all_events()

    # Fact metrics
    total_facts = len(all_facts)
    status_counts = Counter(f.status.lower() if f.status else "active" for f in all_facts)

    # Replay Verification
    verif_res = verify_projection_integrity(store)

    # Provenance & Confidence Health
    missing_origin = sum(1 for f in all_facts if not f.origin)
    missing_identity = sum(1 for f in all_facts if not f.identity_layer)
    invalid_confidence = sum(
        1
        for f in all_facts
        if not (0.0 <= f.confidence <= 1.0)
        or not (0.0 <= f.conf_observed <= 1.0)
        or not (0.0 <= f.conf_inferred <= 1.0)
        or not (0.0 <= f.conf_stability <= 1.0)
        or not (0.0 <= f.conf_verification <= 1.0)
    )

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 45)
    print("        Memory OS Integrity Report")
    print("=" * 45)
    print("Facts:")
    print(f"  total:       {total_facts}")
    for status_name, count in status_counts.items():
        print(f"  {status_name:<12} {count}")
    print()
    print("Events:")
    print(f"  total:       {len(all_events)}")
    print()
    print("Replay Verification:")
    status_str = "PASS [OK]" if verif_res.passed else "FAIL [FAIL]"
    print(f"  status:      {status_str}")
    print(f"  missing:     {len(verif_res.missing)}")
    print(f"  mismatched:  {len(verif_res.mismatched)}")
    if verif_res.missing:
        print("  -- missing items --")
        for m in verif_res.missing[:5]:
            print(f"     - {m}")
    if verif_res.mismatched:
        print("  -- mismatched fields --")
        for mm in verif_res.mismatched[:5]:
            print(f"     - ID {mm['id']} | Field {mm['field']}: SQLite={mm['sqlite_value']} vs Replay={mm['replay_value']}")
    print()
    print("Provenance & Confidence:")
    print(f"  missing origin:          {missing_origin}")
    print(f"  missing identity_layer:  {missing_identity}")
    print(f"  invalid confidence:      {invalid_confidence}")
    print("=" * 45)

    return verif_res.passed


def main() -> None:
    passed = run_memory_audit()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
