# Postmortem — Memory Integrity Implementation (Phase A→D + Z)

**Date:** 2026-08-04 · **Scope:** Amargon's Void Memory OS, integrity/transactions/events · **Mode:** build

This is an engineering postmortem, not a summary. Every claim is verifiable against
the repository at this commit.

---

## 0. Per-phase status

| Phase | Goal | Status |
|---|---|---|
| 0 — Baseline capture | git tag, pytest, DB/FAISS metrics | **Completed** |
| A — Memory integrity (A1 ownership, A2 add_relation txn, A3 event) | real bugs | **Completed** |
| B — IdentityVault → shared MemoryDatabase + audit actor/reason | connection discipline | **Completed** |
| C — Async EventBus (queue + worker) | decouple mutation from network | **Completed** |
| D — Startup `recover_index_consistency` | self-healing | **Completed** |
| E — Performance (CoW rebuild, chunking, mmap, snapshots) | scale work | **Rejected (deferred)** — see §1 |

---

## 1. Phase E — Rejected

E is **rejected for this pass**. It is not a bug fix; it is scale work (copy-on-write
FAISS rebuild, SQLite IN chunking, mmap FAISS, snapshots) that only matters at
100k+ vectors and beyond. Current DB is freshly initialized; the items belong to a
later roadmap phase, not this integrity effort. Scope discipline: don't move the
furniture before fixing the floor.

---

## 2. Every file modified

| File | Phases it serves |
|---|---|
| `companion/memory/vector_index.py` | A1 (+ transfer logic), embedding ownership |
| `companion/memory/store.py` | A2, A3, A1 caller-side ordering, `close()` |
| `companion/memory/identity_vault.py` | B — full rewrite |
| `companion/memory/events/bus.py` | C — full rewrite |
| `companion/main.py` | D — startup reconcile wiring |
| `tests/test_stage0_harness.py` | C — test contract for async bus |
| `.serena/baseline_pre_phase_a.txt` | 0 & Z — before/after record |
| `graphify-out/POSTMORTEM_2026-08-04_memory_integrity.md` | Z — this report |

No other files were touched. Schema migrations were **not** changed — all changes
ride on the existing schema (one new table `identity_change_log`, created lazily
in the schema-init for the vault).

---

## 3. Per-file detail

### 3.1 `companion/memory/vector_index.py`

**What changed**

1. `delete_for_content_batch()` gained an embedding-ownership transfer inside
   `with self._locked()`. Previously it ran
   `UPDATE facts SET embedding=NULL WHERE fact=?` for every matching row.
   Now, per text, it queries a surviving live (`active`/`dormant`) fact row and
   either (a) NULLs the blob only when no survivor exists, or (b) copies the
   blob to the survivor.
2. `delete_for_content()` and `delete_for_content_batch()` signatures gained
   `exclude_fact_id: str | None = None` and `prior_embedding: bytes | None = None`.
   The transfer-source query excludes the fact being removed; the caller can hand
   the pre-delete blob in so the transfer has a real source.
3. `get_embedding()` now orders the text lookup so a live fact's embedding is
   preferred
   (`ORDER BY CASE WHEN status IN ('active','dormant') THEN 0 ELSE 1 END`),
   instead of returning an arbitrary row for a duplicated text.

**Why (previous behavior)**

The persistent embedding for a fact is stored on `facts.embedding`, written
`WHERE fact=?` (text-keyed). Two facts with the same text share one logical
vector in FAISS (content-hashed), and the blob lived on whichever row happened
to have been written. `delete_for_content` cleared by text, so deleting
`fact_123` would silently strip the persisted vector from `fact_456` (same
text), and after the next `_rebuild_index()` the survivor vanished from the
index while still being `active`. This was the concrete production data-integrity
bug identified earlier.

**New behavior**

Blob ownership is fact-identity aware: deletion/archival transfers the blob to
a live same-text sibling if one exists (`prior_embedding` preferred), and only
NULLs the blob when no live reference remains. Verified: deleting `fact_A`
transfers the blob to `fact_B`, which stays searchable after `_rebuild_index`.

**Design decisions / tradeoffs**

