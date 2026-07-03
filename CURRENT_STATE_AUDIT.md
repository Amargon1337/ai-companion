## CURRENT STATE AUDIT

Baseline audit of the current project state based only on the code currently present in the repository.

- Date: 2026-07-03
- Scope: `companion/**/*.py`, `tests/**/*.py`, root runtime/config files
- Method: static code audit, cross-reference search, runtime-path tracing
- No code changes included in this audit

---

## 1. What Really Exists

### 1.1 Runtime Entry and Main Control Flow

The real runtime entrypoint is `companion/main.py`.

Observed startup flow:

1. Logging is configured.
2. Selected JSONL logs are rotated on startup.
3. `memory_store` is imported from `companion/bot_core.py`.
4. `timeline.jsonl` is migrated into SQLite timeline rows if the file still exists.
5. Vector index reindexing is attempted via `memory_store.reindex_all()`.
6. Embedding API is tested; vector retrieval can be disabled at startup.
7. Telegram `Bot` and `Dispatcher` are created.
8. Handlers are registered from `companion/handlers`.
9. Background proactive ping loop is started.
10. Polling is started with aiogram.
11. On shutdown, the ping task and tracked background tasks are cancelled.

Relevant files:

- `companion/main.py`
- `companion/handlers/__init__.py`
- `companion/bot_core.py`
- `companion/background_scheduler.py`

### 1.2 Actual Runtime Core

The real orchestration center is `companion/bot_core.py`.

It owns:

- Global singletons:
  - `memory_store = MemoryStore()`
  - `retrieval_mgr = RetrievalBudgetManager()`
- In-memory chat sessions:
  - `user_chats`
  - `user_message_counts`
- Rate limiting state
- Compression locks
- Request preprocessing
- Context loading
- Command routing
- LLM response generation
- Compression trigger and reset flow
- Background reflection scheduling

This is not a thin utility module. It is the actual application core.

### 1.3 Telegram Ingress Layer

Two handler modules are actively registered:

- `companion/handlers/chat.py`
- `companion/handlers/media.py`

Actual live ingress paths:

- `/start`
- `/help`
- `/summary`
- `/personality`
- `/remember`
- `/search`
- ordinary text messages
- voice messages
- documents
- photos/stickers
- video/video_note
- TikTok links
- callback confirmations for destructive LLM-routed commands

### 1.4 Service Layer

There is a real service layer, but it is narrow and command-oriented:

- `companion/services/memory_service.py`
- `companion/services/reasoning_service.py`
- `companion/services/report_service.py`

These services are used by `bot_core._route_command()` and some direct command handlers.

### 1.5 LLM Subsystem

The LLM layer is split across these active modules:

- `companion/llm/client.py`
- `companion/llm/analyzer.py`
- `companion/llm/sessions.py`
- `companion/llm/pipeline.py`
- `companion/llm/master_summary.py`
- `companion/llm/shadow_eval.py`
- `companion/llm/prompts.py`

Actual usage by role:

- `analyzer.py`: structured analysis of incoming messages
- `sessions.py`: system instruction assembly and session creation
- `pipeline.py`: compress pipeline and memory consolidation
- `master_summary.py`: post-compress long-term summary maintenance
- `shadow_eval.py`: validation of core identity drift proposals
- `client.py`: sync and async Gemini wrappers, structured output models, search grounding, upload helpers

### 1.6 Memory Subsystem That Actually Exists

Primary runtime memory stack:

- `companion/memory/store.py` -> high-level memory facade
- `companion/storage/sqlite_db.py` -> primary persistence for facts/messages/reflections/beliefs/summaries/timeline/meta/sessions/retrieval metrics/audit log
- `companion/memory/vector_index.py` -> embedding table + in-memory FAISS-like HNSW index
- `companion/memory/identity_vault.py` -> protected identity facts table in SQLite
- `companion/user_model.py` -> separate long-lived user model stored in SQLite meta

Actual stored domains:

- facts
- fact relations
- messages
- reflections
- beliefs
- summaries
- timeline events
- sessions
- retrieval metrics
- meta values including personality, user_model, master_summary, counters
- identity facts in dedicated `identity_facts`

### 1.7 Legacy / File-Based Subsystems Still Alive

These file-based systems are still live in runtime:

- `companion/storage/legacy.py`
- `companion/storage/jsonl.py`
- `companion/reasoning.py`
- `companion/self_model.py`

They are not just leftovers; some are still used for real runtime data.

Current file-backed data still active:

- `ivan.txt` static persona
- diary text file
- permanent notes text file
- todo JSON file
- monthbook text files
- reasoning JSON/JSONL files:
  - goals
  - world model
  - causal links
  - predictions
- self model JSON
- self errors JSONL
- policy decisions JSONL
- user model updates JSONL

### 1.8 Reasoning Subsystem

`companion/reasoning.py` is a live subsystem with its own persistence model.

It maintains:

- goals
- world model
- causal links
- predictions

Storage is file-based, not SQLite-based.

This subsystem is actively queried by:

- `companion/bot_core.py`
- `companion/llm/sessions.py`
- `companion/llm/pipeline.py`
- `companion/services/reasoning_service.py`

### 1.9 Background Processing

There are two active background mechanisms:

1. `proactive_ping_loop()` in `companion/bot_core.py`
2. background task scheduler in `companion/background_scheduler.py`

Tracked background jobs:

- user model reflection
- personality micro-update

### 1.10 Test Surface That Exists

The repo has active tests for:

- background scheduler
- critique manager
- FAISS behavior
- grounding handler
- pipeline
- policy layer
- retrieval
- structured parsing

There are no visible tests for:

- `user_model.py`
- `identity_vault.py`
- `rollback.py`
- `documents.py`
- handler-level media flows
- master summary update path

---

## 2. Fact-Based Architecture Map

### 2.1 Text Request Flow

Real control flow for normal text:

1. `handlers/chat.py:text_handler`
2. `bot_core.process_llm_request()`
3. `bot_core.build_context()`
4. `llm.analyzer.analyze_message()`
5. `memory_store.log_message()`
6. `reasoning_engine.auto_reasoning_context()`
7. `memory_service.auto_add_event_from_message()`
8. `policy_layer.decide_policy()`
9. `bot_core._load_retrieval_context()`
10. session init via `llm.sessions.create_default_session()` if needed
11. `background_scheduler.run_background_tasks()`
12. optional compression if threshold reached
13. command route OR grounding route OR normal response route
14. `bot_core._generate_and_send_response()`
15. `llm.run_llm(chat.send_message, ...)`
16. critique and optional grounding retry
17. assistant message persistence
18. optional retrieval metrics write
19. optional background reflection

### 2.2 Search Flow

Two actual search-related paths exist:

1. explicit `/search` path in `handlers/chat.py`
2. grounded fallback/retry path via `grounding_handler.py`

Both use `llm.search_with_grounding()` via `llm.run_llm(...)`.

### 2.3 Compression Flow

Real compress path:

1. `bot_core.compress_and_reset()`
2. `llm.pipeline.run_compress_pipeline()`
3. summary generation via chat session
4. `store.save_summary()`
5. fact extraction
6. relation consolidation
7. causal link extraction
8. optional reflections every `REFLECTION_EVERY_N`
9. personality snapshot generation
10. importance decay
11. retrieval effectiveness tuning
12. master summary update
13. new chat session seeded with latest summary

### 2.4 System Prompt Assembly

Two prompt assembly layers exist and both are active:

1. `llm/sessions.py:build_system_instruction()`
2. `models.ContextBundle.to_prompt_block()` via `retrieval.select()`

Prompt input sources currently merged:

- IdentityVault block
- personality snapshot
- UserModel block
- permanent notes
- recent messages
- active goals
- causal links
- predictions
- world model context
- facts
- reflections
- summaries
- static `ivan.txt`
- optionally master summary

---

## 3. What Is No Longer Used

This section is restricted to code paths that are unreferenced or structurally bypassed by current runtime.

### 3.1 Dead Module: Rollback

`companion/memory/rollback.py` defines `RollbackManager`, but there is no live import or invocation in runtime or tests.

Observed state:

- present
- not wired into commands
- not wired into startup
- not tested

Status: effectively dead code

### 3.2 Dead Shadow Path: Unified Profile

`companion/memory/unified_profile.py` exists, and `unified_profile_block` is still part of:

- `companion/memory/retrieval.py`
- `companion/models.py`
- `companion/bot_core.py`

But no producer currently populates `ctx_data["unified_profile_block"]`, and no runtime construction of `UnifiedProfile(...)` is present.

Status: dead execution path with leftover interface hooks

### 3.3 Obsolete Intent Module

`companion/intents.py` explicitly states it was replaced by the LLM analyzer and contains no live logic.

Status: compatibility placeholder, not part of runtime behavior

### 3.4 Unused Legacy Summary API

