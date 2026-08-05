# OMNI-COGNITIVE BLUEPRINT — Engineering Manifest for Amargon's Void Cognitive Organism

**Date:** 2026-08-04 · **Status:** Design document (no code shipped with this manifest) · **Constraint set:** AMD A8 / 8GB DDR3 / SQLite WAL / FAISS-as-index-only / no deletion, aggregate states only.

> Language discipline: terms like "stream of consciousness" are used strictly as metaphors. Every subsystem below is specified as data, states, algorithms, and measurable checks.

---

# PHASE 0 — Audit of the existing system (ground truth)

Verified against working code, not docs.

## 0.1 Existing tables (SQLite, WAL, single writer via `MemoryDatabase._lock` RLock)

Core memory: `facts, fact_relations, messages, reflections, beliefs, patterns, episodes, timeline, summaries, monthbooks`.
Models of the person: `communication_prefs, human_model, life_transitions, identity_facts, identity_change_log, state_models` (`user`, `world`, `personality_snapshot_v2`, `faiss_mapping`).
Reasoning: `goals, causal_links, predictions`.
World graph: `entities, entity_attributes, entity_relations, entity_mentions`.
Ops/telemetry: `retrieval_metrics, retrieval_replays, memory_access_log, memory_mutation_log, audit_log, proactive_events, sessions, shared_lore_candidates, temporal_counters, temporal_counter_pauses, prospective_tasks, meta`.
Vector layer: `embeddings, embeddings_fts`, `facts.embedding BLOB`, FAISS HNSW on disk, dirty-flag recovery.

## 0.2 Existing mutation paths (all verified)

- `store.add_fact` — dedup gate → governor ingestion validation → atomic txn (insert + world model + FAISS upsert) → `FactCreatedEvent`.
- `store.update_fact / delete_fact / archive_fact` — OCC (`expected_version`) + atomic txn + events; delete/archive transfer embedding to same-text live siblings (fixed this cycle).
- `store.add_relation` (`supersedes|contradicts`) — one `atomic_memory_transaction` + `FactSupersededEvent` + mutation log (fixed this cycle).
- `persistence.apply_decision` — policy decisions → entity update + `memory_mutation_log` + `MutationAppliedEvent`/lifecycle events.
- `consolidation.decay_fact_confidence / revalidate_insight_provenance / promote_patterns_to_insights` — nightly batch mutators.
- `hygiene.audit → governor.decide → persistence` — GC pipeline.

## 0.3 Existing lifecycle states (facts)

`active → dormant | pending_review | quarantine | superseded | archived → (purged)`; matrix enforced in `memory/lifecycle.py::validate_transition`, itself invoked inside every `update_fact_fields` that touches `status`. `CONTRADICTED` does **not** exist as a terminal state today — supersede covers it. Patterns: `active/superseded`. HumanModel insights: `active/aging/stale/refuted` (lazy computation).

## 0.4 Existing event flows

`MemoryEventBus` (now queue+worker), subscribers: `IndexSyncService` (FAISS↔SQLite reconcile per event), idempotent via content-hash check. Startup runs `recover_index_consistency`.

## 0.5 Existing hard constraints that the design must respect

- One shared connection, one writer lock; any new writer MUST go through `MemoryDatabase` (IdentityVault was the sole violator; fixed).
- FAISS is derived state: `_rebuild_index()` reconstructs it from `facts.embedding` — therefore *embeddings are facts-layer data, index is disposable*.
- Governor is the only legitimate policy-decision engine; no code path should mutate lifecycle outside `persistence.apply_decision` or audited store methods.
- Embedding API calls are network + latency; they must never sit inside DB critical sections (2-phase rule already established).

---

# PHASE 1 — Cognitive Kernel Architecture

Triage into fundamental (kernel), derived (services), deferrable (growth).

## 1.1 Kernel (cannot be removed without breaking the organism)

