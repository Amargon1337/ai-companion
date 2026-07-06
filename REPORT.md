# REPORT.md

# AI-Companion: архитектурный и технический аудит кодовой базы

Дата аудита: 2026-07-06  
Область анализа: статический анализ текущих файлов проекта, прежде всего `companion/` и точек входа.  
Стек: Python 3.11, aiogram, SQLite/WAL, FAISS HNSW, google-genai SDK.

---

## 1. СТРУКТУРА И АРХИТЕКТУРА ПРОЕКТА

### 1.1. Актуальное дерево проекта

```text
.
├── bot.py
├── requirements.txt
├── pyproject.toml
├── api.env
├── bot.log
├── audit_metrics.json
├── audit_results.json
├── diary.txt
├── fact_usage.json
├── git.txt
├── ivan.txt
├── mood.jsonl
├── permanent_notes.txt
├── personality.json
├── pinned_facts.json
├── summaries.txt
├── todo.json
├── timeline.jsonl.bak
├── analyze.py
├── cc.bat
├── scripts/
│   ├── debug_db.py
│   ├── debug_pipeline.py
│   ├── fix_jsonl_encoding.py
│   ├── review_quarantine.py
│   └── sanitize_existing_data.py
├── data/
│   ├── companion.db
│   ├── companion.db-shm
│   ├── companion.db-wal
│   ├── beliefs.jsonl
│   ├── causal_links.jsonl
│   ├── facts.jsonl
│   ├── goals.jsonl
│   ├── messages.jsonl
│   ├── policy_decisions.jsonl
│   ├── predictions.jsonl
│   ├── self_model.json
│   ├── shared_lore_candidates.jsonl
│   ├── user_model_updates.jsonl
│   └── world_model.json
├── companion/
│   ├── __init__.py
│   ├── background_scheduler.py
│   ├── bot_core.py
│   ├── config.py
│   ├── critique_manager.py
│   ├── documents.py
│   ├── grounding_handler.py
│   ├── main.py
│   ├── models.py
│   ├── policy_layer.py
│   ├── reasoning.py
│   ├── runtime_state.py
│   ├── self_model.py
│   ├── user_model.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   └── media.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── analyzer.py
│   │   ├── client.py
│   │   ├── master_summary.py
│   │   ├── pipeline.py
│   │   ├── prompts.py
│   │   ├── sessions.py
│   │   └── shadow_eval.py
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── identity_vault.py
│   │   ├── importance.py
│   │   ├── retrieval.py
│   │   ├── store.py
│   │   ├── text_sim.py
│   │   └── vector_index.py
│   ├── proactive/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   ├── engagement.py
│   │   ├── formatter.py
│   │   ├── loop.py
│   │   ├── reasons.py
│   │   └── telemetry.py
│   ├── security/
│   │   ├── __init__.py
│   │   └── sanitizer.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── memory_service.py
│   │   ├── reasoning_service.py
│   │   └── report_service.py
│   └── storage/
│       ├── __init__.py
│       ├── jsonl.py
│       ├── legacy.py
│       └── sqlite_db.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_background_scheduler.py
    ├── test_collector.py
    ├── test_critique_manager.py
    ├── test_engagement.py
    ├── test_faiss_performance.py
    ├── test_formatter.py
    ├── test_grounding_handler.py
    ├── test_pipeline.py
    ├── test_policy_engine.py
    ├── test_policy_layer.py
    ├── test_reasons.py
    ├── test_retrieval.py
    ├── test_sanitizer.py
    ├── test_shadow_eval.py
    ├── test_structured_parsing.py
    ├── test_telemetry.py
    └── test_user_model.py
```

### 1.2. Точный стек технологий

```text
Python target: 3.11

Runtime:
- aiogram
- google-genai
- python-dotenv
- pydub
- SpeechRecognition
- yt-dlp
- pypdf
- python-docx
- numpy
- faiss-cpu
- sqlite3 из stdlib

Static tooling:
- ruff target-version = py311
- ruff line-length = 120
- mypy python_version = 3.11
```

Модели и runtime-константы:

```python
MODEL_NAME = "gemini-3.1-flash-lite"
FINAL_RESPONSE_MODEL = "gemma-4-31b-it"
SEARCH_MODEL = "gemini-2.5-flash"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

SUMMARY_THRESHOLD = 50
RETRIEVAL_CHAR_BUDGET = 50_000
RETRIEVAL_MAX_FACTS = 25
RETRIEVAL_MAX_REFLECTIONS = 5
REFLECTION_EVERY_N = 10
DORMANT_REVIVAL_THRESHOLD = 0.80
LLM_COMMAND_CONFIDENCE_THRESHOLD = 0.92
```

Точка входа:

```python
# bot.py
from companion.main import main

if __name__ == "__main__":
    main()
```

Runtime-цепочка запуска:

```text
bot.py
└── companion.main.main()
    └── asyncio.run(run())
        ├── sanitize_and_scan_legacy_files()
        ├── rotate_jsonl(messages/policy/user_model_updates)
        ├── memory_store.reindex_all()
        ├── memory_store.vector.test_embeddings()
        ├── Bot(token=API_TOKEN)
        ├── Dispatcher()
        ├── AuthMiddleware()
        ├── register_handlers(dp, bot)
        ├── setup_bot_commands(bot)
        ├── create_task(proactive_ping_loop(bot))
        └── dp.start_polling(bot, handle_as_tasks=True)
```