In `companion/storage/legacy.py`, these methods exist but are not part of the active summary path:

- `save_summary()`
- `load_latest_summary()`
- `load_all_summaries()`
- `load_master_summary()`
- `save_master_summary()`

Active summary and master summary operations now go through `MemoryStore` + SQLite meta.

Status: stale compatibility helpers

### 3.5 Likely Unused Async Wrapper Variants

In `companion/llm/client.py`, these functions exist but are not used by current runtime paths:

- `aio_search_with_grounding()`
- `async_search_with_grounding()`

Current runtime uses `run_llm(search_with_grounding, ...)` instead.

Status: probably dead helpers

### 3.6 Likely Unused Legacy Utility Methods

These methods exist in `companion/storage/legacy.py` but are not visibly used in live runtime paths:

- `save_mood()`
- `load_mood()`
- `count_permanent_notes()`
- `count_diary_entries()`

Status: likely dead utilities

---

## 4. What Is Broken

### 4.1 `show_selfmap()` References Missing Data Shape

`companion/services/reasoning_service.py:show_selfmap()` reads `self_model.data["knowledge_map"]`.

But `companion/self_model.py` defines `knowledge_domains`, not `knowledge_map`.

Observed consequence:

- calling this route can raise `KeyError`

Status: actually broken runtime path

### 4.2 Rollback Logic Does Not Match Current Schema Completeness

`companion/memory/rollback.py` only partially restores:

- facts
- identity_facts
- embeddings cleanup

But current memory system also depends on:

- fact relations
- summaries
- beliefs
- reflections
- retrieval metrics
- sessions
- meta counters
- user_model
- personality meta
- master_summary meta
- reasoning files

It also manually triggers `memory_store.vector._load_index()` through a global import from `bot_core`.

Status: functionally incomplete and unsafe if activated

### 4.3 ShadowEvaluator Protection Is Not Final Authority

`companion/user_model.py` runs shadow evaluation only for `who_they_are`.

Then later the method writes multiple identity-derived fields into `IdentityVault` using `explicit_overwrite=True`:

- `core_identity`
- `ambitions`
- `fears`
- `core_traits`
- `values`
- `roles`

Observed implications:

- ShadowEvaluator protects only one field
- other identity-like fields bypass equivalent validation
- IdentityVault overwrite safeguards are bypassed intentionally by explicit overwrite

Status: safety model is partially implemented and internally inconsistent

### 4.4 `documents.py` Still Exposes Raw Exception Text to User

`companion/documents.py` returns `await message.answer(f"Ошибка файла: {e}")`.

Observed implication:

- raw internals may leak to the user

Status: broken error hygiene

### 4.5 `sqlite_db.py` Docstring Is No Longer Accurate

The file claims SQLite is used as primary store with JSONL mirror.

Observed reality:

- memory domain is mostly SQLite primary
- reasoning is separate JSON/JSONL primary
- no general JSONL mirror for facts/messages/reflections/beliefs is active in runtime

Status: architecture description is stale and misleading

---

## 5. What Is Potentially Broken

### 5.1 Permanent Memory Has Split Source of Truth

Saving a permanent note in `memory_service.remember_text()` does two writes:

1. appends to permanent notes text file
2. creates a permanent `Fact` in SQLite/vector memory

Retrieval and prompting still inject permanent notes separately from facts.

Potential consequences:

- duplication in prompts
- divergence between file notes and fact store
- one path can succeed while the other fails
- deletion/update semantics are undefined across the two stores

### 5.2 Personality Also Has Split Historical Model

Current intended source is SQLite meta via `MemoryStore.load_personality()`.

But legacy migration from `personality.json` still exists and `config.py` bootstraps that file if absent.

Potential consequences:

- confusing bootstrap artifact recreation
- ambiguous operational source during partial migrations

### 5.3 Summaries and Master Summary Mix Old and New Assumptions

Current active summary storage is SQLite summaries table.
Current active master summary storage is SQLite meta.

But:

- startup and legacy code still assume historical file migrations
- `master_summary.txt.bak` is still produced in `llm/master_summary.py`
- legacy summary helpers still exist

Potential consequences:

- operator confusion
- accidental reuse of stale file-based data

### 5.4 Reasoning Context Is Operationally Separate From Main Memory

Reasoning state is file-based and not transactionally tied to the SQLite memory stack.

Potential consequences:

- no atomic consistency between facts and goals/causal links/predictions/world model
- backup/restore semantics differ by subsystem
- rollback, migration, and integrity checks cannot treat memory as one unit