| # | Subsystem | Cognitive function | Existing substrate |
|---|---|---|---|
| K1 | **Admission Controller** (ACCEPT/QUARANTINE/REJECT/REQUEST_MORE_CONTEXT) | Gatekeeping truth intake | `FactValidationPolicy` (exists, minimal) |
| K2 | **Epistemic Typing** (`DIRECT_FACT/HYPOTHESIS/LLM_INFERENCE/PREDICTION`) + `support_count/contradiction_count` | Distinguishes observation from conjecture | partial: facts.confidence exists; counts don't |
| K3 | **Provenance Graph** | Answers "why do I believe this?" via SQL | `memory_mutation_log` + `fact_relations` + `patterns.evidence` (exists, underused) |
| K4 | **Memory Genome** (origin, survival_score, generation) | Long-run selection pressure on memories | none (new) |
| K5 | **Working Memory** (bounded, session-scoped) | Prevents O(RAM) growth of live context | none as a structure (prompt assembly is ad hoc) |
| K6 | **Lifecycle & Governance** | No-deletion state machine, OCC, audit | exists, solid |
| K7 | **Sleep Consolidation** (compress, abstract, forget-soft) | Bound memory growth, prevent Semantic Poisoning | `episodic_compression`, `apply_importance_decay`, nightly tasks exist |
| K8 | **Homeostasis metrics + Meta-auditor** | Detect drift ≠ tolerate drift | `memory_health` exists, no threshold triggers |
| K9 | **Value/Identity drift tracker (Identity Epochs)** | Change is evolution, not contradiction | `life_transitions`, `identity_change_log` exist |
| K10 | **Causal edges** | Cause→effect reasoning over the person | `causal_links` exists (thin) |

## 1.2 Derived services (built on kernel, swappable)

- **Theory of Mind levels** — derived from facts+entities+councils over existing tables; storage extends, logic is service.
- **Narrative Identity (Narrative Arcs)** — derived view over `episodes + life_transitions + patterns`, computed, not authoritative.
- **Affective tagging** — columns on `episodes`/facts, fed by the analyzer; derived.
- **Active Attention engine** — ranking function only (§MATH-2), no storage.
- **Internal Dialogue (Council)** — *evaluation function over candidate mutations*, persisted as votes; derived.
- **Cognitive timeline ("stream")** — an append-only op-log of cognitive phases; derived, externalized observability.

## 1.3 Deferred explicitly

- Cross-encoder reranker upgrades; HyDE proliferation; mmap FAISS; distributed anything — forbidden by the Iron Law or premature by scale.
- "Dream simulation" as generative content — exists as inner_monologue; kept strictly marked `source_type='system'` + `epistemic_class='LLM_INFERENCE'` so it can never masquerade as observed reality.

---

# PHASE 2 — Responsibility boundaries (per new/changed subsystem)

Format: Input / Output / Storage / Dependencies / Failure modes.

### S1 Admission Controller (extends K1)
- **In:** candidate text + `epistemic_class` hint + source metadata.
- **Out:** one of `ACCEPT | QUARANTINE | REJECT | REQUEST_MORE_CONTEXT`, written into `facts.status`/`epistemic` columns; decision row in `memory_mutation_log`.
- **Storage:** `facts` (extended), no new table.
- **Deps:** sanitizer (exists), `IdentityVault.should_lock_update`, governor policies.
- **Failure modes:** (a) over-quarantine → admission latency; monitored by `quarantine/total` ratio; (b) under-quarantine → Semantic Poisoning; mitigated by adversarial tests §5.

### S2 Epistemic Certainty Model (K2)
- **In:** every new memory; every confirm/contradict relation.
- **Out:** `confidence`, `support_count`, `contradiction_count` updates.
- **Storage:** columns on `facts`; no new table.
- **Deps:** `add_relation` hooks (`confirms`/`contradicts` relations already parsed in consolidation).
- **Failure modes:** confidence inflation from repeated self-confirmation — countered by diminishing returns (§MATH) and nightly renormalization.

