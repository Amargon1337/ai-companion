# Amargon's Void — Architectural Modernization Plan

> "Настоящая память — это не база фактов. Настоящая память — это непрерывная модель опыта, личности и изменений во времени."

**Status:** Architectural Blueprint  
**Author:** Principal Engineering Audit  
**Date:** 2026-08-06  
**Scope:** Full system modernization preserving cognitive architecture  

---

## Table of Contents

1. [System Map](#1-system-map)
2. [What Must Be Preserved](#2-what-must-be-preserved)
3. [Problem Classification](#3-problem-classification)
4. [Phase 0: Stabilization](#4-phase-0-stabilization-p0)
5. [Phase 1: Structural Foundation](#5-phase-1-structural-foundation-p1)
6. [Phase 2: Cognitive Architecture](#6-phase-2-cognitive-architecture-p2)
7. [Phase 3: Scale & Observability](#7-phase-3-scale--observability-p3)
8. [Migration Strategy](#8-migration-strategy)
9. [Testing Strategy](#9-testing-strategy)
10. [Risk Register](#10-risk-register)

---

## 1. System Map

### 1.1. Current Architecture (As-Is)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ENTRY POINT: bot.py                                  │
│                              ↓                                               │
│                    companion/main.py::run()                                  │
│         ┌──────────────┬───────────────┬──────────────────┐                 │
│         ↓              ↓               ↓                  ↓                 │
│   AuthMiddleware  Dispatcher     sanitize_legacy     reindex_all()          │
│                                      files()          recover_index()       │
└─────────────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                     REQUEST PIPELINE (bot_core.py)                           │
│                                                                              │
│  message → process_llm_request()                                            │
│              ├─ build_context()                                              │
│              │    ├─ _restore_sessions()          ← race condition           │
│              │    ├─ check_rate_limit()           ← in-memory dict           │
│              │    ├─ analyze_message()            ← LLM call (3-5s)         │
│              │    ├─ reasoning_engine.auto_reasoning_context()               │
│              │    ├─ _load_retrieval_context()    ← asyncio.to_thread        │
│              │    ├─ _get_policy_decision()       ← DISABLED, returns None   │
│              │    └─ _init_user_session()         ← LLM call                │
│              │                                                              │
│              ├─ _route_command() (if intent=command)                        │
│              │                                                              │
│              └─ _generate_and_send_response()                               │
│                   ├─ retrieval_mgr.select()      ← 319 lines               │
│                   ├─ generate_plan()             ← LLM call (Phase 1 CoT)  │
│                   ├─ chat.send_message()         ← LLM call (Phase 2)      │
│                   ├─ run_self_critique()         ← LLM call (sometimes)     │
│                   ├─ apply_critique_to_text()                               │
│                   ├─ send_long_message()                                    │
│                   ├─ log_message()               ← SQLite write             │
│                   └─ _analyze_context_utilization()                         │
│                                                                              │
│  ═══════════════════════════════════════════════════════════════════════     │
│  GLOBAL STATE (module-level):                                               │
│    memory_store = MemoryStore()           ← singleton, 1321 lines           │
│    retrieval_mgr = RetrievalBudgetManager()                                 │
│    context_aggregator = ContextAggregator()                                 │
│    user_chats: dict[int, Any] = {}                                         │
│    user_message_counts: dict[int, int] = {}                                 │
│    _user_request_times: dict[int, list[float]] = {}                         │
│    _compression_locks: dict[int, asyncio.Lock] = {}                         │
│    last_activity: dict[int, float] = {}                                    │
│    embedding_retry_worker = None                                            │
│  ═══════════════════════════════════════════════════════════════════════     │
└─────────────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                      MEMORY LAYER (companion/memory/)                        │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     MemoryStore (1321 lines)                        │    │
│  │  facts CRUD │ reflections │ patterns │ beliefs │ summaries          │    │
│  │  human_model │ comm_prefs │ transitions │ episodes │ retrieval      │    │
│  │  personality │ search │ graph traversal │ lifecycle                  │    │
│  └─────────────────────────────┬───────────────────────────────────────┘    │
│                                │                                            │
│  ┌─────────────┐  ┌────────────▼──────────┐  ┌──────────────────────────┐  │
│  │ VectorIndex │  │ MemoryEventBus        │  │ MemoryGovernor           │  │
│  │ (FAISS +    │  │ (pub/sub, daemon      │  │ (decide: archive, boost, │  │
│  │  SQLite +   │  │  thread)              │  │  decay, merge, validate) │  │
│  │  FTS5)      │  │                       │  │                          │  │
│  └─────────────┘  │ IndexSyncService      │  │ policies/                │  │
│                   │ (subscribes events,   │  │  ├─ archive_policy        │  │
│                   │  syncs FAISS)         │  │  ├─ boost_policy          │  │
│                   └───────────────────────┘  │  ├─ immunity_policy       │  │
│                                              │  ├─ merge_policy          │  │
│  ┌───────────────────────────────────────┐  │  └─ validation_policy     │  │
│  │ WorldModelService                     │  └──────────────────────────┘  │
│  │ Entity extraction (hardcoded rules)   │                                 │
│  │ Entity resolution (name/alias/vector) │  ┌──────────────────────────┐  │
│  │ Entity merge (OCC, audit)             │  │ MemoryPersistenceLayer   │  │
│  │ Graph retrieval (multi-hop)           │  │ apply_decision()         │  │
│  │ Graph consistency checker             │  │ mutation_log             │  │
│  └───────────────────────────────────────┘  │ event publishing         │  │
│                                              └──────────────────────────┘  │
│  ┌───────────────────────────────────────┐                                  │
│  │ CognitiveLoop / ReasoningEngine /     │                                  │
│  │ LearningEngine                        │                                  │
│  └───────────────────────────────────────┘                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STORAGE LAYER (sqlite_db.py — 2745 lines)                 │
│                                                                              │
│  25+ tables:                                                                │
│  Core: facts, fact_relations, messages, reflections, beliefs, patterns      │
│  Models: human_model, communication_prefs, life_transitions, episodes       │
│  Graph: entities, entity_attributes, entity_relations, entity_mentions      │
│  Reasoning: goals, causal_links, predictions                                │
│  Cognitive: memory_genome, cognitive_working_memory, theory_of_mind,        │
│            council_votes, cognitive_timeline, homeostasis_metrics            │
│  System: meta, sessions, audit_log, memory_mutation_log, state_models,      │
│          faiss_mapping, retrieval_metrics, retrieval_replays,                │
│          proactive_events, temporal_counters, memory_access_log,             │
│          shared_lore_candidates, monthbooks, summaries                       │
│                                                                              │
│  Concurrency: threading.RLock + threading.local() tx depth                  │
│  Transactions: BEGIN IMMEDIATE + atomic_memory_transaction()                │
│  Audit: SQLite triggers on facts/entities/attributes/relations/mentions     │
│  Migrations: ALTER TABLE in Python (no versioning, no rollback)             │
└─────────────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                      BACKGROUND LAYER                                        │
│                                                                              │
│  proactive_ping_loop()     — 60s cycle, nightly consolidation               │
│  embedding_retry_worker    — exponential backoff (BROKEN: wrong field name)  │
│  background_scheduler      — safe_task(), circuit breaker, semaphore=5       │
│  cancel_all_tasks()        — shutdown cleanup                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2. Data Flow: Single Request

```
User message arrives
  │
  ├─ [1] analyze_message()          ← LLM call, ~2s
  │     intent, mood, importance, command, needs_clarification
  │
  ├─ [2] reasoning_engine           ← deterministic
  │     auto_reasoning_context → goals, causal links, world model
  │
  ├─ [3] _load_retrieval_context()  ← SQLite reads + FAISS search
  │     facts, reflections, patterns, summaries, goals, events
  │
  ├─ [4] retrieval_mgr.select()     ← deterministic (ranking, MMR, budget)
  │     ContextBundle with token budget enforcement
  │
  ├─ [5] generate_plan()            ← LLM call, ~2s (Phase 1 CoT)
  │     Internal reasoning plan
  │
  ├─ [6] build_system_instruction() ← deterministic (template assembly)
  │     CORE_PERSONALITY + strategy + tone + temporal + sensitivity + RAG
  │
  ├─ [7] chat.send_message()        ← LLM call, ~5-30s (Phase 2 final)
  │     Final response generation
  │
  ├─ [8] run_self_critique()        ← LLM call (optional, ~2s)
  │     Quality check on response
  │
  └─ [9] log_message()              ← SQLite write
        Persist assistant response

Total latency: 10-60 seconds per message
LLM calls: 3-5 per message
```

### 1.3. Data Flow: Compress Pipeline (every 50 messages)

```
compress_and_reset()
  │
  ├─ [CRITICAL PATH — sequential]
  │    ├─ SUMMARY              ← LLM call (~5s)
  │    ├─ EXTRACT_FACTS        ← LLM call (~5s)
  │    ├─ CONSOLIDATE_FACTS    ← LLM call (~5s)
  │    └─ EXTRACT_CAUSAL_LINKS ← LLM call (~5s)
  │
  ├─ [EVERY Nth COMPRESS]
  │    ├─ EXTRACT_PATTERNS     ← LLM call (~5s)
  │    ├─ GENERATE_REFLECTIONS ← LLM call (~5s)
  │    └─ EXTRACT_LCE          ← LLM call (~5s)
  │
  ├─ [EVERY COMPRESS]
  │    ├─ EXTRACT_COMM_PREFS   ← LLM call (~3s)
  │    └─ EXTRACT_HUMAN_MODEL  ← LLM call (~3s)
  │
  ├─ [PERSONALITY UPDATE]
  │    └─ GENERATE_PERSONALITY ← LLM call (~5s, async)
  │
  ├─ [MAINTENANCE — in threads]
  │    ├─ consolidate_if_due()         (weekly snapshot)
  │    ├─ decay_fact_confidence()      (daily)
  │    ├─ apply_importance_decay()     (dormant aging)
  │    ├─ compress_dormant_episodes()  (episodic compression)
  │    ├─ analyze_retrieval_effectiveness()
  │    └─ update_master_summary()      ← LLM call
  │
  └─ Total: 10-15 LLM calls, 60-180 seconds
```

---

## 2. What Must Be Preserved

These mechanisms represent the unique cognitive architecture of Amargon's Void. They are **NOT to be removed or simplified**. They are to be **extracted, cleaned, and made robust**.

### 2.1. Memory Lifecycle State Machine

```
quarantine → pending_embedding → active → dormant → archived → purged
                ↓                  ↓         ↓
          pending_review     superseded  contradicted
```

**Why it matters:** This is what makes the system a Memory OS rather than a vector database. Facts have birth, life, aging, and death. They don't just sit there.

**Preservation rule:** Keep the state machine. Fix the implementation (remove `compute_and_cache()` from inside transactions, fix `embedding_retry_worker`).

### 2.2. Reliability Layer (Aging/Decay)

```
active ──(90d without confirmation)──▶ aging ──(240d)──▶ stale
```

**Why it matters:** This implements " forgetting without deleting." The system remembers everything but knows what's fresh.

**Preservation rule:** Keep lazy status computation. Keep `touch_*()` methods for confirmation. Keep the half-life parameters configurable.

### 2.3. Pattern → Insight Promotion

```
Pattern (confirmed ≥3 times, span ≥14 days)
    ↓ promote_patterns_to_insights()
HumanModelInsight (trait with confidence = 0.6 + 0.05×confirmations)
    ↓ revalidate_insight_provenance()
    If sources die → trait weakens → trait refuted
    If sources revive → trait restored
```

**Why it matters:** This is what separates a mood from a personality trait. Three mentions in one evening is a mood. Three mentions across three months is who the person is. No other AI system implements this.

**Preservation rule:** Keep the promotion logic. Keep the provenance chain. Keep the revalidation.

### 2.4. Epistemic Typing

```
DIRECT_FACT        — observed, user-stated
HYPOTHESIS         — model inference
LLM_INFERENCE      — generated by LLM
PREDICTION         — forward-looking
```

With `support_count` and `contradiction_count` tracking.

**Why it matters:** The system knows WHY it believes something and HOW SURE it is. This is what enables "explain why you think this" — a capability no other personal AI has.

**Preservation rule:** Keep the epistemic classes. Expand them. Make them queryable.

### 2.5. Life Continuity Engine (LCE)

```
HumanModel snapshot at T1
    ↓
HumanModel snapshot at T2
    ↓
extract_life_transitions() → "Иван перешёл от состояния A к состоянию B"
    ↓
confidence < 0.65 → pending_review (quarantine)
```

**Why it matters:** This tracks the trajectory of personality change. Not "who you are now" but "how you've changed." This is what gives the system continuity over years.

**Preservation rule:** Keep the transition model. Keep the quarantine for low-confidence transitions. Keep the timeline view.

### 2.6. Identity Vault

Core facts (name, age, city, pet, diagnosis, anchor reasons) protected by write-lock. Low-confidence updates rejected. Major changes require explicit override.

**Why it matters:** Without this, a single confused conversation could overwrite the user's name. This is the system's immune system for core identity.

**Preservation rule:** Keep the vault. Keep the overlap-based rejection. Keep the change log.

### 2.7. Provenance Chains

```
HumanModelInsight.evidence = [pattern_id, fact_id_1, fact_id_2, ...]
    ↓
explainInsight() → full source resolution with current status
```

**Why it matters:** Every belief is traceable to its sources. If a source is superseded, the belief weakens. This is what prevents "immortal personality traits" based on outdated evidence.

**Preservation rule:** Keep the evidence arrays. Keep the revalidation. Keep the explainability API.

### 2.8. World Model (Entity Graph)

Entities with attributes, relations, mentions. Multi-hop graph retrieval. Consistency checking.

**Why it matters:** "Иван имеет отношение X к Y в контексте Z" is fundamentally different from "факт: Иван X." The graph enables relational reasoning.

**Preservation rule:** Keep the entity model. Replace hardcoded extraction rules with configurable/learned ones. Keep the merge service.

### 2.9. Consolidation & Golden Memory

```
build_snapshot() → personality snapshot v2
    ↓
golden_memory = stable person-level meaning (values, proven patterns, causal links)
    ↓
Identity Vault: anchor_reason ← golden_memory
```

**Why it matters:** The system distinguishes between raw episodic facts and stable personality-level knowledge. Golden memory is what survives compression.

**Preservation rule:** Keep the snapshot builder. Keep the golden memory concept. Keep the identity anchoring.

### 2.10. Event Bus Architecture

Async pub/sub with daemon thread, at-least-once delivery, graceful shutdown.

**Why it matters:** Decouples memory mutations from side effects. FAISS sync, audit logging, and future consumers can subscribe without blocking the write path.

**Preservation rule:** Keep the bus. Keep sync/async modes. Keep the shutdown sequence.

---

## 3. Problem Classification

### P0 — Critical (Must fix before any new features)

| ID | Problem | File | Impact |
|----|---------|------|--------|
| P0-1 | `embedding_retry_worker` uses `fact.metadata` instead of `fact.meta` — completely broken | `embedding_retry_worker.py` | Facts with failed embeddings never recover |
| P0-2 | `api.env` not in `.gitignore` — credential leak risk | `.gitignore` | API keys could be committed to git |
| P0-3 | `config.py` has 19 duplicate variable definitions | `config.py` | Silent override, unpredictable behavior |
| P0-4 | `sqlite_db.py` has duplicate `get_meta`/`set_meta` with different defaults | `sqlite_db.py` | Wrong default value (`""` vs `"0"`) |
| P0-5 | `models.py` missing `import json` at top level | `models.py` | `Entity.__post_init__` crashes on clean import |
| P0-6 | Embedding API call inside `atomic_memory_transaction()` — holds SQLite write lock 2-30s | `store.py` | System-wide write freeze during API calls |
| P0-7 | `world_model.process_fact()` exception inside transaction → ALL facts lost | `store.py` | Single bad fact → entire compress cycle lost |
| P0-8 | `asyncio.Lock` used to protect `to_thread()` critical sections — doesn't protect across threads | `pipeline.py`, `background_scheduler.py` | Personality updates lost to race condition |

### P1 — Important (Must fix for production quality)

| ID | Problem | File | Impact |
|----|---------|------|--------|
| P1-1 | `bot_core.py` is a god-object (1273 lines, 15 responsibilities) | `bot_core.py` | Untestable, unextendable |
| P1-2 | `sqlite_db.py` is a god-object (2745 lines, 25+ tables) | `sqlite_db.py` | Schema changes are terrifying |
| P1-3 | No dependency injection — 5 global singletons | Multiple | Tests require monkeypatching |
| P1-4 | 50+ deferred imports to work around circular dependencies | Multiple | Architecture debt, hidden coupling |
| P1-5 | No schema migration framework — ALTER TABLE in Python | `sqlite_db.py` | Schema drift, no rollback |
| P1-6 | `analyze_message()` LLM call on every message (3-5s latency) | `analyzer.py` | Unnecessary cost and delay |
| P1-7 | Compress pipeline is fully sequential (10-15 LLM calls) | `pipeline.py` | 60-180 second compression |
| P1-8 | Context double-injection (RAG in both system instruction AND user message) | `bot_core.py`, `sessions.py` | ~2000-5000 wasted tokens |
| P1-9 | `_restore_sessions()` race condition — read-check-write without lock | `bot_core.py` | Session corruption under concurrency |
| P1-10 | `proactive_ping_loop` has no backoff on exception | `main.py` | Log spam on persistent error |
| P1-11 | No CI/CD — 78 test files with no automated runner | Infrastructure | Regressions go undetected |
| P1-12 | No Dockerfile — `docker-compose.yml` exists but can't build | Infrastructure | No reproducible deployment |

### P2 — Improvement (Quality of life)

| ID | Problem | File | Impact |
|----|---------|------|--------|
| P2-1 | Entity resolution uses hardcoded rules for 7 names | `world_model.py` | Can't handle new entities without code change |
| P2-2 | 162 `except Exception` handlers — catch-all culture | Multiple | Errors silently swallowed |
| P2-3 | 74 f-strings in logger calls — eager evaluation | Multiple | Performance waste |
| P2-4 | 94 `Any`-typed parameters — no type contracts | Multiple | Type safety gaps |
| P2-5 | Bilingual codebase (Russian + English mixed) | Multiple | Inconsistent UX |
| P2-6 | Junk files in repo root (d, tall gitleaks, debug_*.py, etc.) | Root | Professional appearance |
| P2-7 | 30+ functions over 50 lines (top: `_init_schema` at 578 lines) | Multiple | Readability, testability |
| P2-8 | `cosine_similarity` defined twice in `vector_index.py` | `vector_index.py` | Dead code |
| P2-9 | Private API abuse — `db._conn()` called from 6+ external modules | Multiple | Encapsulation broken |
| P2-10 | No vector store abstraction — FAISS hardcoded everywhere | `vector_index.py` | Can't swap backends |
| P2-11 | Observability traces in-memory only — lost on restart | `observability.py` | Can't diagnose post-restart issues |
| P2-12 | `send_long_message()` edge case — `split == 0` → infinite loop | `bot_core.py` | Hang on specific text patterns |

### P3 — Future (Strategic)

| ID | Problem | File | Impact |
|----|---------|------|--------|
| P3-1 | Single-process, single-user architecture | Architecture | Can't scale beyond 1 user |
| P3-2 | FAISS HNSW can't remove vectors — soft-delete accumulation | `vector_index.py` | Search quality degrades at scale |
| P3-3 | No multi-agent / tool use architecture | Architecture | Can't delegate, can't use external tools |
| P3-4 | No streaming response support | Architecture | Users wait 10-60 seconds for complete response |
| P3-5 | No explicit state machine / graph execution | Architecture | Can't model complex conversation flows |
| P3-6 | Memory poisoning has no active defense | Security | Sustained false input corrupts model |
| P3-7 | No truth verification layer for user-stated facts | Memory quality | Single lie can become permanent |
| P3-8 | FAISS rebuild from scratch at every startup (if dirty) | `vector_index.py` | Slow startup at scale |
| P3-9 | No vector store sharding | Architecture | Can't scale past ~500K vectors |
| P3-10 | Context selection collapse — rich-get-richer retrieval bias | `retrieval.py` | Old/rare facts never surface |

---

## 4. Phase 0: Stabilization (P0)

**Goal:** Fix critical bugs. No new features. No refactoring. Pure bug fixes.  
**Duration:** 1-2 days.  
**Risk:** Minimal — each fix is isolated.

### 4.1. P0-1: Fix `embedding_retry_worker`

**Problem:** Worker uses `fact.metadata` but `Fact` dataclass has `fact.meta`. Worker crashes on first use.

**Fix:**
```python
# embedding_retry_worker.py
# Change all occurrences of:
fact.metadata  →  fact.meta
```

**Files:** `companion/memory/embedding_retry_worker.py`  
**Test:** `tests/test_embedding_retry_worker.py` — verify retry cycle with mock embedding API.

### 4.2. P0-2: Fix `.gitignore`

**Problem:** `api.env` not excluded. Pattern `*.env.*` requires suffix after `.env`.

**Fix:**
```gitignore
# Add:
api.env
api.env.txt
*.env
```

**Files:** `.gitignore`  
**Test:** `git check-ignore api.env` should return the path.

### 4.3. P0-3: Remove duplicate config definitions

**Problem:** 19 variables defined twice in `config.py` (lines 112-144 duplicated at 170-201).

**Fix:** Delete lines 170-201 (the duplicate block).

**Files:** `companion/config.py`  
**Test:** `python -c "from companion.config import LCE_EVERY_N"` — should work.

### 4.4. P0-4: Remove duplicate `get_meta`/`set_meta`

**Problem:** Two definitions with different defaults (`"0"` vs `""`).

**Fix:** Remove the second definition (lines 2348+). Keep the first (line 1371, default `"0"`). Audit callers that depend on `""` default.

**Files:** `companion/storage/sqlite_db.py`  
**Test:** Existing tests should pass. Add test for default value.

### 4.5. P0-5: Add `import json` to `models.py`

**Problem:** `Entity.__post_init__` calls `json.loads()` but `json` not imported at top level.

**Fix:** Add `import json` to imports section.

**Files:** `companion/models.py`  
**Test:** `python -c "from companion.models import Entity; Entity(name='test', type='person')"` — should work.

### 4.6. P0-6: Move embedding API call outside transaction

**Problem:** `add_fact()` calls `compute_and_cache()` inside `atomic_memory_transaction()`, holding SQLite write lock during API call (2-30s).

**Fix:**
```python
def add_fact(self, fact: Fact) -> Fact:
    # Phase 1: Compute embedding BEFORE transaction
    vec = None
    if fact.status in ("active", "dormant"):
        vec = self.vector.embed_text_only(fact.fact)
        if vec is None:
            fact.status = "pending_embedding"
    
    # Phase 2: Transaction (no API calls inside)
    with self.db.atomic_memory_transaction():
        self.db._insert_fact(fact.to_dict())
        # world_model.process_fact() wrapped in try/except (see P0-7)
        if vec is not None:
            self.vector.upsert_embedding(fact.fact, vec, content_type="fact", fact_id=fact.id)
    
    # Phase 3: Events (outside transaction)
    if self.event_bus:
        self.event_bus.publish(FactCreatedEvent(...))
```

**Files:** `companion/memory/store.py`  
**Test:** `tests/test_add_fact_no_api_in_transaction.py` — verify no API call inside transaction.

### 4.7. P0-7: Wrap `process_fact()` in try/except

**Problem:** If `world_model.process_fact()` raises, the entire `atomic_memory_transaction()` rolls back → all facts in the batch are lost.

**Fix:**
```python
with self.db.atomic_memory_transaction():
    self.db._insert_fact(d)
    try:
        if hasattr(self, "world_model") and self.world_model:
            self.world_model.process_fact(fact, index_entities=False)
    except Exception as exc:
        logger.warning("world_model.process_fact() failed for %s: %s", fact.id, exc)
    # Continue with vector upsert even if world_model failed
```

**Files:** `companion/memory/store.py`  
**Test:** `tests/test_world_model_failure_isolation.py` — verify fact saved even if world_model fails.

### 4.8. P0-8: Fix cross-thread lock for personality updates

**Problem:** `asyncio.Lock` doesn't protect `asyncio.to_thread()` critical sections. Two threads can read-modify-write personality simultaneously.

**Fix:**
```python
# Replace asyncio.Lock with threading.Lock for sync critical sections
import threading

class MemoryStore:
    def __init__(self):
        ...
        self._sync_lock = threading.Lock()  # For to_thread() critical sections
        self._async_lock = asyncio.Lock()    # For async-only critical sections
    
    @property
    def sync_lock(self) -> threading.Lock:
        return self._sync_lock
    
    @property
    def lock(self) -> asyncio.Lock:
        return self._async_lock
```

Then in `pipeline.py`:
```python
# Before:
async with store.lock:
    merged = await asyncio.to_thread(_personality_critical_section, store, updated)

# After:
def _personality_with_lock(store, updated):
    with store.sync_lock:
        return _personality_critical_section(store, updated)
merged = await asyncio.to_thread(_personality_with_lock, store, updated)
```

**Files:** `companion/memory/store.py`, `companion/llm/pipeline.py`, `companion/background_scheduler.py`  
**Test:** `tests/test_personality_concurrent_update.py` — verify no lost updates.

---

## 5. Phase 1: Structural Foundation (P1)

**Goal:** Break up god-objects, introduce DI, fix pipeline architecture.  
**Duration:** 2-3 weeks.  
**Risk:** Medium — structural changes require careful migration.

### 5.1. Extract Request Pipeline from `bot_core.py`

**Target architecture:**

```
companion/
  pipeline/
    __init__.py
    base.py              ← PipelineStage protocol
    stages/
      __init__.py
      authenticate.py    ← AuthMiddleware logic
      analyze.py         ← analyze_message() + fast-path
      retrieve.py        ← _load_retrieval_context() + retrieval_mgr.select()
      generate.py        ← plan + response generation
      persist.py         ← log_message + metrics
    executor.py          ← Pipeline orchestrator
    context.py           ← PipelineContext (replaces RuntimeState)
```

**Migration path:**
1. Create `pipeline/` package with `PipelineStage` protocol.
2. Extract each stage from `bot_core.py` into its own module.
3. `bot_core.py` becomes a thin adapter: `telegram_handler → pipeline.execute()`.
4. Existing tests continue to work (they test `MemoryStore`, not `bot_core`).

**Key benefit:** Each stage is independently testable. New stages can be added without touching existing code. Different channels (Telegram, API, CLI) can reuse the same pipeline.

### 5.2. Introduce Dependency Injection Container

**Target architecture:**

```python
# companion/container.py
@dataclass
class AppContainer:
    config: AppConfig
    db: MemoryDatabase
    vector_store: VectorStore
    memory_store: MemoryStore
    retrieval_manager: RetrievalBudgetManager
    event_bus: MemoryEventBus
    governor: MemoryGovernor
    # ... etc

def create_container(config_path: str = "api.env") -> AppContainer:
    config = AppConfig.from_env(config_path)
    db = MemoryDatabase(config.sqlite_path)
    vector_store = VectorIndex(db=db)
    event_bus = MemoryEventBus(async_mode=True)
    # ... wire everything
    return AppContainer(...)
```

**Migration path:**
1. Create `AppContainer` dataclass.
2. `main.py` creates container, passes to pipeline/handlers.
3. Module-level singletons become properties of container.
4. Legacy code continues to work via compatibility shims:
   ```python
   # Backward compatibility
   _default_container: AppContainer | None = None
   def get_default_container() -> AppContainer:
       global _default_container
       if _default_container is None:
           _default_container = create_container()
       return _default_container
   ```
5. Tests create container with mocks → no monkeypatching needed.

### 5.3. Fix Compress Pipeline: DAG Architecture

**Target architecture:**

```
                    ┌──────────────┐
                    │   SUMMARY    │  ← LLM call
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ EXTRACT_FACTS│  ← LLM call
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼──────┐    │     ┌──────▼────────┐
       │CONSOLIDATE  │    │     │CAUSAL_LINKS    │
       └──────┬──────┘    │     └──────┬─────────┘
              │            │            │
              └────────────┼────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
    [every N] │     [every compress]  [every N]
              │            │                │
       ┌──────▼──────┐ ┌──▼──────────┐ ┌───▼──────────┐
       │ PATTERNS    │ │ COMM_PREFS  │ │ HUMAN_MODEL  │
       │ REFLECTIONS │ │             │ │              │
       │ LCE         │ │             │ │              │
       └─────────────┘ └─────────────┘ └──────────────┘
              PARALLEL              PARALLEL
```

**Implementation:**
```python
# companion/pipeline/compress.py
import asyncio

async def run_compress_pipeline(store, chat, user_id):
    # Stage 1: Summary (critical path)
    summary = await llm.run_llm(chat.send_message, SUMMARY_PROMPT)
    await asyncio.to_thread(store.save_summary, summary)
    
    # Stage 2: Fact extraction (critical path)
    new_facts = await asyncio.to_thread(extract_facts, store, summary)
    
    # Stage 3: Parallel non-critical stages
    consolidation_task = asyncio.to_thread(consolidate_facts, store, new_facts)
    causal_task = asyncio.to_thread(extract_causal_links, store, new_facts, summary)
    
    compress_n = store.get_compress_count()
    
    if compress_n % REFLECTION_EVERY_N == 0:
        patterns_task = asyncio.to_thread(extract_patterns, store, summary)
        reflections_task = asyncio.to_thread(generate_reflections, store, summary)
    
    comm_prefs_task = asyncio.to_thread(extract_comm_prefs, store, summary)
    human_model_task = asyncio.to_thread(extract_human_model, store, summary)
    
    if compress_n % LCE_EVERY_N == 0:
        lce_task = asyncio.to_thread(extract_life_transitions, store, summary)
    
    # Wait for all parallel tasks
    await asyncio.gather(
        consolidation_task, causal_task,
        comm_prefs_task, human_model_task,
        *(patterns_task, reflections_task) if compress_n % REFLECTION_EVERY_N == 0 else (),
        *(lce_task,) if compress_n % LCE_EVERY_N == 0 else (),
        return_exceptions=True  # Don't let one failure kill all
    )
    
    # Stage 4: Personality update (separate, can fail independently)
    try:
        await generate_personality_snapshot(store, summary)
    except Exception as e:
        logger.error("Personality update failed: %s", e)
    
    # Stage 5: Maintenance (all independent, all can fail independently)
    maintenance_tasks = [
        asyncio.to_thread(consolidate_if_due, store, 7),
        asyncio.to_thread(decay_fact_confidence, store),
        asyncio.to_thread(store.apply_importance_decay),
        asyncio.to_thread(store.analyze_retrieval_effectiveness),
        asyncio.to_thread(update_master_summary, summary),
    ]
    await asyncio.gather(*maintenance_tasks, return_exceptions=True)
```

**Benefit:** Compress time reduced from 180s to ~30s (parallel execution). One failed stage doesn't kill others.

### 5.4. Schema Migration Framework

**Target architecture:**

```python
# companion/migrations/__init__.py
MIGRATIONS = []  # Ordered list of (version, migration_func)

# companion/migrations/001_initial.py
def migrate_up(conn):
    # Current _init_schema() content
    conn.executescript("""...""")

def migrate_down(conn):
    # Reverse (or no-op for initial)
    pass

# companion/migrations/002_add_epistemic_columns.py
def migrate_up(conn):
    conn.execute("ALTER TABLE facts ADD COLUMN epistemic_class TEXT DEFAULT 'DIRECT_FACT'")
    conn.execute("ALTER TABLE facts ADD COLUMN support_count INTEGER DEFAULT 0")

def migrate_down(conn):
    # SQLite doesn't support DROP COLUMN before 3.35.0
    # Log warning, leave columns
    pass

# companion/migrations/runner.py
def run_migrations(db: MemoryDatabase):
    current_version = db.get_meta("schema_version", "0")
    for version, migration in MIGRATIONS:
        if version > int(current_version):
            logger.info("Running migration %d...", version)
            with db.atomic_memory_transaction():
                migration.migrate_up(db.conn)
                db.set_meta("schema_version", str(version))
            logger.info("Migration %d complete.", version)
```

**Migration path:**
1. Current `_init_schema()` becomes migration 001.
2. All existing `ALTER TABLE` blocks become subsequent migrations.
3. New schema changes add new migration files.
4. `MemoryDatabase.__init__()` calls `run_migrations()` instead of `_init_schema()`.

### 5.5. Fast-Path Message Analysis

**Problem:** Every message → `analyze_message()` LLM call (3-5s). Many messages are simple enough for deterministic analysis.

**Target architecture:**

```python
# companion/pipeline/stages/analyze.py
def analyze_message_fast(text: str) -> dict | None:
    """Deterministic fast-path. Returns None if LLM needed."""
    stripped = text.strip()
    
    # Empty
    if not stripped:
        return _default_analysis()
    
    # Command detection (deterministic)
    if stripped.startswith("/"):
        return {
            "intent": "command",
            "confidence": 0.95,
            "command": stripped.split()[0][1:],
            "estimated_importance": 3,
            "user_mood": {"anxiety": 0, "anger": 0, "sadness": 0, "energy": 0.5},
            "user_state": "NORMAL",
        }
    
    # "Запомни" / "Remember" prefix → high importance, direct intent
    lowered = stripped.lower()
    if lowered.startswith(("запомни", "remember")):
        return {
            "intent": "memory",
            "confidence": 0.9,
            "estimated_importance": 8,
            "command": "remember",
            "user_mood": {"anxiety": 0, "anger": 0, "sadness": 0, "energy": 0.5},
            "user_state": "NORMAL",
        }
    
    # Very short messages (< 5 words) → low importance, no need for LLM
    if len(stripped.split()) < 5 and len(stripped) < 50:
        return {
            "intent": "chat_casual",
            "confidence": 0.7,
            "estimated_importance": 3,
            "user_mood": {"anxiety": 0, "anger": 0, "sadness": 0, "energy": 0.5},
            "user_state": "NORMAL",
        }
    
    # Complex/ambiguous → fall through to LLM
    return None

async def analyze_message_with_fallback(text: str) -> dict:
    result = analyze_message_fast(text)
    if result is not None:
        return result
    return await asyncio.to_thread(analyze_message_llm, text)
```

**Benefit:** ~30-40% of messages bypass LLM analysis → 1-2s latency saved.

### 5.6. Eliminate Context Double-Injection

**Problem:** RAG context appears in BOTH system instruction AND user message payload.

**Fix:** In `_generate_and_send_response()`, remove RAG context from user message payload. It's already in the system instruction via `build_system_instruction(precomputed_context=ctx_block)`.

```python
# bot_core.py → _build_user_prompt_block()
# REMOVE: runtime_context_block, RAG context
# KEEP: only user message + system time + reasoning mode flags
```

**Benefit:** ~2000-5000 tokens saved per request.

---

## 6. Phase 2: Cognitive Architecture (P2)

**Goal:** Improve memory quality, entity resolution, and vector store abstraction.  
**Duration:** 3-4 weeks.  
**Risk:** Low-medium — these are additive improvements.

### 6.1. Vector Store Protocol

**Target:**

```python
# companion/memory/vector_store.py
from typing import Protocol

class VectorStore(Protocol):
    def upsert(self, id: str, text: str, vector: list[float], metadata: dict) -> None: ...
    def delete(self, id: str) -> None: ...
    def search(self, query_vector: list[float], top_k: int, filter: dict | None) -> list[dict]: ...
    def count(self) -> int: ...
    def flush(self) -> None: ...

# Implementations:
# - FaissVectorStore (current behavior)
# - SqliteVectorStore (for small deployments)
# - QdrantVectorStore (future, for scale)
```

**Migration:** `VectorIndex` implements `VectorStore` protocol. Callers depend on protocol, not implementation. Config flag selects backend.

### 6.2. Configurable Entity Dictionary

Replace hardcoded entity rules with a configurable dictionary:

```python
# companion/memory/entity_dictionary.py
@dataclass
class EntityTemplate:
    name: str
    type: str
    default_relation: str
    default_role: str
    aliases: list[str] = field(default_factory=list)

# Loaded from data/entity_dictionary.json
# User can add entities via /entity add command
# LLM-assisted entity extraction for unknown names
```

### 6.3. Truth Verification Layer

**Target:**

```python
# companion/memory/truth_verification.py
class TruthVerifier:
    """Cross-checks new facts against existing knowledge."""
    
    def verify(self, new_fact: Fact, store: MemoryStore) -> VerificationResult:
        # 1. Contradiction check: does this contradict existing active facts?
        contradictions = self._find_contradictions(new_fact, store)
        
        # 2. Source reliability: is this from user, LLM inference, or external?
        reliability = self._assess_reliability(new_fact)
        
        # 3. Novelty check: is this genuinely new or a restatement?
        novelty = self._assess_novelty(new_fact, store)
        
        # Decision:
        # - If contradicts high-confidence fact → pending_review
        # - If low reliability + never confirmed → pending_review
        # - Otherwise → active
        return VerificationResult(
            status="active" if no_issues else "pending_review",
            contradictions=contradictions,
            reliability=reliability,
        )
```

### 6.4. Observability: Persistent Traces

```python
# companion/observability.py
# Add: persist traces to SQLite
def save_trace(trace: RequestTrace, store: MemoryStore) -> None:
    store.db.save_request_trace({
        "trace_id": trace.replay_id,
        "user_id": trace.user_id,
        "query": trace.query,
        "timings_ms": json.dumps(trace.timings_ms),
        "input_tokens": trace.input_tokens,
        "context_tokens": trace.context_tokens,
        "response_text": trace.response_text,
        "created_at": datetime.now().isoformat(),
    })

# Add: metrics endpoint
def get_metrics(store: MemoryStore) -> dict:
    return {
        "facts_total": store.db.count_facts(None),
        "facts_active": store.db.count_facts("active"),
        "faiss_vectors": store.vector.index.ntotal,
        "faiss_dirty": store.db.get_meta("faiss_index_dirty", "0") == "1",
        "pending_embeddings": store.db.count_facts("pending_embedding"),
        "avg_latency_ms": average_latency(store),
        "llm_calls_today": count_llm_calls_today(store),
    }
```

---

## 7. Phase 3: Scale & Observability (P3)

**Goal:** Prepare for multi-user, add monitoring, plan for scale.  
**Duration:** Ongoing.  
**Risk:** Low — these are additive.

### 7.1. Session Management

```python
# companion/session.py
class SessionManager:
    """Per-user session with TTL and LRU eviction."""
    
    def __init__(self, max_sessions: int = 100, ttl_hours: int = 24):
        self._sessions: OrderedDict[int, UserSession] = OrderedDict()
        self._max = max_sessions
        self._ttl = ttl_hours * 3600
    
    def get_or_create(self, user_id: int) -> UserSession:
        if user_id in self._sessions:
            self._sessions.move_to_end(user_id)
            return self._sessions[user_id]
        # Evict oldest if at capacity
        while len(self._sessions) >= self._max:
            _, old = self._sessions.popitem(last=False)
            old.close()
        session = UserSession(user_id)
        self._sessions[user_id] = session
        return session
```

### 7.2. Circuit Breaker for LLM API

```python
# companion/llm/circuit_breaker.py
class CircuitBreaker:
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject calls
    HALF_OPEN = "half_open" # Testing recovery
    
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self._state = self.CLOSED
        self._failures = 0
        self._last_failure_time = 0
```

---

## 8. Migration Strategy

### 8.1. Principles

1. **Never break the running system.** Every change must be backward-compatible.
2. **Feature flags for new code paths.** New pipeline runs alongside old. Toggle via config.
3. **Database changes are additive.** New tables/columns only. No drops until verified.
4. **Tests before refactoring.** Add tests for existing behavior BEFORE changing code.
5. **One concern per PR.** Don't mix bug fixes with refactoring.

### 8.2. Migration Order

```
Phase 0 (1-2 days):
  ├─ P0-1: Fix embedding_retry_worker field name
  ├─ P0-2: Fix .gitignore
  ├─ P0-3: Remove duplicate config
  ├─ P0-4: Remove duplicate get_meta/set_meta
  ├─ P0-5: Add import json to models.py
  ├─ P0-6: Move embedding outside transaction
  ├─ P0-7: Wrap process_fact in try/except
  └─ P0-8: Fix cross-thread lock

Phase 1 (2-3 weeks):
  ├─ Week 1: Add tests for existing behavior (see §9)
  ├─ Week 2: Extract pipeline, introduce DI container
  ├─ Week 3: Fix compress pipeline (DAG), schema migrations
  └─ Week 4: Fast-path analysis, context dedup

Phase 2 (3-4 weeks):
  ├─ Week 1: Vector store protocol
  ├─ Week 2: Entity dictionary, truth verification
  ├─ Week 3: Observability (persistent traces, metrics)
  └─ Week 4: CI/CD, Docker, deployment

Phase 3 (ongoing):
  ├─ Session management with TTL
  ├─ Circuit breaker for LLM API
  ├─ Streaming responses
  └─ Multi-user support
```

### 8.3. Data Migration Safety

```python
# Every migration must:
# 1. Be idempotent (safe to re-run)
# 2. Be reversible (have a down() path, even if it's "log warning")
# 3. Be tested (migration test with real data fixtures)
# 4. Preserve all existing data

# Before any schema change:
# 1. Backup SQLite file
# 2. Run migration on backup
# 3. Verify data integrity
# 4. Apply to production
```

---

## 9. Testing Strategy

### 9.1. Priority Test Coverage

**Tier 1: Data Integrity (write first)**

| Test | What it verifies |
|------|-----------------|
| `test_add_fact_atomicity` | Fact is either fully saved or not at all |
| `test_embedding_failure_creates_pending` | Failed embedding → `pending_embedding`, not `active` |
| `test_archive_removes_from_faiss` | Archived fact not in search results |
| `test_supersede_transfers_status` | Superseded fact has correct status |
| `test_world_model_failure_preserves_fact` | Fact saved even if entity extraction fails |
| `test_compress_pipeline_survives_llm_failure` | Partial compress doesn't lose already-saved facts |
| `test_concurrent_add_fact_dedup` | Two threads adding same text → one fact |
| `test_recovery_after_crash` | Restart rebuilds FAISS, all facts searchable |

**Tier 2: Memory Lifecycle**

| Test | What it verifies |
|------|-----------------|
| `test_lifecycle_valid_transitions` | Only valid state transitions allowed |
| `test_aging_after_no_confirmation` | Pattern/insight goes stale without touch |
| `test_touch_restores_freshness` | touch_pattern bumps last_confirmed_at |
| `test_promotion_requires_span` | Pattern not promoted without time span |
| `test_revalidation_weakens_on_source_death` | Insight confidence drops when source archived |
| `test_revalidation_restores_on_source_revival` | Refuted insight restored when source comes back |
| `test_permanent_facts_never_decay` | Permanent facts immune to decay |
| `test_confidence_decay_uses_baseline` | Decay computed from original confidence, not current |

**Tier 3: Security**

| Test | What it verifies |
|------|-----------------|
| `test_injection_detected_in_fact` | Prompt injection → pending_review |
| `test_xml_tags_sanitized` | `<system>` → `‹system›` |
| `test_auth_rejects_unknown_user` | Non-admin user gets rejected |
| `test_memory_poisoning_resistance` | 10 contradictory facts don't create 10 insights |
| `test_identity_vault_rejects_low_confidence` | Core fact not overwritten by low-confidence update |

**Tier 4: Crash Recovery**

| Test | What it verifies |
|------|-----------------|
| `test_crash_during_transaction` | Transaction rolled back, no partial state |
| `test_faiss_corrupt_rebuilds` | Deleted FAISS file → rebuild on restart |
| `test_dirty_flag_triggers_rebuild` | Dirty flag → full rebuild |
| `test_event_bus_shutdown_drains` | Pending events processed before shutdown |
| `test_embedding_retry_worker_recovers` | Worker retries after API comes back |

### 9.2. Test Infrastructure

```python
# conftest.py — improvements
@pytest.fixture
def container(tmp_path, monkeypatch):
    """Full AppContainer with test configuration."""
    import companion.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))
    return create_container()

@pytest.fixture
def mock_embedding_api(monkeypatch):
    """Mock embedding API to return deterministic vectors."""
    from companion.config import EMBEDDING_DIM
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    monkeypatch.setattr("companion.memory.vector_index._embed_texts", lambda texts: [vec] * len(texts))

@pytest.fixture
def mock_llm(monkeypatch):
    """Mock all LLM calls to return predictable responses."""
    ...
```

---

## 10. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Schema migration corrupts data | Low | Critical | Backup before migration. Test on copy first. |
| Pipeline extraction breaks existing behavior | Medium | High | Add tests BEFORE refactoring. Feature flag new pipeline. |
| DI container breaks singleton-dependent code | Medium | Medium | Compatibility shims during transition. |
| Parallel compress causes LLM rate limiting | Medium | Medium | Semaphore on concurrent LLM calls. |
| Vector store protocol breaks FAISS-specific code | Low | Medium | Keep FAISS as default implementation. Protocol is additive. |
| Fast-path analysis misses important messages | Medium | Low | Configurable threshold. Fallback to LLM if unsure. |
| Truth verification rejects legitimate facts | Low | Medium | Default to permissive. Tighten over time with data. |
| Cross-thread lock change introduces deadlock | Low | Critical | Extensive testing. Lock ordering documentation. |

---

## Appendix A: File Change Map

| Phase | Files to Create | Files to Modify | Files to Delete |
|-------|----------------|-----------------|-----------------|
| P0 | `tests/test_embedding_retry_worker.py` | `embedding_retry_worker.py`, `.gitignore`, `config.py`, `sqlite_db.py`, `models.py`, `store.py` | — |
| P1 | `pipeline/` package (8 files), `container.py`, `migrations/` package (5 files) | `bot_core.py`, `main.py`, `pipeline.py`, `background_scheduler.py` | — |
| P2 | `vector_store.py`, `entity_dictionary.py`, `truth_verification.py` | `vector_index.py`, `world_model.py`, `observability.py` | — |
| P3 | `session.py`, `circuit_breaker.py` | `main.py`, `bot_core.py` | `debug_*.py`, `d`, `tall gitleaks`, `hidden.txt`, `git.txt` |

## Appendix B: Dependency Updates

```txt
# requirements.txt — pin versions
aiogram>=3.0,<4.0
google-genai>=1.0,<2.0
python-dotenv>=1.0,<2.0
pydub>=0.25,<1.0
SpeechRecognition>=3.10,<4.0
yt-dlp>=2024.0,<2027.0
pypdf>=3.0,<5.0
python-docx>=1.0,<2.0
numpy>=1.24,<2.0
faiss-cpu>=1.7,<2.0
pydantic>=2.0,<3.0
```

## Appendix C: Metrics to Track

```
latency:
  - p50, p95, p99 request latency
  - LLM call latency (per model)
  - SQLite query latency
  - FAISS search latency

memory:
  - facts_total, facts_active, facts_dormant, facts_archived
  - pending_embedding count
  - faiss_vectors count
  - faiss_dirty flag
  - sqlite_file_size_bytes

quality:
  - retrieval_precision (facts_sent vs facts_used)
  - compression_ratio (messages compressed → facts extracted)
  - contradiction_rate
  - quarantine_rate
  - pattern_promotion_rate

cost:
  - llm_calls_per_day
  - embedding_calls_per_day
  - tokens_per_request (input + output)
  - estimated_cost_per_day
```

---

*This plan preserves the soul of Amargon's Void — its cognitive memory architecture — while building the engineering foundation it needs to survive and grow.*