### 5.5 `_compression_locks` Is Never Cleaned

`bot_core.py` creates per-user `asyncio.Lock` objects stored in `_compression_locks`.

No cleanup path exists.

Potential consequence:

- unbounded growth over long-lived operation

### 5.6 Background Circuit Breaker Is Partial

`background_scheduler.py` has breaker accounting for background tasks, but `safe_task()` itself does not integrate breaker state; breaker checks are only inside selected task bodies.

Potential consequence:

- task objects can still be created repeatedly even if task logic early-exits

### 5.7 `ReasoningEngine._save_world_model()` Can Skip Saves Under Burst Updates

It rate-limits writes by 10 seconds and simply returns.

Potential consequence:

- recent updates inside the window can remain only in memory and be lost on crash before next save

### 5.8 `UserModel.reflect_after_interaction()` Mixes Async, Blocking DB Writes, and Cross-System Sync Under One RLock

Inside a `threading.RLock`, the method:

- mutates model state
- saves model through new `MemoryDatabase()` instance
- appends reflection JSONL log
- writes back to `IdentityVault`

Potential consequences:

- coroutine interleaving is not prevented by `threading.RLock`
- long critical section with blocking I/O
- multiple persistence targets updated without atomicity

### 5.9 FAISS / Embeddings Only Reflect Text Presence, Not Full Fact Lifecycle

Embeddings are keyed by fact text content and content type, not by fact ID.

Potential consequence:

- collisions/ambiguity if multiple records share identical text
- delete/update semantics depend on text equality, not entity identity

### 5.10 `reindex_all()` Uses Reasoning Presentation Output, Not Canonical Causal Data

`MemoryStore.reindex_all()` indexes causal link text via `reasoning_engine.get_relevant_causal_context("")`, which returns formatted presentation strings, not raw causal entities.

Potential consequence:

- vector index for causal links is based on display output, not canonical storage

---

## 6. Duplicated Systems

### 6.1 Memory Duplication

Permanent memory exists in two active forms:

- `permanent_notes.txt`
- `facts` table with `memory_kind="permanent"`

### 6.2 Identity Duplication

User identity is stored across:

- `UserModel`
- `IdentityVault`
- personality snapshot derived from personality meta
- fact tags like `core_identity`, `anchor`, `pinned`

These systems are related but not unified.

### 6.3 Personality / Profile Duplication

Profile-like data exists in:

- personality meta in SQLite
- `UserModel`
- `IdentityVault`
- dead `UnifiedProfile` shadow path

### 6.4 Long-Term Context Duplication

Long-term context is split across:

- summaries table
- master summary meta
- permanent notes file
- facts table
- static `ivan.txt`

### 6.5 Storage Model Duplication

The project currently operates two persistence paradigms at once:

- SQLite-backed memory stack
- file/JSON/JSONL-backed reasoning + self model + auxiliary data

---

## 7. Previous Refactorings: Actual State Check

### 7.1 SQLite Refactor

Status: partially successful

What is true now:

- facts/messages/reflections/beliefs/summaries/timeline/sessions/meta are in SQLite
- retrieval metrics and audit log are in SQLite
- `MemoryStore` is a real facade over SQLite + vector index

What is not fully unified:

- reasoning remains file-based
- user model logs remain JSONL
- self model remains file-based
- todo/diary/monthbook/permanent notes remain file-based

### 7.2 Session Recovery Refactor

Status: active and real

`llm/sessions.py` reconstructs recent chat history from SQLite message logs.

Dedup between passed history and reconstructed history is present.

### 7.3 ShadowEvaluator Refactor

Status: present but narrow

What is true:

- evaluator exists
- it is invoked from `UserModel`
- overwrite bug on `identity_updates` is not present in current code

What is still not true:

- protection does not cover all identity fields
- failure mode is allow-by-default
- final write to IdentityVault uses explicit overwrite

### 7.4 FAISS / Embedding Refactor

Status: active and real

What is true:

- embeddings are persisted in SQLite table `embeddings`
- in-memory HNSW index is built from DB state
- reindex on startup exists
- search path is live

What is still imperfect:

- index keys are text/hash based, not entity-id based
- search and delete semantics are text-centric
- content type filtering is runtime-side over a single shared index

### 7.5 Master Summary Refactor

Status: active and real

What is true:

- master summary is actually used in system prompt building
- update path is wired into compress pipeline
- storage is SQLite meta

What remains stale:

- legacy file helper methods still exist
- backup side file is still emitted

---

## 8. Memory Consistency Audit

### 8.1 SQLite

Current role:

- primary persistence for the core memory system

Tables confirmed:

- facts
- fact_relations
- messages
- reflections
- beliefs
- timeline
- meta
- sessions
- retrieval_metrics
- summaries
- audit_log

Strengths:

- central for core memory
- session persistence exists
- timeline migration landed here
- audit triggers exist for facts and identity

Weak points:

- many callers bypass public APIs and use private methods
- each DB call opens a new connection
- no cross-subsystem transaction covering DB + JSONL/file domains

### 8.2 FAISS / Vector Layer

Current role:

- semantic retrieval layer backed by SQLite embeddings + in-memory HNSW index

Consistency model:

- embeddings persisted in DB
- in-memory index loaded from DB
- updates mostly happen during fact/belief/reflection/summary insertions

Observed weak points:

- entity lifecycle is text-hash driven
- rollback compatibility is incomplete
- causal link indexing uses rendered context strings instead of canonical causal entities

### 8.3 IdentityVault

Current role:

- SQLite table for protected identity categories

Consistency model:

- user model sync writes back into vault
- prompt system reads vault block directly

Observed weak points:

- overwrite protection can be bypassed via `explicit_overwrite=True`
- no unified authority over identity vs UserModel vs personality vs facts

### 8.4 UserModel

Current role:

- separate long-lived reflective personality/identity model

Persistence:

- main state stored in SQLite meta as `user_model`
- reflection log stored in JSONL
- legacy file migration path still present

Observed weak points:

- blocking persistence inside async method
- partial overlap with IdentityVault and personality store
- sync back to IdentityVault is non-atomic with model save

### 8.5 Summaries

Current role:

- summaries table stores rolling compress outputs
- master summary meta stores long-term aggregate summary

Observed weak points:

- stale legacy helper API remains
- backup file is still emitted for master summary updates

### 8.6 Rollback

Current role:

- not active in runtime

Consistency status:

- cannot be considered a valid part of current memory consistency model
- covers too little of the actual memory surface

### 8.7 Overall Memory Consistency Conclusion

The project does not have one unified memory system.

It has:

1. a core SQLite memory stack
2. a vector overlay
3. an IdentityVault sub-store
4. a separate UserModel state machine
5. a separate file-based reasoning engine
6. several legacy/file auxiliary stores

These systems interact, but they are not transactionally unified.

---

## 9. Async / Sync Boundary Audit

### 9.1 Boundaries That Are Explicitly Managed

There is clear intentional use of `asyncio.to_thread(...)` in many hot paths:

- analyzer call path from `bot_core`
- logging messages
- reasoning context computation
- retrieval context loading
- compression sub-stages
- personality critical section
- todo file operations
- voice recognition conversion path
- document uploads in parts of pipeline

This indicates real awareness of blocking I/O in async contexts.

### 9.2 Boundaries That Remain Unsafe or Mixed

Unsafe or mixed sync-in-async areas still present:

- `user_model.reflect_after_interaction()` performs blocking DB and file operations directly inside async method
- `self_model.get_error_summary()` is sync file scan and can be reached from command path
- `report_service._collect_retrospective()` uses sync file/event reads
- `documents.py` writes temp files directly in async request path and returns raw exception text
- `MemoryStore` methods are sync by design and sometimes called directly from async contexts without `to_thread`

### 9.3 LLM Invocation Models

Three invocation styles coexist:

- direct sync calls, e.g. `oneshot`, `search_with_grounding`
- native aio client calls, e.g. `aio_oneshot`
- thread-wrapped sync calls, e.g. `run_llm`, `async_oneshot`

This is operationally inconsistent, though runtime currently works by using a subset of these.

---

## 10. Locking Audit

### 10.1 Locks That Exist

- `MemoryStore.lock` -> lazily created `asyncio.Lock`
- `VectorIndex.lock` -> `threading.RLock`
- `UserModel._lock` -> `threading.RLock`
- `ReasoningEngine._lock` -> `threading.RLock`
- `_compression_locks[user_id]` -> per-user `asyncio.Lock`
- background semaphore in `background_scheduler`

### 10.2 What Those Locks Actually Protect

`MemoryStore.lock`

- used as external critical-section lock for multi-step read-modify-write work
- not acquired internally by store methods

`VectorIndex.lock`

- protects in-memory index state and embedding-table mutation path

`UserModel._lock`