### S3 Provenance Graph (K3)
- **In:** derivation edge `(derived_id, source_id, relation, method)`.
- **Out:** recursive CTE answering *why*; consumed by `/why`, Meta-auditor, contention resolution.
- **Storage:** reuse `fact_relations` (+ one materialized helper, §DDL).
- **Deps:** all mutators must write edges; enforced by code review + invariant test.
- **Failure modes:** *circular provenance* (A derived from B derived from A) — cycle check query in the nightly auditor; hallucinated evidence ids — already mitigated in patterns (evidence must resolve to shown ids); same rule extended.

### S4 Memory Genome (K4)
- **In:** creation (origin), every retrieval hit (usage), every supersession (mutation), every sentiment mark.
- **Out:** `survival_score`, `adaptation_history` (append JSON log, capped), `generation` for compressed children.
- **Storage:** `memory_genome` (new).
- **Deps:** hook into `record_fact_access` (exists), `add_relation` supersede (exists), episodic compression (exists).
- **Failure modes:** genome table doubling row count of facts — accepted (narrow columns, one row per fact, indexed only by FK).

### S5 Cognitive Working Memory (K5)
- **In:** per-turn analyzer output, retrieval top-k, open questions, current goal.
- **Out:** bounded JSON slot set reused by prompt compiler; expiry.
- **Storage:** `cognitive_working_memory` (new, small, per-user); in-memory mirror with TTL for speed.
- **Deps:** analyzer, retrieval manager.
- **Failure modes:** slot starvation on 8GB — hard cap ~50 rows/user; stale slot poisoning — `expires_at` + lazy purge.

