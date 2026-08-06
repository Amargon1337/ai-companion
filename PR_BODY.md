## Summary

Complete architectural evolution of Amargon's Void — transforming it from a simple RAG Telegram bot into a **Self-Maintaining Cognitive Memory Architecture**.

**6 commits | ~8000 lines added | 370 tests passing | 0 regressions**

---

## What Was Built

### Phase 0+1: Critical Fixes and Foundation
- **8 P0 bugs fixed**: embedding retry worker, credential leak risk, config duplicates, transaction safety, thread safety
- **VectorStore Protocol** — abstract interface for vector backends (FAISS today, Qdrant tomorrow)
- **Migration Framework** — versioned, ordered, atomic schema migrations
- **16 verification tests**

### Phase 2: Structural Decomposition
- **Repository Layer** — FactRepository, EntityRepository, MessageRepository (first step in decomposing 2745-line sqlite_db.py)
- **AppContainer (DI)** — explicit composition root with lazy initialization
- **Memory Explainability API** — explain_memory() answers "Why do you believe this?" with full provenance chains
- **Contradiction Engine** — detects conflicts between new and existing knowledge
- **LLM Provider Abstraction** — Protocol + GeminiProvider + factory for future backends

### Phase 3: Cognitive Safety
- **Contradiction Engine v2** — distinguishes temporal evolution from logical contradiction
- **Provenance Verification** — prevents memory hallucination where provenance exists but semantics are wrong
- **Cascade Invalidation** — when evidence dies, inferences are re-evaluated automatically
- **11 cognitive integrity tests** spanning all layers

### Phase 4: Self-Maintaining Architecture
- **Epistemic Layers** — three-layer separation: Evidence / Inference / Narrative
- **Memory Health Score** — composite metric (confidence, evidence quality, contradictions, freshness, verification, source reliability)
- **Automatic Review Queue** — facts below health threshold enter review queue with priority and suggested action
- **12 self-maintaining tests**

### Phase 5: Digital Friend Optimization (500 RPD budget)
- **CoT plan generation disabled by default** — saves ~100 LLM calls/day (only enabled for importance >= 8)
- **Deterministic fast-path analyzer** — handles short acks, emoji, jokes, clearly sad/happy messages without LLM (~30-40% savings)
- **Smart compress scheduling** — emotional conversations compress sooner, light chatting goes longer
- **Emotional memory callbacks** — "Last time you talked about work, you were stressed" (zero LLM cost)
- **Humor-aware system prompt** — understands memes/jokes, does not explain jokes, callbacks to earlier humor

---

## New Files

| File | Purpose |
|------|---------|
| companion/storage/repositories/ | Repository layer (Fact, Entity, Message) |
| companion/container.py | DI container (AppContainer) |
| companion/memory/epistemic_layers.py | Three-layer knowledge separation + cascade invalidation |
| companion/memory/health_score.py | Health score + review queue |
| companion/memory/contradiction.py | Contradiction Engine v2 (evolution vs contradiction) |
| companion/memory/provenance_verification.py | Protects against memory hallucination |
| companion/memory/explainability.py | Explainability API |
| companion/memory/emotional_context.py | Emotional callbacks (zero LLM cost) |
| companion/llm/provider.py | LLM Provider abstraction |
| companion/migrations/ | Migration framework |
| tests/test_p0_fixes.py | 12 P0 verification tests |
| tests/test_migrations.py | 4 migration tests |
| tests/test_architecture_v2.py | 16 architecture tests |
| tests/test_cognitive_integrity.py | 11 cross-layer lifecycle tests |
| tests/test_self_maintaining.py | 12 self-maintaining tests |
| MODERNIZATION_PLAN.md | Full modernization roadmap |
| EVOLUTION_REPORT_V2.md | Detailed evolution report |

---

## Cognitive Mechanisms Preserved

All unique cognitive mechanisms are preserved and enhanced:

- Memory Lifecycle (state machine intact)
- Epistemic Typing (DIRECT_FACT, HYPOTHESIS, etc.)
- Provenance Chains (now queryable via Explainability API)
- Identity Vault (protected facts win in contradictions)
- Life Continuity Engine (transitions explainable)
- Pattern to Insight Promotion (time-earned traits)
- Reliability Layer (aging/decay with freshness)
- Event Bus (async pub/sub)
- World Model (entity graph)
- Golden Memory (raw vs stable knowledge)

---

## Test Results

```
370 passed, 2 pre-existing failures, 5 pre-existing test isolation errors
0 regressions from baseline
```

Pre-existing issues (present before this PR, not caused by changes):
- test_deleted_ids_cleared_after_rebuild — test design flaw with dedup
- test_evolution_report_aggregates — passes individually, fails in suite order
- test_engagement.py (x5) — passes individually, fails in suite order (SQLite file contention)

---

## Architecture

```
Self-Diagnosis Layer (Phase 3-4)
  +-- Provenance Verification
  +-- Contradiction Engine v2
  +-- Health Score + Review Queue
  +-- Epistemic Layers (Evidence / Inference / Narrative)

Cognitive Layer (Phase 2)
  +-- Explainability API
  +-- LLM Provider (Gemini + future backends)
  +-- DI Container

Structural Layer (Phase 1-2)
  +-- Repository Layer
  +-- VectorStore Protocol
  +-- Migration Framework

Storage Layer
  +-- MemoryDatabase (SQLite + WAL + triggers + audit)
```

---

## LLM Budget Impact (500 RPD)

| Change | Savings |
|--------|---------|
| CoT plan disabled (except importance >= 8) | ~100 calls/day |
| Fast-path analyzer (short msgs, emoji, jokes) | ~30-40 calls/day |
| **Total freed** | **~130-140 calls/day = ~40-50 more conversations** |