- protects in-process model dict mutation
- does not make async execution atomic

`ReasoningEngine._lock`

- protects goal/world-model updates and rewrite operations

`_compression_locks`

- protects concurrent compress per user

### 10.3 Locking Gaps

- `threading.RLock` in async methods does not prevent coroutine interleaving across awaits
- cross-system updates are not transactionally protected under one lock
- `MemoryStore.lock` is advisory and depends on caller discipline
- `_compression_locks` has no cleanup path
- file-based reasoning and file-based self model have no shared coordination with memory store lock

### 10.4 Overall Locking Conclusion

Locking is present but local.

There is no single concurrency model for the whole runtime.
Protection exists per component, not per logical unit of state.

---

## 11. ShadowEvaluator Audit

### 11.1 What It Actually Does

`companion/llm/shadow_eval.py` evaluates proposed changes to identity by comparing:

- category
- current value
- proposed value

It returns boolean validity from a separate LLM call.

### 11.2 Where It Is Actually Used

It is used in `companion/user_model.py` during `reflect_after_interaction()`.

Current scope:

- only `identity_updates["who_they_are"]`

### 11.3 What Is Already Fixed in Current Code

The earlier overwrite bug is not present in current code.

`identity_updates` is loaded once, modified by shadow evaluation if needed, and then reused.

### 11.4 Real Remaining Limitations

- only one field is shadow-evaluated
- all other identity-ish fields are not shadow-evaluated
- evaluator prompt is English while surrounding system is Russian-oriented
- on evaluator failure, it returns `True` and allows the update
- after update, sync into `IdentityVault` uses `explicit_overwrite=True`

### 11.5 Overall ShadowEvaluator Conclusion

ShadowEvaluator exists and is live.
It is not dead code.
But it is not a full identity safety layer.
It is a narrow guard on one field with fail-open behavior.

---

## 12. Potential Points of Failure

- `services/reasoning_service.py:show_selfmap()` because `knowledge_map` is no longer in `self_model.data`
- any future activation of `memory/rollback.py`
- divergence between permanent notes file and permanent facts in SQLite
- divergence between `UserModel` and `IdentityVault`
- crash before world model delayed save flushes to disk
- blocking sync I/O inside async user model path under load
- startup/migration confusion from stale legacy compatibility helpers
- private API coupling (`_insert_*`, `_content_hash`, `_load_index`, `_conn`) causing fragile refactors

---

## 13. What Requires Removal

- dead `RollbackManager` path if rollback is not going to be completed
- dead `UnifiedProfile` path and `unified_profile_block` plumbing if shadow-mode merge is abandoned
- stale legacy summary/master-summary helper methods if SQLite is the final source
- dead/unused async grounding wrappers if runtime will remain on `run_llm(search_with_grounding, ...)`
- obsolete compatibility placeholders like `companion/intents.py` if backward compatibility is not required

---

## 14. What Requires Rewriting

- identity consistency model across `UserModel`, `IdentityVault`, facts, personality
- cross-subsystem memory authority and source-of-truth boundaries
- rollback if it is meant to exist in production
- async/sync execution model for user model persistence and related cross-writes
- private memory/storage API coupling
- unified architectural documentation to match actual runtime

---

## 15. What Can Stay As Is

- `main.py` startup orchestration overall
- `bot_core.py` as the actual runtime orchestrator, if accepted explicitly as application core
- session reconstruction from SQLite in `llm/sessions.py`
- compression pipeline shape in `llm/pipeline.py`
- SQLite core memory tables and retrieval metrics/audit usage
- vector index startup/load/search design as an operational baseline
- service split for command-oriented operations
- background scheduler semaphore/task tracking as a workable first-line mechanism

---

## 16. Final Baseline Summary

The current system is a hybrid architecture.

It is not a single coherent memory engine yet.

What exists in reality:

- a real Telegram bot runtime
- a real SQLite-backed memory core
- a real vector retrieval layer
- a real identity vault
- a real reflective user model
- a real file-based reasoning subsystem
- several still-live legacy/file-based side stores

What is no longer part of the effective runtime:

- rollback path
- unified profile shadow path
- old intent router
- old legacy summary/master-summary helpers

What is truly broken now:

- `show_selfmap()` against missing `knowledge_map`

What is structurally fragile:

- source-of-truth duplication
- fail-open identity validation
- mixed async/sync persistence
- private API coupling
- split persistence paradigms across subsystems

This file is intended to serve as the factual baseline for any further cleanup, deletion, or redesign work.