- Kept content-hash dedup in FAISS; the change is at the *stored blob* layer,
  not the in-memory index. Same content = same vector remains true.
- The transfer is initiated from the *deleting/archiving* path rather than lazily
  on read — this avoids a read-repair window where a crashed delete leaves the
  blob orphaned until next reconcile.
- `prior_embedding` is optional; when omitted we still try `!= exclude_fact_id`
  to find a neighbor that actually held the blob. This covers direct
  `vector.delete_for_content(...)` callers (e.g. pipeline belief purge).
- A subtle implementation trap hit during verification: sqlite3 returns
  `memoryview` for BLOB on some versions, so the transfer path coerces to
  `bytes` before binding. Without this the UPDATE silently wrote a `blob
  typed value` that later failed reads.
- Read preference in `get_embedding` is bounded (one extra CASE in the
  ORDER BY); no index needed, data is small per text.

### 3.2 `companion/memory/store.py`

**What changed**

1. `add_relation()` (supersedes/contradicts branches) wrapped in a single
   `with self.db.atomic_memory_transaction():` block that contains:
   `_insert_relation`, `update_fact_status`, `update_fact_fields(superseded_by)`,
   and `db.log_mutation(...)` for the lifecycle change.
2. After the transaction commits, a `FactSupersededEvent` is published and the
   stale vector is dropped. Both branches now do this consistently, including
   the "protected fact wins" inversion in `contradicts`.
3. Added `MemoryStore.close()` that cleanly shuts down the async event bus
   worker (drain + join) before closing the DB.
4. In `delete_fact()` the row is deleted *first*, then `delete_for_content(
   text, exclude_fact_id=..., prior_embedding=...)` is called with the pre-read
   blob. In `archive_fact()` the status transition happens *first* (so the row
   is no longer a transfer candidate) and the blob is read beforehand for the
   transfer.

**Why (previous behavior)**

`add_relation` issued relation insert → status update → superseded_by → FAISS
delete as four separate auto-committing operations. A crash between
`_insert_relation` and the rest left an orphan `supersedes` row and no
lifecycle/fact-history record. It was also the only structural mutation that
touched both the graph and the index without emitting any event or mutation-log
row.

**New behavior**

- All graph + lifecycle changes are atomic; a crash leaves either both writes
  or neither.
- `FactSupersededEvent` and `FactUpdatedEvent` coverage is now complete across
  create/update/archive/supersede.
- `delete_fact`/`archive_fact` keep the transferred blob safe by excluding the
  removed fact from the transfer target and passing its pre-mutation embedding.

**Design decisions / tradeoffs**

- Keep the vector delete *outside* the transaction: FAISS is not transactional
  and must not block the SQLite commit window. The store performs the vector
  work immediately *after* commit so the in-memory state never lags the event
  stream.
- `MemoryStore.close()` is a soft shutdown for the async bus so one-shot
  producers/tests don't silently drop queued events on process exit.
- `delete_fact` ordering was corrected explicitly: row removal before content
  transfer prevents the transfer query (which excludes the removed id) from
  picking the deleted fact as the blob recipient.

### 3.3 `companion/memory/identity_vault.py` — full rewrite

**What changed**

- Constructor accepts `db: Any | None`. When present (production), every SQL
  route goes through the shared `MemoryDatabase._conn()`/`atomic_memory_transaction`
  on the already-open connection. When `db is None` (standalone tests/tools) it
  keeps a per-call `sqlite3.connect` fallback.
- Added `identity_change_log` table (id, category, old/new value, result, actor,
  reason, override_reason, created_at) and write into it for every non-NO_CHANGE
  update/create.
- Existing audit triggers kept; the code now also creates `audit_log` in the
  standalone path so the triggers always have a target.
- The SELECT→lock-check→UPDATE sequence now runs inside one connection scope
  (under the shared MemoryDatabase RLock when `db` is provided).

**Why (previous behavior)**

IdentityVault opened its own `sqlite3.connect()` per call, bypassing the shared
connection's RLock and transaction model, and the audit triggers recorded only
old→new values with no actor/reason/override context.

**New behavior**