### S6 Theory of Mind levels (derived)
- **In:** facts mentioning an entity, entity graph, council reflection.
- **Out:** level-1/2/3 claims about "what X does / believes X values / believes I understand".
- **Storage:** `theory_of_mind` (new).
- **Deps:** entity graph exists; confidence from epistemic model.
- **Failure modes:** level conflation (model states another's belief as fact) — enforced by CHECK constraint + prompt blocks label levels separately. *Any* ToM claim is `epistemic_class='LLM_INFERENCE'` by construction.

### S7 Council / Internal Dialogue (derived)
- **In:** candidate mutation (promotion, identity change, belief adoption).
- **Out:** votes `{role, verdict, reason}`, majority decision.
- **Storage:** `council_votes` (new).
- **Deps:** LLM calls — the only high-cost subsystems; gated to nightly + high-stakes mutations only (Iron Law: no per-message councils).
- **Failure modes:** self-agreement bias (same model, five hats) — mitigated by deterministic checkers cast as "roles" where possible (Critic = provenance % runnable in SQL, Guardian = injection/anchor rule engine), LLM only for open-text judgement.

### S8 Cognitive timeline (metaphor: "stream")
- **In:** instrumented pipeline stages (perception→interpretation→reflection→decision→memory_update).
- **Out:** append-only rows with latency + payload hash; powers debuggability and the Meta-auditor.
- **Storage:** `cognitive_timeline` (new).
- **Deps:** existing observability trace (`observability.py`) — this generalizes it.
- **Failure modes:** write amplification — sample or batch; values capped at one row per phase per turn (5/turn), auto-archived after 90 days (aggregate state, no delete).

---

# PHASE 3 — SQLite layer (exact DDL)

Design rules applied: each table states the cognitive function it supports; FK + CHECK everywhere; indexes only where a query exists; no wide TEXT indexes; every table appendix carries `archived` or `status` so nothing ever needs `DELETE` (Ilegitimate deletes already confined to GC internals only where irreversible corruption is at stake).

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;      -- durability adequate for derived/audit-heavy tables under single writer
PRAGMA cache_size=-20000;       -- 20MB page cache: hard cap for the 8GB box
PRAGMA foreign_keys=ON;

-- ── Amendments to existing `facts` (epistemic layer; no new table) ──────────
ALTER TABLE facts ADD COLUMN epistemic_class TEXT NOT NULL DEFAULT 'DIRECT_FACT'
  CHECK (epistemic_class IN ('DIRECT_FACT','HYPOTHESIS','LLM_INFERENCE','PREDICTION'));
ALTER TABLE facts ADD COLUMN support_count INTEGER NOT NULL DEFAULT 0
  CHECK (support_count >= 0);
ALTER TABLE facts ADD COLUMN contradiction_count INTEGER NOT NULL DEFAULT 0
  CHECK (contradiction_count >= 0);
-- New aggregate state allowed by lifecycle matrix (migration: extend validate_transition):
--   'contradicted' reachable from active; reversible to active only by new evidence.

-- ── S4 Genome ───────────────────────────────────────────────────────────────
-- Cognitive function: long-run selection pressure on memories.
CREATE TABLE IF NOT EXISTS memory_genome (
  memory_id        TEXT PRIMARY KEY REFERENCES facts(id) ON UPDATE CASCADE,
  origin           TEXT NOT NULL,                      -- message_id | compress_n | migration | dream
  generation       INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
  parent_memory_id TEXT REFERENCES facts(id),          -- compression lineage
  mutation_history TEXT NOT NULL DEFAULT '[]',         -- JSON, capped at 20 entries by writer
  adaptation_log   TEXT NOT NULL DEFAULT '[]',         -- JSON, capped at 20
  survival_score   REAL NOT NULL DEFAULT 0.5 CHECK (survival_score BETWEEN 0.0 AND 1.0),
  born_at          TEXT NOT NULL,
  last_evaluated_at TEXT
);
-- No secondary indexes: access strictly by PK (join from facts). RAM cost ~0.

-- ── S5 Working memory ───────────────────────────────────────────────────────
-- Cognitive function: bounded live context; prevents prompt-builder from
-- scanning long-term tables per turn.
CREATE TABLE IF NOT EXISTS cognitive_working_memory (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL,
  slot_type  TEXT NOT NULL CHECK (slot_type IN
             ('current_goal','active_identity','open_question','salient_fact','affective_state')),
  ref_kind   TEXT CHECK (ref_kind IN ('fact','goal','entity','none')),
  ref_id     TEXT,
  payload    TEXT NOT NULL DEFAULT '',
  salience   REAL NOT NULL DEFAULT 0.5 CHECK (salience BETWEEN 0.0 AND 1.0),
  entered_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cwm_user_live
  ON cognitive_working_memory (user_id, expires_at);   -- live-slot sweep only
-- Housekeeping trigger-free: sweeper runs `archived` flip; row cap (~50/user)
-- enforced by writer before insert.

-- ── ToM (S6) ────────────────────────────────────────────────────────────────
-- Cognitive function: layered social cognition; separates observed behavior
-- (L1) from inferred values (L2) from meta-perception (L3).
CREATE TABLE IF NOT EXISTS theory_of_mind (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON UPDATE CASCADE,
  level       INTEGER NOT NULL CHECK (level IN (1,2,3)),
  claim       TEXT NOT NULL,
  epistemic_class TEXT NOT NULL DEFAULT 'LLM_INFERENCE'
                CHECK (epistemic_class IN ('HYPOTHESIS','LLM_INFERENCE','PREDICTION')),
  confidence  REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
  basis_ids   TEXT NOT NULL DEFAULT '[]',              -- JSON: fact/pattern ids ONLY
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','superseded','archived','refuted')),
  created_at  TEXT NOT NULL,
  superseded_by INTEGER REFERENCES theory_of_mind(id)
);
CREATE INDEX IF NOT EXISTS idx_tom_subject ON theory_of_mind (subject_entity_id, level, status);

-- ── Council votes (S7) ──────────────────────────────────────────────────────
-- Cognitive function: auditable multi-role evaluation of high-stakes mutations.
CREATE TABLE IF NOT EXISTS council_votes (
  vote_id      TEXT PRIMARY KEY,
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('fact','pattern','identity','belief','transition')),
  subject_id   TEXT NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('explorer','critic','historian','predictor','guardian')),
  verdict      TEXT NOT NULL CHECK (verdict IN ('accept','reject','abstain','quarantine')),
  rationale    TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_council_subject ON council_votes (subject_kind, subject_id);

-- ── Cognitive timeline (S8) ─────────────────────────────────────────────────
-- Cognitive function: reconstruct a turn's phase sequence for audit/debug.
CREATE TABLE IF NOT EXISTS cognitive_timeline (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id   TEXT NOT NULL,
  user_id   INTEGER NOT NULL,
  phase     TEXT NOT NULL CHECK (phase IN
            ('perception','interpretation','reflection','decision','memory_update','action')),
  payload_hash TEXT NOT NULL,              -- sha256 of payload; large blobs live in payloads table if ever needed
  payload   TEXT NOT NULL DEFAULT '',      -- capped 4KB by writer
  latency_ms REAL,
  created_at TEXT NOT NULL,
  archived  INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_timeline_turn ON cognitive_timeline (turn_id, id);

-- ── Provenance helper (S3): causal edges reuse existing causal_links ────────
-- Do NOT duplicate. Add columns to the existing table:
ALTER TABLE causal_links ADD COLUMN derived_from TEXT NOT NULL DEFAULT '[]';  -- JSON fact ids
ALTER TABLE causal_links ADD COLUMN method TEXT NOT NULL DEFAULT 'llm'
  CHECK (method IN ('llm','rule','human','compression'));

-- ── Homeostasis history (K8) ────────────────────────────────────────────────
-- Cognitive function: time series for drift detection; Meta-auditor input.
CREATE TABLE IF NOT EXISTS homeostasis_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  measured_at TEXT NOT NULL,
  contradiction_density REAL NOT NULL,   -- contradicted+superseded / active
  stale_ratio REAL NOT NULL,             -- stale-or-dormant / active
  null_embedding_ratio REAL NOT NULL,    -- active facts lacking embedding
  confidence_inflation REAL NOT NULL,    -- mean(confidence) - calibrated mean
  quarantine_ratio REAL NOT NULL,
  entropy_score REAL NOT NULL            -- §MATH-3 composite
);
-- No DELETE ever; this table IS the long memory of the auditor.
```

**Migration note (metadata):** bump `PRAGMA user_version = 2`; all `ALTER`s guarded `IF NOT EXISTS`-style by the existing schema-evolution idiom in `_init_schema`; lifecycle matrix gains `contradicted` (active→contradicted; contradicted→active allowed only through a `confirms`-edge with human or ≥2 independent `support_count`).

---

# PHASE 4 — Runtime lifecycle (one turn)

```
Message arrives
      │  (perception)  → cognitive_timeline row #1
      ▼
Admission Controller (S1)        — same-thread, no LLM, <5ms budget
      │  class := DIRECT_FACT observed text; LLM output := LLM_INFERENCE/HYPOTHESIS
      ▼
Working Memory load (S5)         — ≤50 slots, TTL-expired lazily flipped to archived
      │  (interpretation) → timeline row #2
      ▼
Attention + Retrieval (§MATH-2)  — FAISS eye + gravity + recency + salience
      ▼
Reasoning (existing engine)      — read-only
      │  (reflection) → timeline row #3 (nightly on high-importance turns only)
      ▼
Governance                       — any proposed mutation routes through
      │                              Governor → Persistence (OCC + mutation log)
      ▼
Memory Mutation                  — atomic txn; FAISS write post-commit; event queued
      │  (memory_update) → timeline row #4
      ▼
Background consolidation         — sleep window only:
      update support/contradiction counts → genome scores → entropy (§MATH-3)
      → if entropy > τ: Sleep Cycle (compress → abstract → quarantine-review)
```

Budget rules (Iron Law #1): per message ≤ 2 network LLM calls on the hot path; everything else scheduled. Working memory kills the need for per-turn scans of large tables.

---

# PHASE 5 — Adversarial review (attempting to break this design)

| Attack | Where it lands | Detection | Containment |
|---|---|---|---|
| **Hallucination injection** (LLM invents fact) | Admission | `evidence_ids` must resolve; unknown-source importance cap ≤6 | `QUARANTINE`; never ACTIVE without a message-id or rule provenance |
| **False belief propagation** | Beliefs/patterns | contradiction_count > support_count × γ | auto-`contradicted`; FAISS removal happens *at the fact layer* inline + post-commit event, so the eyes stay honest |
| **Identity corruption** | IdentityVault | lock + `identity_change_log` actor/reason (shipped this cycle) | reformation only via council + human or ≥2 corroborating sources |
| **Memory explosion** | facts/embeddings | genome.survival_score floor + sleep compression deadline | compress into summary memories (generation+1), parents → `archived`, lineage kept |
| **Circular reasoning** | provenance | nightly cycle-detection CTE over `fact_relations` where relation IN (derived_from family) | break weakest-confidence edge; mark cycle members `QUARANTINE` |
| **Outdated personality model** | snapshot | `generated_at` age vs. `identity_change_log` volume | re-snapshot consolidating; snapshot is *derived*, never source of truth |
| **Semantic poisoning drift** | global | entropy series in `homeostasis_metrics` (trend, not point) | forced Sleep Cycle + human-readable report |
| **Self-confirming council** | council | deterministic roles first, LLM roles advisory only | Guardian/Critic are rule engines — cannot be talked into acceptance |

Known blind spot left deliberately: the LLM can still hallucinate *interpretation* of genuine facts. Containment is epistemic typing (its output never becomes DIRECT_FACT) plus provenance, not text filters.

---

# PHASE 6 — Roadmap (strict order)

- **R0 (this manifest) — merge-ready prerequisites, already shipped this cycle:** embedding ownership transfer at delete/archive; `add_relation` atomicity + events; IdentityVault on shared connection + attribution; async event bus; startup reconcile.
- **R1 — Kernel columns, no behavior change:** DDL migration (facts epistemic columns, causal_links columns, new tables empty); lifecycle adds `contradicted`; invariant tests on schema.
- **R2 — Counts + provenance edges:** count hooks in `add_relation`; derivation edges from consolidation/compression/analyzer; cycle detector in nightly auditor.
- **R3 — Genome + working memory:** writers + sweepers; prompt compiler switches to slot-based assembly (measurable RAM/prompt-token win).
- **R4 — Homeostasis triggers:** entropy computed nightly; forced sleep on τ breach; `/memory_health --deep` surfaces the series.
- **R5 — Council on high-stakes mutations only** (identity, belief adoption, promotion refusal), votes persisted.
- **R6 — ToM + narrative arcs as read-models** (derived views, no authority).
- **R7 — Capacity work (former "Phase E")**: CoW FAISS rebuild, IN-chunking, mmap reads — triggered only by measured thresholds (ntotal > 50k or rebuild p95 > 2s).

Each R-step is independently revertible and never overlaps a data migration with a logic change.

---

# MATH (measurable, no mysticism)

### M1 — Cognitive gravity of a memory
$$G_{mem} = I \cdot W_{emo} \cdot R_{id} \cdot S_{hist} \in [0,1]$$

- **Importance** $I$: `importance/10` blended with usage precision, $I = 0.7\cdot\frac{imp}{10} + 0.3\cdot\frac{used}{sent}$ (clamped).
- **Emotional weight** $W_{emo} = 0.5 + 0.5\cdot\lVert E \rVert$, where $\lVert E\rVert$ is the L1 norm of the episode's emotion vector (stored on `episodes.emotions`), so neutral memories sit at 0.5, charged ones approach 1.
- **Identity relevance** $R_{id}$: fraction of the fact's resolved entities that appear in IdentityVault or L2 ToM claims; 0.2 floor so nothing is zeroed.
- **Historical stability** $S_{hist} = \frac{survival\_score + (g/(g+1))}{2}$, where $g$ = times confirmed (`support_count`); memories that mutate rather than die accumulate stability instead of vanishing.

### M2 — Hybrid retrieval score
For candidate $m$ with FAISS cosine $c \in [-1,1]$:

$$\text{sim} = \frac{c+1}{2},\qquad \text{recency} = e^{-\lambda \Delta t},\; \lambda = \frac{\ln 2}{30\,\text{d}}$$

$$\text{score} = \sigma\Big(\alpha\,\mathrm{z}(\text{sim}) + \beta\,\mathrm{z}(G_{mem}) + \gamma\,\mathrm{z}(\text{recency}) + \delta\,\mathrm{z}(\text{salience}) + \eta\,\mathrm{z}(\text{BM25})\Big)$$

with $\sigma$ logistic, $z$ per-batch z-normalization (prevents any single channel dominating), defaults $\alpha .35,\; \beta .25,\; \gamma .15,\; \delta .15,\; \eta .10$; salience comes from working-memory slot overlap (0/1). All terms are table columns or the FAISS score — retrieval stays O(FAISS) + one indexed lookup per candidate.

### M3 — Homeostasis entropy
$$H = w_1 \rho_{contra} + w_2 \rho_{stale} + w_3 \rho_{nullEmb} + w_4 \rho_{infl} + w_5 \rho_{quar}$$

(all ratios already persistable to `homeostasis_metrics`; weights sum to 1, defaults .30/.25/.20/.15/.10).

Sleep Cycle is forced when the **3-sample moving average** $\bar H_3 > \tau$ (τ default 0.35) — a single noisy night never triggers it, a monotone drift always does. $\rho_{infl}$ = `mean(active confidence)` − calibrated expectation computed from prediction-track record, handling class imbalance by 0.05-binning.

---

# 3. FSM — hypothesis lifecycle

```
                REJECT (sanitizer/Admission)
                    ▲
   new candidate ──► HYPOTHESIS ──► QUARANTINE
                    │                  │ (council vote / N supports, span-guard)
                    │                  ▼
                    │              ACCEPT ──► ACTIVE(DIRECT-ish inference)
                    ▼                  │
       REQUEST_MORE_CONTEXT            ▼ supports/contradicts accumulate
                                       ▼
                                  BELIEF(promoted pattern)
                                  │   contradicts > supports·γ
                                  ▼            ┌──────────────┐
                              CONTRADICTED ──► │ ACTIVE again │  only via new confirms-edge
                                  ▼            └──────────────┘
                              ARCHIVED (age/policy)
```
FSM is enforced where facts already are: `update_fact_fields` → `validate_transition`; extension is one more vertex/edges. Council never writes directly — it *proposes*, Persistence *decides*.

---

# 4. Algorithms (pseudocode)

```python
# ConsciousnessStream.tick() — one turn, hard budgeted
def tick(msg, ctx):
    t0 = clock()
    tl.log("perception", hash(msg))
    admission = admit(msg)                       # S1, no LLM
    if admission.decision == REJECT: return reply_refusal()

    wm  = WorkingMemory.load(ctx.uid)            # S5: ≤50 live slots, TTL sweep
    ret = retrieve(query=msg, wm=wm)             # M2 scorer over FAISS+DB
    tl.log("interpretation", ret.hash())

    plan = reason(msg, ret, wm)                  # read-only reasoning
    proposed = plan.mutations                    # zero or more
    for m in proposed:
        if stakes(m) >= HIGH and council_required(m.kind):
            votes = Council.vote(m)              # deterministic roles first
            if not majority_accept(votes): m.quarantine(); continue
        governor.apply(m)                        # atomic txn + events + id-log
    tl.log("decision", plan.hash())

    answer = compose(plan)
    wm.upsert(delta_of(plan))                    # bounded write
    tl.log("memory_update", wm.hash())
    return answer
```
```python
# ImmuneResponse.audit() — nightly, deterministic first, LLM never in control
def audit(db):
    viral = db.query("""
      SELECT f.id FROM facts f
      WHERE f.status='active' AND f.epistemic_class IN ('LLM_INFERENCE','HYPOTHESIS')
        AND NOT EXISTS (SELECT 1 FROM memory_access_log a WHERE a.fact_id=f.id)
        AND julianday('now') - julianday(f.last_retrieved_at) > :stale_days
    """)                                        # unseen inferences: primary virus class
    inflate = db.query("""SELECT id FROM facts WHERE status='active'
                          AND confidence > :cap AND support_count = 0
                          AND epistemic_class <> 'DIRECT_FACT'""")
    cycles  = provenance_cycles(db)              # recursive CTE over derivation edges
    propose_all(viral + inflate + cycles, action='quarantine', reason='immune_audit')
    compute_homeostasis(db)                      # writes homeostasis_metrics row
    if moving_avg('entropy_score', 3) > TAU: force_sleep_cycle()
```

---

# 5. Testing & validation strategy (Manual QA discipline)

**Conflict Resolution Engine**
- *Equivalence partitions*: pairs (DIRECT×DIRECT), (DIRECT×HYPOTHESIS), (HYP×LLM_INFERENCE), (anchor-tag × anything), (permanent × anything) — outcome must follow authority, not recency.
- *Boundary values*: promotion at `PROMOTION_MIN_OBSERVATIONS` = 3 → asserts at N=2 (no), 3 (yes), 4 (no double-promote); span-days at `min_span − 1 / = / + 1`; confidence exactly at archive threshold 0.30 / 0.299.
- *Pairwise*: axes {epistemic class, anchor?, protected?, relation type}; full grid is 5×2×2×4=80 — pairwise shrinks it to ~14 cases via all-pairs tool, committed as parameterized tests.

**Identity lock-in defence**
- Differential replay: seed timeline where value A→B has ≥2 corroborations; assert vault accepts; without → reject. Boundary: `text_overlap` at 0.49/0.5/0.8/1.0 around the existing lock thresholds.
- Regression guard: `identity_change_log` row must exist for every non-NO_CHANGE write (invariant test scanning both tables).

**Property-based invariants** (Hypothesis where available, else randomized smoke): no ACTIVE fact lacks resolvable provenance; every supersession writes exactly one event; genome row ⟺ fact row (1:1); working-memory live slots ≤ 50 at any assert point.

---

# 6. 10-year failure-mode analysis (~1M messages)

1. **ToM level drift** — level-3 claims (meta-perception) age fastest and are pure inference. Counter: hard TTL per level (L1 none, L2 180d, L3 30d) expressed as `expires_at` + lazy `archived` flip — no deletions, staleness visible in prompts.
2. **Narrative arc fragmentation** — millions of episodes → thousands of arcs → prompt incoherence. Arcs are *derived*, recomputed from waypoint memories (genomically top-quartile survival_score), so the narrative cannot diverge from what memory still holds.
3. **Confidence mass inflation** — nightly `$mean(confidence)>` calibrated band triggers renormalization spread over `support_count` only (never touching DIRECT_FACT of human origin).
4. **Provenance graph bloat** — relation rows grow linearly; queries stay indexed; the Meta-auditor tracks median depth and forces compression when depth p95 > 12 (chain-of-hearsay defense).
5. **Existential consistency check (Meta-auditor)** — the periodic job that recomputes snapshot ⟂ facts disagreement, vault-vs-ToM contradictions, entropy trend; on breach it *reforms identity* by proposing (council-gated) a new snapshot derived from raw memory, archiving the old one with full provenance — the system can change its mind about itself with an audit trail.

**Iron Law check:** everything above is SQLite DDL + ranking math + scheduled batch jobs. No services, no brokers, no clusters. Recoverable: drop `faiss_index.bin` → `_rebuild_index()`; drop any derived table → recompute from facts + logs. Nothing canonical lives outside the one file.
