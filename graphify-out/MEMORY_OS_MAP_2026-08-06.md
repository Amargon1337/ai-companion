# Memory OS — Карта текущего состояния (2026-08-06)

Сгенерировано по: `companion/` (113 py-файлов, 22 122 LOC), `C:\Games\data\companion.db`
(user_version=2, WAL), символьному индексу Serena, live-запросам к SQLite.

---

## 1. Высокоуровневая архитектура

```
Telegram User
    │
    ▼
handlers/ (chat, commands, media)      ← aiogram, AuthMiddleware (ADMIN_IDS)
    │
    ▼
bot_core.py                            ← build_context → process_llm_request
    │  ┌──────────────────────────────────────────────────────┐
    ├─▶ MemoryStore (memory/store.py)  ← ФАСАД (95 методов)   │
    │      │  db          → MemoryDatabase (SQLite WAL)       │
    │      │  vector      → VectorIndex (FAISS HNSW + FTS5)   │
    │      │  event_bus   → MemoryEventBus (async + journal)  │
    │      │  identity    → IdentityVault (shared conn)       │
    │      │  governor    → MemoryGovernor (policies)         │
    │      │  persistence → MemoryPersistenceLayer            │
    │      │  working_memory → WorkingMemoryService (R3)      │
    │      │  council     → CouncilService (R5)               │
    │      │  tom         → TheoryOfMindEngine (R6)           │
    │      │  narrative   → NarrativeEngine (R6)              │
    │      └──────────────────────────────────────────────────┘
    │
    ├─▶ llm/pipeline.py                 ← compress: facts/reflections/patterns/beliefs
    ├─▶ llm/sessions.py                 ← system prompt (WM-блок первым)
    ├─▶ memory/retrieval.py             ← Cognitive Gravity скоринг (R5-A)
    ├─▶ reasoning.py + reasoning_engine.py
    ├─▶ learning_engine.py / cognitive_loop.py
    └─▶ proactive/ (loop, subconscious, inner_monologue, telemetry)
           + background_scheduler.py    ← nightly: health/snapshot/sleep/immune
```

## 2. Слои памяти (source of truth)

| Слой | Хранилище | Роль |
|---|---|---|
| **Факты** | `facts` (53 колонки) | Единственная правда. lifecycle: `active → dormant/pending_review/pending_embedding/quarantine → superseded/contradicted → archived → purged` |
| **Связи** | `fact_relations` | supersedes/contradicts/confirms/related_to/summarizes… |
| **Выводы** | `reflections`, `patterns`, `beliefs` | Reliability Layer: aging/stale/refuted, promotion N подтверждений |
| **Модель человека** | `human_model`, `communication_prefs`, `life_transitions` | LCE, aging/stale по дням |
| **Идентичность** | `identity_facts` + `identity_change_log` | IdentityVault, lock-политика, actor/reason |
| **Мир** | `entities`, `entity_attributes`, `entity_relations`, `entity_mentions` | World Model graph (relational, не Neo4j) |
| **Причинность** | `causal_links` (+`derived_from`, `method`) | Causal chains |
| **Цели/предсказания** | `goals`, `predictions` | Reasoning |

## 3. Когнитивный кернел (новые таблицы R1–R6)

| Таблица | Колонок | Когнитивная функция | Писатель |
|---|---|---|---|
| `memory_genome` | 9 | выживаемость памяти (survival_score, generation) | add_fact + sleep._update_genome_survival |
| `cognitive_working_memory` | 10 | bounded live-контекст диалога (TTL, cap 50) | WorkingMemoryService ← build_context |
| `event_journal` | 6 | crash-consistency: commit→journal→drain→applied | MemoryEventBus.publish/replay |
| `council_votes` | 7 | внутренний совет (5 ролей) для high-stakes | CouncilService ← add_belief |
| `theory_of_mind` | 10 | L1/L2/L3 социальная когниция (TTL 365/180/30д) | TheoryOfMindEngine.refresh |
| `homeostasis_metrics` | 8 | entropy-тред (Semantic Poisoning detector) | compute_homeostasis nightly |
| `cognitive_timeline` | 9 | трассировка тактов (perception…action) | (создана, писатель отложен) |

## 4. Событийная архитектура

- `MemoryEventBus` (async_mode, queue+worker, journal_db)
- Типы: FactCreated/Updated/Archived/Superseded/Retrieved, MutationApplied
- Журналирование: publish → `event_journal` append → worker dispatch → `applied=1`
- Replay: `replay_pending()` на старте (main.py) — краш-окно закрыто
- Subscribers: `IndexSyncService` (FAISS↔SQLite, идемпотентный по content_hash)

## 5. Транзакции и консистентность

- `atomic_memory_transaction` (BEGIN IMMEDIATE, thread-local depth, RLock)
- OCC: `expected_version` в update_fact/update_goal/upsert_world_entity
- FAISS: derived state, `faiss_index_dirty` flag, `_rebuild_index()` из facts.embedding
- Embedding ownership: delete/archive переносит blob живому сиблингу (A1 fix)
- Единое соединение: `get_shared_db()` (reasoning/user_model/self_model/telemetry)

## 6. Ночной цикл (bot_core.proactive_ping_loop, 3:00–4:59)

1. memory_health + consolidate_if_due + decay_fact_confidence
2. promote_patterns_to_insights + revalidate_insight_provenance
3. reconcile_genome_parity + audit_provenance_cycles (карантин циклов)
4. compute_homeostasis → **если entropy > τ: run_sleep_cycle + immune_audit**
5. create_snapshot (VACUUM INTO + FAISS cache) + archive_audit_log(30d)

## 7. Текущее состояние данных

**Prod-BD пуста** (дев-сброс): facts=0, messages=0, genome=0, WM=0, journal=0.
`audit_archive.db` (5.9 MB, 2954 события, 3–8 июля 2026) — след реальной истории:
2712 фактов, 242 identity-изменения. `causal_links`=4, `state_models`=1 (faiss_mapping).
Снапшоты: `data/snapshots/` — не создавался.

## 8. Тестовое покрытие

386 тестов, 0 failures. Файлы: test_cognitive_{r2,r3,a1,b1,c1,c2,c3}.py — кернел-инварианты.

## 9. Открытые пункты (roadmap)

- ~~`cognitive_timeline` — DDL есть, писатель не подключён~~ → **закрыто (R7)**: writer `CognitiveTimeline` материализует таймлайн из event_journal (watermark, идемпотентно, фазы по типам событий), archive_old(90d), на старте в main.py
- ~~R7 capacity~~ → **закрыто**: IN-chunking (500) в hydrate_fact_metadata + compute_and_cache_batch; CoW rebuild (read-фаза вне lock + swap под lock; гейт ntotal>50k / p95>2s откладывает inline rebuild); mmap отклонён (HNSW не поддерживает IO_FLAG_MMAP — задокументировано)
- ~~force sleep~~ → активен (breach → run_sleep_cycle)
- **Восстановление истории**: data/companion.db пуста (dev-reset, API-ключ невалиден 06.08); реальная история в legacy-файлах (messages.jsonl 708 сообщений 06.08–01.07, audit_archive.db 2954 события). Миграция legacy → новую БД — отдельная задача.