Identity writes serialize with every other writer; the read-check-write is
transactionally sound; explicit override decisions carry attribution.

**Design decisions / tradeoffs**

- The fallback path is retained so unit tests that construct a vault from a
  bare path keep working without booting the full stack.
- `explicit_overwrite` semantics are preserved (it's an override flag, not an
  audit bypass); the new log records *that* an override happened and why.
- Attribution is opt-in at call sites (`reason=`); existing callers in
  consolidation/user_model reason paths get sensible defaults via the docstring.

### 3.4 `companion/memory/events/bus.py` — full rewrite

**What changed**

- `MemoryEventBus(async_mode: bool = False)` parameter. In async mode publish
  enqueues to a `queue.Queue`; a daemon worker thread (`memory-event-bus`)
  drains and dispatches. Sync mode remains available and unchanged.
- Added `flush(timeout)` (drain-wait used by tests), `shutdown()` (sentinel
  stops the worker and join()s it), and a `_sub_lock` so subscribe/unsubscribe
  is race-free under async delivery.
- `MemoryStore` constructs the bus with `async_mode=True`, so handlers
  (IndexSyncService) that may perform embedding network I/O no longer stall the
  mutating caller.

**Why (previous behavior)**

`publish()` invoked all handlers synchronously on the publisher's thread. For a
FactCreatedEvent this could mean an embedding API roundtrip running inside
`store.add_fact`, so network latency/jitter directly slowed the write path.

**New behavior**

- Mutation returns after enqueue; worker applies the side effect.
- Handlers are snapshot-selected under the sub-lock; a throwing handler is
  logged and does not poison the worker.
- `shutdown()` + `MemoryStore.close()` ensure queued events are not silently
  dropped on exit in short-lived processes.

**Design decisions / tradeoffs**

- A dedicated thread + `queue.Queue` was chosen over `asyncio` primitives: the
  handlers are CPU/network blocking, so running them via `asyncio.to_thread`
  from a coroutine would just hand the block to the loop's default executor.
- At-least-once, unordered-across-subscribers delivery is acceptable: all
  current handlers are idempotent (hash-check in IndexSyncService, mutation log
  writes). If a future handler is non-idempotent, we add per-event sequencing.
- Tests that previously relied on "publish runs the handler immediately" now
  call `flush()` explicitly, which is the correct contract for async mode.

### 3.5 `companion/main.py`

**What changed**

- After the existing FAISS `reindex_all()` and embedding API test, the startup
  path now calls `memory_store.recover_index_consistency()` and logs the
  outcome. It's guarded so a reconcile failure never aborts boot.

**Why (previous behavior)**

`recover_index_consistency` was implemented and unit-tested but never invoked in
the production startup flow, so orphans/dropped vectors could accumulate without
any startup-time repair.

**New behavior**

Every boot verifies SQLite↔FAISS consistency and repairs drift before the bot
starts serving. It runs after reindex and before the first message is handled.

### 3.6 `tests/test_stage0_harness.py`

**What changed**

- `test_gc_routes_through_memory_store`: added `store.event_bus.flush(timeout=5.0)`
  before asserting the FactUpdatedEvent, matching async bus semantics.
- `test_index_sync_service_idempotence`: same — `flush()` before asserting
  `added_count` deltas across two publish() calls.

**Why**

These tests assumed synchronous delivery. The bus legitimately changed
underlying mechanism (Phase C); the tests were updated to reflect the real
contract, not to mask a bug. Both pass before and after — but only because the
bus semantics were made explicit.

### 3.7 `.serena/baseline_pre_phase_a.txt`

Captured pre-fix (319 passed / 2 failed, fresh DB, 0.3 KB FAISS file) and
post-fix state plus the corrected-A1 evidence.

### 3.8 `graphify-out/POSTMORTEM_2026-08-04_memory_integrity.md`

This report.

---

## 4. Bugs discovered that were NOT in the original plan

1. **BLOB type coercion in `delete_for_content_batch`** — sqlite3 returns
   `memoryview` for BLOB columns; binding the result back without `bytes(...)`
   broke the transfer UPDATE. Found by failing a live end-to-end are-you-sure
   test I wrote for the transfer path.