### 1.3. Маппинг компонентов `companion/`

| Файл | Runtime-задача |
|---|---|
| `companion/main.py` | Инициализация процесса: логирование, auth middleware, bootstrap legacy sanitation, JSONL rotation, timeline migration, FAISS reindex, embedding health-check, запуск polling и proactive loop. |
| `companion/config.py` | Централизованная конфигурация путей, env-переменных, моделей, лимитов retrieval/LLM/media, safety settings. Также bootstrap legacy-файлов `personality.json`, `ivan.txt`. |
| `companion/bot_core.py` | Главный runtime-оркестратор диалога: singleton `MemoryStore`, `RetrievalBudgetManager`, in-memory Gemini sessions, rate-limit, command routing, context build, retrieval loading, LLM response generation, compression trigger, utilization metrics. |
| `companion/models.py` | Dataclass-модели памяти: `Fact`, `FactRelation`, `MessageRecord`, `Reflection`, `ContextBundle`. Также сериализация prompt context в XML-подобные секции. |
| `companion/runtime_state.py` | DTO состояния одного запроса: user message, mood, intent, command, policy, reasoning context, LLM response, critique. |
| `companion/policy_layer.py` | Rule-based policy layer: mapping `UserState -> PolicyDecision`, constraints injection into prompt, post-processing ограничения количества вопросов, JSONL-лог policy decisions. |
| `companion/reasoning.py` | Reasoning engine: goals, world model, causal links, predictions. Часть данных остается в JSON/JSONL, часть интегрируется в prompt context. |
| `companion/user_model.py` | Целостная модель пользователя: identity, beliefs, patterns, emotional timeline, proactivity counters. Сейчас persisted в SQLite `meta.user_model`, но часть telemetry все еще пишется в JSONL. |
| `companion/self_model.py` | Self-awareness model бота: strengths/weaknesses/confidence domains/knowledge domains, self critique, self error logging. Persisted в `data/self_model.json`. |
| `companion/background_scheduler.py` | Fire-and-forget scheduler: semaphore, active task registry, circuit breaker, background user model reflection, periodic personality micro-update. |
| `companion/critique_manager.py` | Thin wrapper над `self_model.critique_response()`; применяет confidence warnings и fallback-prefix к ответу. |
| `companion/grounding_handler.py` | Google Search grounding flow: сбор retrieval context, вызов `search_with_grounding`, fallback в обычный chat при ошибке. |
| `companion/documents.py` | Обработка document upload: чтение txt/pdf/docx, fallback upload в Gemini files API, truncation по `MAX_DOCUMENT_CHARS`. |
| `companion/handlers/__init__.py` | Регистрация chat/media handlers и Telegram bot commands. |
| `companion/handlers/chat.py` | Command surface и текстовый ingress: `/start`, `/help`, `/search`, `/summary`, `/personality`, `/remember`, inline callbacks, command confirmation, TikTok handler. |
| `companion/handlers/media.py` | Voice/photo/sticker/video/document handlers. Делает STT, загрузку файлов, Gemini file upload, передачу payload в `process_llm_request`. |
| `companion/services/memory_service.py` | Команды памяти: remember, show facts, show notes, diary, timeline/year, auto event extraction. |
| `companion/services/reasoning_service.py` | Команды reasoning/todo/goals/selfmap. Все todo/goals пока через legacy JSON/JSONL. |
| `companion/services/report_service.py` | Summary/personality/selfie/week digest/monthbook/retrospective/context reports. |
| `companion/storage/sqlite_db.py` | Primary SQLite backend: schema init, CRUD facts/messages/reflections/beliefs/summaries/timeline/sessions/metrics/proactive events, audit triggers. |
| `companion/storage/legacy.py` | Остаточный file-storage слой: diary, permanent notes, todos, monthbook, ivan.txt, mood, legacy summaries. Timeline уже прокинут в SQLite через `memory_store.db`. |
| `companion/storage/jsonl.py` | Append-only JSONL utilities и rotation. Используется для policy logs, goals, causal links, predictions, self errors, user model updates. |
| `companion/security/sanitizer.py` | Минимальная защита от XML/HTML-like prompt injection: замена `<tag>` на `‹tag›`, regex detection подозрительных русских SPI-маркеров. |
| `companion/memory/store.py` | Unified memory facade: facts/messages/reflections/beliefs/summaries/personality/master summary; dedup; FAISS integration; dormant revival; decay; usage feedback. |
| `companion/memory/vector_index.py` | Embedding cache в SQLite + in-memory FAISS `IndexHNSWFlat`. Batch embedding через Gemini, blob serialization, search. |
| `companion/memory/retrieval.py` | Retrieval Budget Manager: pinned guarantee, formula ranking, tier budgets, context eviction. |
| `companion/memory/importance.py` | Heuristic importance/decay helpers. Важное: `retrieval_score()` сейчас не вызывается production retrieval. |
| `companion/memory/text_sim.py` | Character n-gram similarity for Russian-friendly dedup/overlap. |
| `companion/memory/identity_vault.py` | IdentityVault table and lock policy for core identity facts. Включает audit triggers. |
| `companion/llm/client.py` | google-genai client wrapper: