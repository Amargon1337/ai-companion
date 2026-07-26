# Graph Report - .  (2026-07-23)

## Corpus Check
- 97 files · ~70,146 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1076 nodes · 2415 edges · 58 communities (48 shown, 10 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 96 edges (avg confidence: 0.58)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Route A Command From Llm Analysis To The Appropriate Service.
- Загрузить Модель Из Бд.
- .From Dict()
- .Save Event()
- Register Handlers()
- Test Run Llm Passthrough Sync Function()
- Sanitize And Scan Legacy Files()
- .  Init  ()
- Обновить Цель (Rewrite File).
- .Get Self Description()
- .From Analyzer State()
- Manages Embedding Storage In Sqlite And Provides Vector Search.
- .Async Upsert Prospective Task()
- Get Mixed Ngrams()
- Commprefextractionresult
- Уровень 6: Извлечь/Обновить Модель Человека (Выводы).      Возвращает None При
-  Fake Embed()
- Reset State()
- Analyzer.Py
- Life Continuity Engine: Найти Устойчивые Переходы Состояния Человека.      Это
- Retrieval Budget Manager — Ranked Context Within Token Budget.
- Store.Py
- Shadow Evaluates If A Proposed Identity Change Is Valid Or A Hallucination/Drift
- Temporal & Contextual Awareness Module For Amargon'S Void.
- . Upsert Prediction Conn()
- Build System Instruction()
- Edit A Fact In Place. Keeps Faiss And Db In Sync.          If The Fact Text Chan
- Mock Gemini Chat Session.
- Storage Migration.Py
- Testretrievalbudgetmanagerselect
- Delete File()
- Python-Dotenv
- Extract Comm Prefs()
- Quality Bar
- Any
- . Row Causal Link()
- 🔒 My Identity
- Pattern
- Безопасно Удалить
- Aio Delete File()
- Observation
- Path
- Test That Adding And Deleting Facts Updates The In-Memory Index Correctly.
- 1. Подробный Список Удалённых И Очищенных Объектов
-  Is Explicit Search Query()
- Personal Ai Companion — Memory Architecture.
- Proactive Interaction Loop Subsystem.
- Security/  Init  .Py
- Service Layer For Ai Companion First Flows.
- .Archive Audit Log()
- .Update Fact Fields()

## God Nodes (most connected - your core abstractions)
1. `MemoryDatabase` - 169 edges
2. `MemoryStore` - 132 edges
3. `Fact` - 59 edges
4. `UserModel` - 30 edges
5. `sanitize_markup()` - 28 edges
6. `VectorIndex` - 24 edges
7. `ReasonDecision` - 24 edges
8. `oneshot_structured()` - 23 edges
9. `FactRelation` - 23 edges
10. `ReasoningEngine` - 22 edges

## Surprising Connections (you probably didn't know these)
- `mock_memory_store()` --indirect_call--> `MemoryStore`  [INFERRED]
  tests/test_policy_engine.py → companion/memory/store.py
- `proactive_ping_loop()` --indirect_call--> `memory_store()`  [INFERRED]
  companion/bot_core.py → tests/conftest.py
- `mock_retrieval_mgr()` --indirect_call--> `RetrievalBudgetManager`  [INFERRED]
  tests/test_policy_engine.py → companion/memory/retrieval.py
- `TestContradictionFix` --uses--> `MemoryStore`  [INFERRED]
  tests/test_store_fixes.py → companion/memory/store.py
- `TestDuplicateInsertionFix` --uses--> `MemoryStore`  [INFERRED]
  tests/test_store_fixes.py → companion/memory/store.py

## Import Cycles
- None detected.

## Communities (58 total, 10 thin omitted)

### Community 0 - "Route A Command From Llm Analysis To The Appropriate Service."
Cohesion: 0.06
Nodes (73): Route a command from LLM analysis to the appropriate service., Fire-and-forget with semaphore, exception logging to logger + self_errors.jsonl., show_goals(), Команда /continuity (алиас /lce): синтез траектории личности.      reflect_on_, MemoryStore with SQLite in temp dir., show_self_description(), show_week_digest(), show_facts() (+65 more)

### Community 1 - "Загрузить Модель Из Бд."
Cohesion: 0.08
Nodes (54): Загрузить модель из БД., record_ping_sent(), Selects the highest priority reason from a list of candidates., Оценивает, уместно ли сейчас отправлять проактивный пинг., test_engagement_allowed_and_boosted(), test_engagement_too_many_ignored_pings(), _send_due_task_ping(), test_reflect_after_interaction_drift_control() (+46 more)

### Community 2 - ".From Dict()"
Cohesion: 0.05
Nodes (15): Load personality from SQLite DB (meta table)., Build one canonical user profile for prompts.          IdentityVault is the iden, Backward-compatible name for the canonical prompt profile., Reindex all facts, beliefs, reflections, and causal links into vector index., Debug assertion to ensure the lock was acquired by the caller., Load master summary from SQLite DB (meta table)., Save personality to SQLite DB., Analyze retrieval metrics and adjust fact importance based on usage patterns. (+7 more)

### Community 4 - "Register Handlers()"
Cohesion: 0.06
Nodes (29): register_handlers(), CLI script to review all quarantined items pending review across the system., main(), Каждую минуту проверяет prospective memory и обычную проактивность., Media handlers — voice, photo, video, document, tiktok., Any, wait_gemini_file_ready(), BaseMiddleware (+21 more)

### Community 5 - "Test Run Llm Passthrough Sync Function()"
Cohesion: 0.08
Nodes (34): test_run_llm_passthrough_sync_function(), An async callable that hangs must respect the timeout (not hang forever)., Real regression test for companion.llm.client.run_llm.  Reproduces the productio, Sync critical section for personality update — runs in thread.      Phase 1.3:, _validate_master_summary(), БЛОК 3: AUTO-UPDATE MASTER SUMMARY      Обновляет master summary после каждого c, Master Summary Management — БЛОК 3: Tier 3 долговременный контекст., _async_send() (+26 more)

### Community 6 - "Sanitize And Scan Legacy Files()"
Cohesion: 0.10
Nodes (25): sanitize_and_scan_legacy_files(), _looks_like_injection(), consolidate_facts(), test_extract_facts_quarantine(), extract_causal_links(), Причинно-следственная связь между событиями., Finds XML/HTML-like tags and replaces < and > with ‹ and › inside matches., auto_add_event_from_message() (+17 more)

### Community 7 - ".  Init  ()"
Cohesion: 0.10
Nodes (17): CounterValue, _timezone(), test_context_aggregator_injects_once(), TemporalContextProvider, test_temporal_counter_archive_and_soft_delete(), RuntimeContextProvider, test_schema_migration_adds_context_tables_and_fact_metadata(), VibeResolver (+9 more)

### Community 8 - "Обновить Цель (Rewrite File)."
Cohesion: 0.10
Nodes (12): Обновить цель (rewrite file)., Долговременная цель пользователя., Goal, ReasoningEngine, Загрузить активную модель мира., Построить цепочку причин-следствий., Список причинно-следственных связей., Движок разума — модель мира, цели, причинность, прогнозы. (+4 more)

### Community 9 - ".Get Self Description()"
Cohesion: 0.08
Nodes (15): Any, Модель самосознания бота., apply_critique_to_text(), Мета-мониторинг ответа перед отправкой., Логировать собственную ошибку., Self-critique logic — response quality evaluation and adjustment., run_self_critique(), SelfModel (+7 more)

### Community 10 - ".From Analyzer State()"
Cohesion: 0.10
Nodes (18): Enum, UserState, Залогировать решение policy., Policy Layer — выбор поведения, а не текста.  Вместо:   LLM → генерирует текс, Проверить и исправить ответ согласно constraints.          Это post-processing, Convert analyzer state string to UserState enum., TestPolicyLayerEnforceConstraints, ResponseMode (+10 more)

### Community 11 - "Manages Embedding Storage In Sqlite And Provides Vector Search."
Cohesion: 0.13
Nodes (14): Manages embedding storage in SQLite and provides vector search., Удалить несколько эмбеддингов и перестроить индекс ОДИН раз.         Раньше dele, Any, _get_genai_client(), Debug database schema., VectorIndex, _configure_conn(), Embed a batch of texts via Gemini API. (+6 more)

### Community 13 - "Get Mixed Ngrams()"
Cohesion: 0.11
Nodes (16): get_mixed_ngrams(), generate_reflections(), IdentityVault - Phase 2 Memory Preservation system., Format:         - deterministic         - compact         - always first section, Any, Lock for critical sections reading and mutating state.                  Contract, Connection, Reasoning Engine — активная модель мира, цели, причинно-следственные связи. (+8 more)

### Community 14 - "Commprefextractionresult"
Cohesion: 0.13
Nodes (24): CommPrefExtractionResult, Gemini API client wrapper., HumanModelItem, KnowledgeDomainsExtractionResult, Build SafetySetting list from config (env-overridable)., CausalLinkItem, PatternItem, LifeTransitionExtractionResult (+16 more)

### Community 15 - "Уровень 6: Извлечь/Обновить Модель Человека (Выводы).      Возвращает None При"
Cohesion: 0.11
Nodes (9): Уровень 6: извлечь/обновить модель человека (выводы).      Возвращает None при, HumanModelInsight, extract_human_model(), Уровень 6 + Reliability Layer: самостоятельная модель человека.      Это вывод, MessageRecord, Any, Merge delta-выводов в модель человека (Reliability Layer).          Каждый элеме, Один вывод о человеке с метаданными свежести (Reliability Layer).      Это НЕ (+1 more)

### Community 16 - " Fake Embed()"
Cohesion: 0.19
Nodes (15): _fake_embed(), test_injection_quarantine_still_pending_review(), FactRelation, make_fact(), store(), TestContradictionFix, _add(), test_oscillation_leaves_single_active() (+7 more)

### Community 17 - "Reset State()"
Cohesion: 0.21
Nodes (12): reset_state(), TestCircuitBreaker, background_personality_micro_update(), background_user_model_reflection(), Фоновое обновление user model через reflection., _check_circuit_breaker(), Background task scheduler — circuit breaker, reflection, personality micro-updat, _record_success() (+4 more)

### Community 18 - "Analyzer.Py"
Cohesion: 0.17
Nodes (19): ReflectionResult, test_oneshot_structured_personality_pipeline(), CausalLinkExtractionResult, Analyze user message using Gemini structured output.      Returns dict with va, test_oneshot_structured_causal_links(), MessageAnalysis, _default_analysis(), test_oneshot_structured_reflection() (+11 more)

### Community 19 - "Life Continuity Engine: Найти Устойчивые Переходы Состояния Человека.      Это"
Cohesion: 0.11
Nodes (6): Life Continuity Engine: найти устойчивые переходы состояния человека.      Это, Один устойчивый переход состояния человека между двумя точками во времени., _new_id(), LifeTransition, extract_life_transitions(), Подтверждение перехода при реальном использовании в retrieval.

### Community 20 - "Retrieval Budget Manager — Ranked Context Within Token Budget."
Cohesion: 0.15
Nodes (16): Retrieval Budget Manager — ranked context within token budget., Лениво считает актуальный статус старения по last_supported_at.      Не мутиру, Heuristic importance 1-10 for a single message., Лениво считает статус старения паттерна по last_confirmed_at., days_since(), Лениво: явный статус (completed/reversed/uncertain/pending_review)     приорите, Importance scoring and decay — never delete, only lower relevance., decay_factor() (+8 more)

### Community 21 - "Store.Py"
Cohesion: 0.15
Nodes (5): Unified memory store — facts, messages, relations, reflections, beliefs., SemanticImportanceRanker, Debug pipeline test failure., test_semantic_ranker_archive_filter_anchor_and_access(), Semantic importance reranking for FAISS candidates.

### Community 22 - "Shadow Evaluates If A Proposed Identity Change Is Valid Or A Hallucination/Drift"
Cohesion: 0.19
Nodes (13): Shadow evaluates if a proposed identity change is valid or a hallucination/drift, aio_oneshot(), evaluate_identity_change(), Any, test_evaluate_identity_change_invalid(), test_evaluate_identity_change_valid(), test_evaluate_identity_change_fallback(), Shadow evaluation (вторая модель проверяет первую). (+5 more)

### Community 23 - "Temporal & Contextual Awareness Module For Amargon'S Void."
Cohesion: 0.22
Nodes (16): Temporal & Contextual Awareness module for Amargon's Void., Calculate hours since the last user message in SQLite database.      Returns (ga, get_day_phase(), get_inactivity_gap(), test_temporal_guidance(), Return (phase_name_ru, phase_category)., generate_temporal_guidance(), Generate subtle behavioral guidance for the LLM companion based on time & gap. (+8 more)

### Community 24 - ". Upsert Prediction Conn()"
Cohesion: 0.15
Nodes (3): _configure_conn(), Connection, _json()

### Community 25 - "Build System Instruction()"
Cohesion: 0.20
Nodes (12): build_system_instruction(), _reconstruct_recent_history(), RetrievalBudgetManager, System prompts and LLM task templates., test_build_system_instruction_depressed_state(), mock_memory_store(), Chat session management with retrieval-augmented system prompts., Восстанавливает последние сообщения из SQLite для continuity после рестарта. (+4 more)

### Community 26 - "Edit A Fact In Place. Keeps Faiss And Db In Sync.          If The Fact Text Chan"
Cohesion: 0.12
Nodes (5): Edit a fact in place. Keeps FAISS and DB in sync.          If the fact text chan, Promote a dormant fact back to active status., Dedup via FAISS vector search cosine similarity., Phase 5: Dormant Memory System — never delete, set to dormant., Hard-delete a fact and its FAISS vector + relations.          Prefer marking `su

### Community 27 - "Mock Gemini Chat Session."
Cohesion: 0.15
Nodes (13): Mock Gemini chat session., Mock parse_json_array to return predefined data., Data models for memory architecture., sample_facts(), mock_llm_parse(), mock_llm_oneshot(), Mock companion.llm.client.oneshot to return a JSON array., Tests for RetrievalBudgetManager.select(). (+5 more)

### Community 28 - "Storage Migration.Py"
Cohesion: 0.20
Nodes (11): Записывает факт отправки пинга и возвращает его ID., test_record_ping_reply(), get_proactive_stats(), record_ping_reply(), test_record_ping_and_stats(), Помечает последний отправленный пинг как отвеченный.     Ищет последний пинг, ко, record_ping_sent(), clean_db() (+3 more)

### Community 29 - "Testretrievalbudgetmanagerselect"
Cohesion: 0.14
Nodes (8): TestRetrievalBudgetManagerSelect, ContextBundle, Importance >= 9 facts are pinned even without special tags., Web search or pytest commands must filter out unrelated personal facts., Selected context for a single LLM request., Pinned, core_identity, anchor, and permanent facts must always appear., Most recent summary should always be in the bundle., Bundle to_prompt_block must not exceed char_budget.

### Community 30 - "Delete File()"
Cohesion: 0.30
Nodes (11): delete_file(), async_oneshot(), make_config(), oneshot(), Any, format_grounding_sources(), get_file(), async_delete_file() (+3 more)

### Community 31 - "Python-Dotenv"
Cohesion: 0.17
Nodes (11): python-dotenv, SQLite встроен в Python 3 — отдельный пакет не нужен, pypdf, pydub, python-docx, yt-dlp, numpy, faiss-cpu (+3 more)

### Community 32 - "Extract Comm Prefs()"
Cohesion: 0.24
Nodes (5): extract_comm_prefs(), Merge delta-обновление предпочтений общения (авто-эволюция).          Пустые пол, Уровень 4: предпочтения общения — единая всегда-активная запись.      Хранится, CommPref, Уровень 4: извлечь/обновить предпочтения общения пользователя.      Возвращает

### Community 33 - "Quality Bar"
Cohesion: 0.20
Nodes (9): Quality Bar, Original User Request, Initial Request — 2026-07-17T09:57:12Z, Requirements, R2. Specialist Perspectives, Acceptance Criteria, Content Completeness, R1. Architectural Audit (+1 more)

### Community 34 - "Any"
Cohesion: 0.36
Nodes (8): Any, _coerce_due_ts(), _heuristic_extract(), Automatic prospective memory extraction and due-task handling., extract_prospective_tasks(), parse_json_array(), build_due_task_payload(), _looks_future_relevant()

### Community 36 - "🔒 My Identity"
Cohesion: 0.22
Nodes (8): 🔒 My Identity, User Context, Mission, Victory Audit Status, BRIEFING — 2026-07-17T09:57:12Z, 🔒 Key Constraints, Artifact Index, Project Status

### Community 37 - "Pattern"
Cohesion: 0.25
Nodes (6): Pattern, An inference over facts — e.g. 'smokes to cope with stress'.      Distinct fro, _pattern_redundant(), True if the pattern text closely mirrors an existing reflection/belief., Уровень 2: вывод паттернов поведения поверх фактов.      Паттерн — это НЕ факт, extract_patterns()

### Community 38 - "Безопасно Удалить"
Cohesion: 0.25
Nodes (7): Безопасно удалить, Дубликаты, Критически важные, ШАГ 1. Полная структура проекта, ШАГ 2 & 3. Категоризация и зависимость мусора, Возможная оптимизация, Требуют проверки

### Community 39 - "Aio Delete File()"
Cohesion: 0.38
Nodes (7): aio_delete_file(), aio_upload_file(), aio_get_file(), aio_oneshot_multimodal(), _get_aio_client(), process_multimodal_request(), Handles photo/voice inputs, extracts facts via Gemini Vision, and saves them to

### Community 40 - "Observation"
Cohesion: 0.29
Nodes (6): Observation, Verification Method, Conclusion, Caveats, Handoff Report, Logic Chain

### Community 41 - "Path"
Cohesion: 0.43
Nodes (6): Path, fix_file(), main(), Attempt to repair common UTF-8/CP1251 mojibake patterns., Fix mojibake (UTF-8/CP1251 corruption) in .jsonl files under data/., decode_mojibake()

### Community 42 - "Test That Adding And Deleting Facts Updates The In-Memory Index Correctly."
Cohesion: 0.33
Nodes (5): Test that adding and deleting facts updates the in-memory index correctly., test_faiss_correctness_after_add_and_delete(), Test that multiple search calls do not rebuild the index from SQLite., Tests for FAISS index performance and correctness (avoiding full rebuilds on sea, test_faiss_rebuild_avoidance_on_search()

### Community 44 - "1. Подробный Список Удалённых И Очищенных Объектов"
Cohesion: 0.40
Nodes (4): 1. Подробный список удалённых и очищенных объектов, Статистика очистки, 3. Сохранённые файлы (Раздел "Требуют проверки"), 2. КРИТИЧЕСКИ ВАЖНЫЕ БД (СОХРАНЕНЫ)

## Knowledge Gaps
- **45 isolated node(s):** `BRIEFING — 2026-07-17T09:57:12Z`, `Mission`, `🔒 My Identity`, `🔒 Key Constraints`, `User Context` (+40 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MemoryDatabase` connect `.Save Event()` to `Загрузить Модель Из Бд.`, `.From Dict()`, `Register Handlers()`, `Sanitize And Scan Legacy Files()`, `.  Init  ()`, `Обновить Цель (Rewrite File).`, `.Get Self Description()`, `Manages Embedding Storage In Sqlite And Provides Vector Search.`, `.Async Upsert Prospective Task()`, `Get Mixed Ngrams()`, ` Fake Embed()`, `Store.Py`, `Shadow Evaluates If A Proposed Identity Change Is Valid Or A Hallucination/Drift`, `. Upsert Prediction Conn()`, `Storage Migration.Py`, `. Row Causal Link()`, `.Get Fact()`, `.Archive Audit Log()`, `.Update Fact Fields()`?**
  _High betweenness centrality (0.314) - this node is a cross-community bridge._
- **Why does `MemoryStore` connect `.From Dict()` to `Route A Command From Llm Analysis To The Appropriate Service.`, `Загрузить Модель Из Бд.`, `.Save Event()`, `Register Handlers()`, `Test Run Llm Passthrough Sync Function()`, `Sanitize And Scan Legacy Files()`, `Manages Embedding Storage In Sqlite And Provides Vector Search.`, `Get Mixed Ngrams()`, `Уровень 6: Извлечь/Обновить Модель Человека (Выводы).      Возвращает None При`, ` Fake Embed()`, `Life Continuity Engine: Найти Устойчивые Переходы Состояния Человека.      Это`, `Store.Py`, `Shadow Evaluates If A Proposed Identity Change Is Valid Or A Hallucination/Drift`, `Temporal & Contextual Awareness Module For Amargon'S Void.`, `Build System Instruction()`, `Edit A Fact In Place. Keeps Faiss And Db In Sync.          If The Fact Text Chan`, `Mock Gemini Chat Session.`, `Extract Comm Prefs()`, `Pattern`, `Test That Adding And Deleting Facts Updates The In-Memory Index Correctly.`?**
  _High betweenness centrality (0.275) - this node is a cross-community bridge._
- **Why does `VectorIndex` connect `Manages Embedding Storage In Sqlite And Provides Vector Search.` to `.Save Event()`, `.From Dict()`, `Get Mixed Ngrams()`, `Store.Py`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Are the 18 inferred relationships involving `MemoryDatabase` (e.g. with `ContextAggregator` and `CounterValue`) actually correct?**
  _`MemoryDatabase` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `MemoryStore` (e.g. with `IdentityVault` and `SemanticImportanceRanker`) actually correct?**
  _`MemoryStore` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Fact` (e.g. with `AuthMiddleware` and `RetrievalBudgetManager`) actually correct?**
  _`Fact` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `UserModel` (e.g. with `ContextPayload` and `EngagementDecision`) actually correct?**
  _`UserModel` has 3 INFERRED edges - model-reasoned connections that need verification._