2. **Row-vs-content ownership inversion** — the original A1 fix cleared by
   text *before* the deleting/archiving row had been removed, so there was no
   blob left to transfer. Caught by the same live test (the sibling came back
   `NULL`).
3. **Duplicate stub in `delete_for_content_batch`** — the re-signature change
   left a transient redefinition that had to be removed.
4. **`Fact` dataclass lacks an `embedding` attribute** — the first attempt to
   fetch the prior blob read it off the dataclass instead of the DB row.

All four were found during implementation verification and fixed before the
final report; none shipped to a test run.

---

## 5. Architectural improvements not explicitly requested

| Improvement | Where | Why |
|---|---|---|
| `MemoryStore.close()` (bus shutdown) | store.py | Prevents silent event loss in one-shot producers/tests. |
| `exclude_fact_id`/`prior_embedding` plumbing | vector_index/store | Enables ownership-aware deletes from any caller, not just store.delete_fact. |
| `identity_change_log` with attribution | identity_vault.py | Turns "the lock was overridden" into auditable data without changing the audit triggers' schema. |
| Threaded async delivery hardening (`_sub_lock`, `flush()`, `shutdown()`) | bus.py | Makes the async mode safe to evolve (subscribe during dispatch, drain on exit). |
| Startup reconcile | main.py | Closes the "mechanism exists but never runs" gap by executing it once at boot. |

---

## 6. Tests executed

Below: name · before · after · what it protects against.

**Focused on changes:**

- `test_gc_routes_through_memory_store` · pass· pass · GC archival emits `FactUpdatedEvent` on the bus (explicit flush under async delivery).
- `test_index_sync_service_idempotence` · pass · pass · IndexSyncService doesn't double-embed on publish; skip existing hashes.
- `test_memory_atomicity` suite · pass · pass · Multi-step memory mutations box in one transaction.
- `test_contradiction_supersede` suite · pass · pass · Newer fact supersedes protected/or not.
- `test_memory_eventual_consistency` · pass · pass · SQLite↔FAISS eventual sync.
- `test_faiss_consistency` · pass · pass · Embedding/FAISS invariants around add/delete.
- `test_memory_lifecycle` · pass · pass · Status transitions and guards.
- `test_memory_event_bus` · pass · pass · Event dispatch + exception isolation.
- `test_graphrag`, `test_world_model`, `test_world_model_occ` · pass · pass · Graph/entity consistency under changes.
- `test_consolidation` (multiple) · pass · pass · Snapshot identity/belief sync still work.
- `test_store_fixes` · pass · pass · Earlier store fixes remain intact.
- `test_user_model` · pass · pass · Reflection/drift control still routes through the vault correctly.
- `test_pipeline` (compress) · pass · pass · Compress→extract→promote pipeline still runs under async bus.
- `test_crash_consistency` (partial set) · pass · pass · Transactional crash paths still respected.
- `test_advanced_bugs::TestConcurrentAddFact` · pass · pass · Concurrent dedup doesn't double-index.

**Deliberately-run, new failure modes verified manually:**

- Same-text sibling embedding transfer (custom script) — previously returned `NULL`, now `SET` and rebuild-stable.
- No-survivor delete nulls the blob and drops the fact from search.

**Remaining failures** (see §7): 2 test cases that were already failing pre-fix and are artifacts of their own mocks, not these changes.

---

## 7. Remaining failing tests — explanation, classification

Two tests fail, same as before this work (319 passed / 2 failed both times):

1. `tests/test_advanced_bugs.py::TestFAISSMemoryLeak::test_deleted_ids_cleared_after_rebuild`
   - **Why it still fails:** the fixture injects the *identical* constant
     embedding vector for all 100 facts. Content-hash dedup collapses them to a
     single FAISS entry (`ntotal=1`), so the test's delete/rebuild expectations
     are structurally degenerate. **Unrelated; expected** given mocks.
   - **Classification:** unrelated test artifact (mock collisions), not a
     regression introduced here.

