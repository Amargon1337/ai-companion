"""Consistent memory snapshots (Phase B / blueprint Phase 24).

Cognitive function: an offline, consistent copy of the ENTIRE memory (SQLite
WAL file + FAISS index file) at a point in time, so the organism can be rolled
back to a known-good state or cloned.

Guarantees:
  * ``VACUUM INTO`` produces a transactionally consistent single-file SQLite
    copy without stopping the writer (works on WAL DBs);
  * FAISS is copied only as a *cache* — it is always derivable from SQLite
    (Iron Law #4), so a restored snapshot simply marks the index dirty and
    lets ``_rebuild_index()`` reconstruct it at next boot;
  * restore writes the DB file, then flags the FAISS mapping as dirty;
  * snapshots are never deleted by the system itself (Iron Law #5).

Failure modes are surfaced as exceptions; callers (nightly task) log and
continue.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def snapshot_dir() -> str:
    from companion.config import SNAPSHOT_DIR
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    return SNAPSHOT_DIR


def create_snapshot(store: Any, *, snapshots_dir: str | None = None,
                    keep: int = 7) -> str:
    """Create a consistent snapshot (db + faiss cache) and return its name.

    ``keep`` bounds how many snapshots are retained (oldest removed via
    os.remove — snapshot retention is NOT long-term memory, it is ops hygiene;
    the DB itself never deletes memory rows).
    """
    import sqlite3
    out_dir = snapshots_dir or snapshot_dir()
    os.makedirs(out_dir, exist_ok=True)
    # Microseconds so rapid successive snapshots never collide (VACUUM INTO
    # refuses to overwrite an existing file).
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"snapshot_{ts}"

    db_path = os.path.join(out_dir, f"{name}.db")
    conn = store.db.conn
    # VACUUM INTO is a single consistent snapshot of the whole DB (WAL-safe).
    # It refuses to overwrite: remove a stale file with the same name first.
    if os.path.exists(db_path):
        os.remove(db_path)
    conn.execute(f"VACUUM INTO '{db_path}'")

    faiss_src = getattr(store.vector, "index_path", None)
    faiss_dst = ""
    if faiss_src and os.path.exists(faiss_src):
        faiss_dst = os.path.join(out_dir, f"{name}.bin")
        shutil.copy2(faiss_src, faiss_dst)

    manifest = {
        "name": name,
        "created_at": datetime.now().isoformat(),
        "db": os.path.basename(db_path),
        "faiss": os.path.basename(faiss_dst) if faiss_dst else "",
        "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
    }
    with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Ops hygiene: retain only the newest `keep` snapshots.
    _prune(out_dir, keep)
    logger.info("Snapshot created: %s (db=%s, faiss=%s)", name,
                manifest["db"], manifest["faiss"] or "<none>")
    return name


def _prune(out_dir: str, keep: int) -> None:
    try:
        names = sorted(
            n for n in os.listdir(out_dir)
            if n.startswith("snapshot_") and n.endswith(".json")
        )
        for old in names[:-max(1, int(keep))]:
            stem = old[:-5]  # strip ".json"
            for ext in (".json", ".db", ".bin"):
                p = os.path.join(out_dir, f"{stem}{ext}")
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
    except OSError as exc:
        logger.warning("Snapshot pruning failed: %s", exc)


def list_snapshots(snapshots_dir: str | None = None) -> list[dict[str, Any]]:
    """List existing snapshots (newest first)."""
    out_dir = snapshots_dir or snapshot_dir()
    results: list[dict[str, Any]] = []
    if not os.path.isdir(out_dir):
        return results
    for fn in sorted(os.listdir(out_dir), reverse=True):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(out_dir, fn), encoding="utf-8") as f:
                m = json.load(f)
            m["path"] = os.path.join(out_dir, f"{m['name']}.db")
            m["exists"] = os.path.exists(m["path"])
            results.append(m)
        except (OSError, ValueError, KeyError):
            continue
    return results


def restore_snapshot(name: str, *, snapshots_dir: str | None = None) -> str:
    """Restore a snapshot DB into the live SQLite path.

    IMPORTANT: call this only when the process can restart afterward — the
    live connection is not closed here. FAISS is marked dirty so the index is
    rebuilt from the restored DB at next boot (Iron Law #4: index is derived).
    """
    import sqlite3
    from companion.config import SQLITE_PATH
    out_dir = snapshots_dir or snapshot_dir()
    src = os.path.join(out_dir, f"{name}.db")
    if not os.path.exists(src):
        raise FileNotFoundError(f"Snapshot {name} not found in {out_dir}")

    # Atomic replace: write to temp, then os.replace. On Windows the live
    # connection keeps the target locked — restore is an OFFLINE operation
    # (callers close the store first). Stale WAL/SHM side files are removed
    # so the restored DB starts clean.
    for side in (SQLITE_PATH + "-wal", SQLITE_PATH + "-shm"):
        try:
            if os.path.exists(side):
                os.remove(side)
        except OSError:
            pass
    tmp = SQLITE_PATH + ".restore_tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, SQLITE_PATH)

    # Mark the FAISS index dirty so next boot rebuilds it from the restored DB.
    probe = __import__("companion.storage.sqlite_db", fromlist=["MemoryDatabase"]).MemoryDatabase(SQLITE_PATH)
    try:
        probe.set_meta("faiss_index_dirty", "1")
    finally:
        probe.close()

    logger.warning("Snapshot %s restored over %s; FAISS marked dirty (rebuild on boot)", name, SQLITE_PATH)
    return SQLITE_PATH