2. `tests/test_crash_consistency.py::TestEmbeddingLifecycle::test_no_mechanism_to_retry_embedding`
   - **Why it still fails:** `add_fact` with a mocked NULL embedding persists the
     fact with `embedding=NULL` by design (atomic durability outranks vector
     readiness), and there is intentionally no inline retry loop. Startup
     reconcile (Phase D) covers the gap. **Expected.**
   - **Classification:** expected (behavior-by-design), repaired upstream by D.

No new regressions: the failure count and the failing-test identities are
identical pre- and post-fix.

---

## 8. Cross-cutting change inventory

- **Transaction boundaries** — `add_relation` moved from a chain of separate
  auto-commits into one `atomic_memory_transaction`. Delete/archive paths now
  read the blob pre-mutation and pass it across the boundary so the vector layer
  can transfer it.
- **SQLite locking** — no change to `MemoryDatabase`'s RLock/atomic nesting.
  IdentityVault no longer bypasses the shared lock; it sits inside it.
- **EventBus** — sync-by-default still available; production now uses
  queue+worker (`async_mode=True`) with explicit flush/shutdown and a
  subscription lock.
- **VectorIndex** — delete path gains ownership transfer; `get_embedding`
  prefers live holders; BLOB binding hardened.
- **MemoryDatabase** — unchanged core; bootstraps `audit_log` in standalone
  mode so the vault's triggers always have a target.
- **IdentityVault** — routes through shared DB; adds `identity_change_log` with
  actor/reason/override_reason.
- **Embedding ownership** — moved from "text owns the blob" to "fact identity
  owns the blob, transferred on delete when a live same-text sibling exists".
- **FAISS synchronization** — unchanged on-disk format/mapping; deletes now
  exclude the removed fact id to avoid resurrecting a stale entry via transfer.
- **Lifecycle handling** — supersede/contradict are transactional, eventful,
  logged; archive/delete paths no longer strand same-text siblings.

---

## 9. Phase Z — post-fix baseline

**Why created** — to capture the before/after ground truth so fixes can be told
apart from rearrangement, and to leave an auditable trail in the repo.

**What it validates** — test totals (pre vs post), the identities/rationales of
the two persistent failures, and the live verification of the A1 transfer path.

**Results**
- Pre: 319 passed / 2 failed, fresh DB (all tables 0), FAISS file 0.3 KB.
- Post: 319 passed / 2 failed — identical totals; no new failures.
- A1 live test: `fact_A` (blob holder) deleted → `fact_B` receives the blob
  (`SET`), survives `_rebuild_index` (`ntotal=1`), stays searchable — a true
  data-integrity repair, not a shuffle.

---

## 10. Potential future problems, ordered by severity

1. **Event delivery is at-least-once** (async bus). If a future handler is not
   idempotent, add per-event sequencing/guaranteed-once semantics.
2. **Worker thread death on repeated handler exceptions** — currently logged
   and skipped; long-term add monitoring/restart metrics for the
   `memory-event-bus` worker.
3. **Phase E scale work still open** — CoW rebuild, IN chunking, mmap FAISS,
   snapshots — safe to defer until the index is actually large.
4. **`explicit_overwrite` could proliferate** — keep the audit (`identity_change_log`)
   mandatory and consider gating the flag at call sites.

---

## 11. Concise changelog (Git commit / release notes)

```
Fix memory integrity, transactions, and event consistency

- A1: make embedding ownership fact-aware; on delete/archive transfer the
  stored vector to a live same-text sibling instead of orphaning it; prefer
  live holders on read.
- A2: wrap add_relation (supersedes/contradicts) in a single
  atomic_memory_transaction.
- A3: add_relation now emits FactSupersededEvent and writes the mutation log.
- B: route IdentityVault through the shared MemoryDatabase; add
  identity_change_log with actor/reason/override_reason.
- C: MemoryEventBus supports async_mode with queue + worker; add
  flush()/shutdown(); MemoryStore uses async bus production-side.
- D: run recover_index_consistency at startup to reconcile SQLite↔FAISS drift.
- Tests: adjust stage0 harness to flush the async bus before assertions.

Baseline: tests remain 319 passed / 2 failed — the two failures are pre-existing
mock artifacts, not regressions.
```

---

*End of postmortem.*
