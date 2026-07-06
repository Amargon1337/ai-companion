# REPORT.md

Этот отчет содержит детальный анализ архитектуры проекта **AI-Companion** по состоянию на 6 июля 2026 года. Анализ охватывает структуру каталогов, архитектуру памяти, экстракцию фактов, механизм ранжирования и контекста (Retrieval), конфигурацию, известные проблемы и дорожную карту этапов внедрения.

---

## 1. СТРУКТУРА ПРОЕКТА

### 1.1 Полное дерево директорий
Ниже приведено полное дерево файлов и папок проекта (исключая служебные директории `.git`, `.venv`, `node_modules`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`):

```text
C:\Games
├── .gitignore
├── .pre-commit-config.yaml
├── ARCHITECTURE_AUDIT.txt
├── AUDIT_REPORT.md
├── CURRENT_STATE_AUDIT.md
├── DYNAMIC_TONE_PLAN.txt
├── FULL_CODE_AUDIT.txt
├── PROACTIVE_LOOP_PLAN.txt
├── PROJECT_STATE.md
├── SHARED_LORE_PLAN.txt
├── analyze.py
├── api.env
├── audit_metrics.json
├── audit_result.txt
├── audit_results.json
├── bot.log
├── bot.py
├── cc.bat
├── diary.txt
├── exocortex_structure.txt
├── fact_usage.json
├── git.txt
├── ivan.txt
├── master_summary.txt.bak
├── mood.jsonl
├── permanent_notes.txt
├── personality.json
├── pinned_facts.json
├── project_files.txt
├── pyproject.toml
├── refactoring_summary_and_diff.txt
├── requirements.txt
├── summaries.txt
├── todo.json
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
├── data/
│   ├── beliefs.jsonl
│   ├── causal_links.jsonl
│   ├── companion.db
│   ├── facts.jsonl
│   ├── goals.jsonl
│   ├── messages.jsonl
│   ├── policy_decisions.jsonl
│   ├── predictions.jsonl
│   ├── self_model.json
│   ├── shared_lore_candidates.jsonl
│   ├── user_model_updates.jsonl
│   └── world_model.json
├── scripts/
│   ├── debug_db.py
│   ├── debug_pipeline.py
│   └── fix_jsonl_encoding.py
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
    ├── test_shadow_eval.py
    ├── test_structured_parsing.py
    ├── test_telemetry.py
    └── test_user_model.py
```

### 1.2 Стек технологий
*   **Язык программирования:** Python 3.11 (целевая версия указана в [pyproject.toml](file:///C:/Games/pyproject.toml)).
*   **Telegram-фреймворк:** `aiogram` (асинхронное взаимодействие с Telegram Bot API).
*   **Работа с LLM:** Google Gemini API через новый SDK `google-genai`.
*   **Основная база данных:** SQLite (встроенный модуль Python `sqlite3`). Хранилище расположено в [data/companion.db](file:///C:/Games/data/companion.db).
*   **Векторный поиск:** `faiss-cpu` (библиотека FAISS от Meta для быстрого HNSW-поиска по эмбеддингам) + `numpy`.
*   **Дополнительные библиотеки:** `python-dotenv` (загрузка переменных окружения), `pydub` + `SpeechRecognition` (для распознавания голосовых сообщений), `yt-dlp` (для TikTok/видео), `pypdf` + `python-docx` (парсинг документов).
*   **Менеджер зависимостей:** `pip` с управлением через стандартный [requirements.txt](file:///C:/Games/requirements.txt).
*   **Линтер / Форматтер:** `ruff` и `mypy` для статической типизации (сконфигурированы в [pyproject.toml](file:///C:/Games/pyproject.toml)).

### 1.3 Точка входа и Запуск проекта
Точкой входа в приложение является файл [bot.py](file:///C:/Games/bot.py) в корневой папке проекта.
Содержимое [bot.py](file:///C:/Games/bot.py):
```python
"""Entry point — run: python bot.py"""
from companion.main import main

if __name__ == "__main__":
    main()
```
Запуск проекта осуществляется из командной строки:
```bash
python bot.py
```
Файл вызывает функцию `main()` из [companion/main.py](file:///C:/Games/companion/main.py), которая инициализирует бота, подключает кэширование, настраивает диспетчер хендлеров из [companion/handlers](file:///C:/Games/companion/handlers), запускает фоновый шедулер задач и активирует long-polling через `aiogram`.

---

## 2. АРХИТЕКТУРА ПАМЯТИ

### 2.1 Файлы и модули подсистем памяти
*   **Messages (Сообщения):**
    *   Схема таблицы и запись: [companion/storage/sqlite_db.py](file:///C:/Games/companion/storage/sqlite_db.py) (таблица `messages`).
    *   Модель данных: `MessageRecord` в [companion/models.py](file:///C:/Games/companion/models.py).
    *   Интерфейс логирования: `MemoryStore.log_message()` в [companion/memory/store.py](file:///C:/Games/companion/memory/store.py).
*   **Facts (Факты):**
    *   Схема таблицы и запись: [companion/storage/sqlite_db.py](file:///C:/Games/companion/storage/sqlite_db.py) (таблица `facts`).
    *   Модель данных: `Fact` в [companion/models.py](file:///C:/Games/companion/models.py).
    *   Логика сохранения/выборки: `MemoryStore` в [companion/memory/store.py](file:///C:/Games/companion/memory/store.py).
    *   Пайплайн извлечения: `extract_facts` и `consolidate_facts` в [companion/llm/pipeline.py](file:///C:/Games/companion/llm/pipeline.py).
*   **Reflections (Выводы/Рефлексия):**
    *   Схема таблицы и запись: [companion/storage/sqlite_db.py](file:///C:/Games/companion/storage/sqlite_db.py) (таблица `reflections`).
    *   Модель данных: `Reflection` в [companion/models.py](file:///C:/Games/companion/models.py).
    *   Логика сохранения/выборки: `MemoryStore` в [companion/memory/store.py](file:///C:/Games/companion/memory/store.py).
    *   Пайплайн генерации: `generate_reflections` в [companion/llm/pipeline.py](file:///C:/Games/companion/llm/pipeline.py).
*   **Beliefs (Убеждения):**
    *   Схема таблицы и запись: [companion/storage/sqlite_db.py](file:///C:/Games/companion/storage/sqlite_db.py) (таблица `beliefs`).
    *   Логика сохранения/выборки: `MemoryStore` в [companion/memory/store.py](file:///C:/Games/companion/memory/store.py).

### 2.2 Схема базы данных SQLite
Схема БД полностью инициализируется в методе `_init_schema` класса `MemoryDatabase` в [companion/storage/sqlite_db.py](file:///C:/Games/companion/storage/sqlite_db.py). 

Путь к файлу: `companion/storage/sqlite_db.py`
```sql
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  fact TEXT NOT NULL,
  date TEXT,
  created_at TEXT,
  memory_kind TEXT DEFAULT 'event',
  importance INTEGER DEFAULT 5,
  confidence REAL DEFAULT 0.8,
  source TEXT,
  source_type TEXT,
  tags TEXT DEFAULT '[]',
  status TEXT DEFAULT 'active',
  valid_from TEXT,
  valid_until TEXT,
  schema_version INTEGER DEFAULT 1,
  evidence TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
CREATE INDEX IF NOT EXISTS idx_facts_importance ON facts(importance);
CREATE INDEX IF NOT EXISTS idx_facts_date ON facts(date);

CREATE TABLE IF NOT EXISTS fact_relations (
  id TEXT PRIMARY KEY,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  created_at TEXT,
  reason TEXT,
  confidence REAL DEFAULT 0.8
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  ts TEXT,
  role TEXT,
  text TEXT,
  importance INTEGER DEFAULT 5,
  mode TEXT DEFAULT 'default',
  signals TEXT DEFAULT '[]',
  user_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_importance ON messages(importance);

CREATE TABLE IF NOT EXISTS reflections (
  id TEXT PRIMARY KEY,
  insight TEXT NOT NULL,
  based_on TEXT DEFAULT '[]',
  period TEXT,
  importance INTEGER DEFAULT 7,
  confidence REAL DEFAULT 0.8,
  status TEXT DEFAULT 'active',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS beliefs (
  id TEXT PRIMARY KEY,
  belief TEXT NOT NULL,
  based_on TEXT DEFAULT '[]',
  importance INTEGER DEFAULT 6,
  status TEXT DEFAULT 'active',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS timeline (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  event TEXT NOT NULL,
  importance INTEGER DEFAULT 5,
  description TEXT
);
CREATE INDEX IF NOT EXISTS idx_timeline_date ON timeline(date);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  user_id INTEGER PRIMARY KEY,
  message_count INTEGER DEFAULT 0,
  last_active TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS retrieval_metrics (
  message_id TEXT PRIMARY KEY,
  timestamp TEXT,
  facts_sent INTEGER,
  facts_used INTEGER,
  goals_sent INTEGER,
  goals_used INTEGER,
  reflections_sent INTEGER,
  reflections_used INTEGER
);

CREATE TABLE IF NOT EXISTS summaries (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  created_at TEXT,
  embedding_hash TEXT,
  status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  record_id TEXT NOT NULL,
  action TEXT NOT NULL,
  old_state TEXT,
  new_state TEXT,
  timestamp TEXT DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS proactive_events (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  reason TEXT NOT NULL,
  baseline_state TEXT,
  urgency INTEGER,
  message TEXT,
  sent BOOLEAN DEFAULT 1,
  user_replied BOOLEAN DEFAULT 0,
  reply_delay_hours REAL
);
```

Также в схеме создаются триггеры логирования изменений для таблицы `facts`:
```sql
CREATE TRIGGER IF NOT EXISTS audit_facts_insert
AFTER INSERT ON facts
BEGIN
    INSERT INTO audit_log (table_name, record_id, action, new_state)
    VALUES ('facts', NEW.id, 'INSERT', json_object('fact', NEW.fact, 'status', NEW.status, 'importance', NEW.importance, 'memory_kind', NEW.memory_kind));
END;

CREATE TRIGGER IF NOT EXISTS audit_facts_update
AFTER UPDATE ON facts
BEGIN
    INSERT INTO audit_log (table_name, record_id, action, old_state, new_state)
    VALUES ('facts', NEW.id, 'UPDATE', 
            json_object('fact', OLD.fact, 'status', OLD.status, 'importance', OLD.importance, 'memory_kind', OLD.memory_kind),
            json_object('fact', NEW.fact, 'status', NEW.status, 'importance', NEW.importance, 'memory_kind', NEW.memory_kind));
END;

CREATE TRIGGER IF NOT EXISTS audit_facts_delete
AFTER DELETE ON facts
BEGIN
    INSERT INTO audit_log (table_name, record_id, action, old_state)
    VALUES ('facts', OLD.id, 'DELETE', json_object('fact', OLD.fact, 'status', OLD.status, 'importance', OLD.importance, 'memory_kind', OLD.memory_kind));
END;
```

### 2.3 Хранение Embeddings и Векторная БД
*   **Где хранятся векторы:** Первичное персистентное хранилище эмбеддингов находится в той же базе данных SQLite в таблице `embeddings`:
    ```sql
    CREATE TABLE IF NOT EXISTS embeddings (
        content_hash TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        embedding BLOB NOT NULL,
        content_type TEXT DEFAULT 'fact',
        created_at TEXT DEFAULT (datetime('now'))
    )
    ```
    Вектор сериализуется в бинарный `BLOB` с помощью структуры вещественных чисел (`struct.pack` с маской `f`).
*   **Векторный поиск:** В рантайме используется `FAISS` (HNSW индекс в оперативной памяти). При инициализации класса `VectorIndex` в [companion/memory/vector_index.py](file:///C:/Games/companion/memory/vector_index.py) происходит метод `_load_index()`, который считывает эмбеддинги из SQLite, десериализует их (`struct.unpack`) и строит в памяти структуру `faiss.IndexHNSWFlat(EMBEDDING_DIM, 32)` для быстрого поиска ближайших соседей (по косинусному расстоянию нормализованных векторов).
*   **Какая векторная модель используется:** Модель `gemini-embedding-2` через Google Gemini API. Размерность векторов — `768`.

---

## 3. ЭКСТРАКТОР ФАКТОВ

### 3.1 Полный код функции извлечения фактов
Функция считывает недавние сообщения и саммери диалогового окна, отправляет их в LLM и возвращает список извлеченных структурированных фактов.

Путь к файлу: `companion/llm/pipeline.py`
```python
def extract_facts(
    store: MemoryStore,
    summary: str,
    message_ids: list[str] | None = None,
) -> list[Fact]:
    known = store.get_active_fact_texts()[-40:]
    msgs = store.recent_messages(min_importance=3, limit=80)
    msg_text = "\n".join(f"- [{m.id}] [{m.role.upper()}] [{m.importance}/10] {m.text[:300]}" for m in msgs)

    prompt = FACT_EXTRACTION_PROMPT.format(
        known_facts="\n".join(f"- {f}" for f in known) or "нет",
        summary=summary,
        messages=msg_text or "нет",
    )
    try:
        result = llm.oneshot_structured(prompt, llm.FactExtractionResult)
        raw = [f.model_dump() for f in result.facts]
    except Exception as e:
        logger.error(f"Fact extraction failed: {e}")
        return []

    created: list[Fact] = []
    source = message_ids[0] if message_ids else f"summary_{datetime.now().strftime('%Y%m%d')}"

    for item in raw:
        if not isinstance(item, dict) or not item.get("fact"):
            continue
        text = str(item["fact"]).strip()
        if store.find_similar_fact(text):
            continue

        # БЛОК 4: IDENTITY ANCHORS 2.0
        # Автоматическое тегирование anchors и core_identity
        tags = [str(t) for t in item.get("tags", [])][:5]
        fact_lower = text.lower()

        # Определяем anchor facts (причины жить, важные обещания)
        if any(kw in fact_lower for kw in ["морзик", "пёс", "собак", "обещан", "не выпил", "якор", "причина жить"]):
            if "anchor" not in tags:
                tags.append("anchor")

        # Определяем core_identity (имя, работа, основные характеристики)
        if any(kw in fact_lower for kw in ["зовут", "работа", "qa", "тестировщик", "возраст", "город"]):
            if "core_identity" not in tags:
                tags.append("core_identity")

        evidence_list = [str(e) for e in item.get("evidence_messages", [])]

        fact = Fact(
            fact=text,
            date=datetime.now().strftime("%Y-%m-%d"),
            importance=max(1, min(10, int(item.get("importance", 5)))),
            confidence=float(item.get("confidence", 0.75)),
            source=source,
            source_type="compress",
            memory_kind=item.get("memory_kind", "event"),
            tags=tags,
            evidence=evidence_list,
        )
        store.add_fact(fact)
        created.append(fact)
    return created
```

### 3.2 Дословные промпты экстракции и консолидации
Ниже приведены оригинальные строки промптов из [companion/llm/prompts.py](file:///C:/Games/companion/llm/prompts.py):

Путь к файлу: `companion/llm/prompts.py`
```python
FACT_EXTRACTION_PROMPT = """
Извлеки факты о пользователе из саммери и сообщений. Каждое сообщение имеет префикс в виде ID в скобках, например [msg_123].
Верни ТОЛЬКО JSON-объект с массивом "facts":
{{
  "facts": [
    {{
      "fact": "текст факта",
      "memory_kind": "permanent|state|event",
      "importance": 1-10,
      "confidence": 0.0-1.0,
      "tags": ["тег"],
      "evidence_messages": ["ID_сообщения_1", "ID_сообщения_2"]
    }}
  ]
}}
Правила:
- permanent: устойчивые черты, ценности, долгие интересы
- state: текущее состояние (переживает, в депрессии, бросил пить)
- event: конкретное событие в прошлом
- Не дублируй очевидное из уже известных фактов
- В поле evidence_messages обязательно впиши точные ID сообщений (например, "msg_123") из переданного списка Важных сообщений, которые подтверждают этот факт. Если подтверждается только саммери, оставь массив пустым [].
- Short messages often contain critical life changes
- ИЗВЛЕКАЙ ФАКТЫ ТОЛЬКО О ПОЛЬЗОВАТЕЛЕ. Игнорируй сообщения с ролью [ASSISTANT] или [MODEL], они не являются фактами о пользователе.

Известные факты:
{known_facts}

Саммери:
{summary}

Важные сообщения:
{messages}
"""
```

Промпт консолидации (сопоставления новых фактов со старыми):
```python
CONSOLIDATION_PROMPT = """
Сопоставь новые факты с существующими. Верни ТОЛЬКО JSON-объект с массивом "relations":
{{
  "relations": [
    {{
      "new_fact_index": 0,
      "existing_fact_id": "fact_...",
      "relation": "supersedes|contradicts|confirms|related_to",
      "reason": "кратко"
    }}
  ]
}}
Если связей нет — верни пустой массив.

Новые факты:
{new_facts}

Существующие активные факты:
{existing_facts}
"""
```

### 3.3 Атомарность факта и фильтрация мусора
1.  **Проверка дублирования и атомарности:** 
    Прежде чем сохранить факт, экстрактор делает вызов `store.find_similar_fact(text)`. Внутри [companion/memory/store.py](file:///C:/Games/companion/memory/store.py) этот метод выполняет поиск топ-5 семантически похожих фактов через векторный индекс FAISS, а затем вычисляет их посимвольное перекрытие n-грамм с помощью метода `text_overlap` из [companion/memory/text_sim.py](file:///C:/Games/companion/memory/text_sim.py):
    ```python
    def text_overlap(a: str, b: str) -> float:
        na, nb = get_mixed_ngrams(a), get_mixed_ngrams(b)
        if not na or not nb:
            return 0.0
        inter = len(na & nb)
        score_max = inter / max(len(na), len(nb))
        score_dice = (2 * inter) / (len(na) + len(nb))
        return max(score_max, score_dice)
    ```
    Если коэффициент перекрытия 2- и 3-грамм (`text_overlap`) превышает или равен `threshold` (по умолчанию `0.52`), новый факт считается дублирующим существующий и отклоняется.
2.  **Фильтрация служебной информации:**
    *   **Разделение ролей:** Промпт прямо требует извлекать факты **только** о пользователе и игнорировать реплики ассистента (`[ASSISTANT]` и `[MODEL]`).
    *   **Pydantic схемы:** Данные принудительно парсятся через валидацию Gemini Structured Output по схеме `FactExtractionResult` (класс `FactItem` с жестко типизированными полями `memory_kind: Literal["permanent", "state", "event"]`, `confidence`, `importance`). Это исключает невалидный JSON, обрывки фраз и свободный текст от модели.

---

## 4. RETRIEVAL

### 4.1 Код функции ранжирования релевантности фактов
Метод `_ranked_score` рассчитывает итоговый вес каждого регулярного факта для инжекции в контекст на основе семантического сходства, важности, фактора новизны (recency) и эмоционального соответствия.

Путь к файлу: `companion/memory/retrieval.py`
```python
        def _ranked_score(f: Fact, semantic_score: float = 0.0) -> float:
            from companion.memory.importance import decay_factor, days_since
            age = days_since(f.date or f.created_at)
            recency = decay_factor(age, f.memory_kind)
            
            if semantic_score == 0.0 and query:
                q = query.lower()
                ft = f.fact.lower()
                tags = [t.lower() for t in f.tags]
                if q in ft:
                    semantic_score = 1.0
                else:
                    qw = set(q.split())
                    fw = set(ft.split())
                    overlap = len(qw & fw) / max(len(qw), 1)
                    tag_hit = any(q in tag or tag in q for tag in tags)
                    semantic_score = min(1.0, overlap * 0.8 + (0.3 if tag_hit else 0))

            semantic = semantic_score * 0.50
            importance = (f.importance / 10) * 0.30
            recency_val = recency * 0.20
            mood_boost = mood_to_retrieval_boost(mood, f.fact) if mood else 0.0
            
            final_score = semantic + importance + recency_val + mood_boost
            
            import logging
            logger = logging.getLogger(__name__)
            logger.info("Fact %s retrieval: FAISS=%.3f Final=%.3f", f.id, semantic_score, final_score)
            return final_score
```

Коэффициент затухания `decay_factor` из [companion/memory/importance.py](file:///C:/Games/companion/memory/importance.py):
```python
def decay_factor(days_old: float, memory_kind: str) -> float:
    """Returns multiplier 0..1. Permanent facts barely decay."""
    if memory_kind == "permanent":
        return 1.0
    if memory_kind == "state":
        half_life = 45.0
    else:
        half_life = 120.0
    if days_old <= 0:
        return 1.0
    return max(0.15, math.exp(-0.693 * days_old / half_life))
```

Адаптивный буст по настроению `mood_to_retrieval_boost` из [companion/memory/retrieval.py](file:///C:/Games/companion/memory/retrieval.py):
```python
def mood_to_retrieval_boost(mood: dict[str, float] | None, fact: str) -> float:
    if not isinstance(mood, dict):
        return 0.0

    boost = 0.0
    f_lower = fact.lower()

    if mood.get("anxiety", 0) > 0.5:
        if any(kw in f_lower for kw in ["морзик", "обещан", "якор", "помогает", "справляюсь"]):
            boost += 0.3
        elif any(kw in f_lower for kw in ["техник", "дыхан", "лекарства"]):
            boost += 0.2

    if mood.get("sadness", 0) > 0.5:
        if any(kw in f_lower for kw in ["достижен", "получилось", "горжусь", "ценю"]):
            boost += 0.25
        elif any(kw in f_lower for kw in ["друзья", "поддержк", "любят"]):
            boost += 0.2

    if mood.get("anger", 0) > 0.5:
        if any(kw in f_lower for kw in ["триггер", "причина", "что помогает"]):
            boost += 0.2

    if mood.get("energy", 0.5) < 0.3:
        if any(kw in f_lower for kw in ["сон", "отдых", "режим", "энергия"]):
            boost += 0.2

    return min(0.3, boost)
```

### 4.2 Формирование контекста и лимиты (Tiers)
Контекст собирается классом `RetrievalBudgetManager` и упаковывается в XML-подобные теги через `ContextBundle.to_prompt_block()` в [companion/models.py](file:///C:/Games/companion/models.py). 

Применяются жесткие лимиты по символам (1 токен ≈ 4 символа):
1.  **Tier 0: System Identity** (`identity_vault_block`) — лимит **2000** символов (500 токенов). Защищенная информация об идентичности бота.
2.  **Tier 1: User Profile** (`personality_snapshot`) — лимит **6000** символов (1500 токенов). Динамический снимок личности пользователя.
3.  **Tier 2: Master Summary + Recent Messages** — суммарно **12000** символов (3000 токенов). Долговременный контекст и недавний диалог.
4.  **Tier 3: FAISS-ranked facts** — лимит **20000** символов (5000 токенов). Релевантные факты.
5.  **Tier 4: Reflections + Causal Links** — суммарно **8000** символов (2000 токенов).
6.  **Tier 5: Historical summaries** — лимит **8000** символов (2000 токенов).
7.  **Глобальный лимит (RETRIEVAL_CHAR_BUDGET):** По умолчанию установлен в **50 000** символов (около 12 500 токенов). Если общий объем `bundle.to_prompt_block()` превышает этот лимит, запускается последовательный механизм вытеснения: сначала удаляются старые саммари (Tier 5), затем причинные связи и выводы (Tier 4), и в последнюю очередь — обычные факты (Tier 3), за исключением «закрепленных» (pinned).

Шаблон структуры контекста:
```xml
<system_identity>
{identity_vault_block}
</system_identity>

<user_profile>
{personality_snapshot}
{user_model_context}
{unified_profile_block}
</user_profile>

<conversational_memory>
[Постоянная память]
{permanent_notes}

[Недавние реплики]
{recent_messages}

[Активные цели]
{active_goals}

[Причинно-следственный контекст]
{causal_links}

[Прогнозы и ожидания]
{predictions}

[Модель мира]
{world_model_context}

[Выводы о пользователе]
{reflections}

[Релевантные факты]
• [{memory_kind}|{importance}/10] {fact}

[Контекст саммари]
{summaries}
</conversational_memory>
```

### 4.3 Текущие веса в формуле релевантности
Формула итогового скора регулярного факта:
$$\text{Score} = (\text{Semantic} \times 0.50) + (\text{Importance} \times 0.30) + (\text{Recency} \times 0.20) + \text{MoodBoost}$$
*   **Semantic Score (50%):** Результат FAISS поиска (косинусное сходство нормализованных векторов). Если FAISS отключен или равен 0.0, используется локальный эвристический поиск по вхождению слов/тегов.
*   **Importance Score (30%):** Нормированная важность факта $(\text{importance} / 10)$.
*   **Recency Factor (20%):** Экспоненциальное затухание по времени. У permanent-фактов затухание отключено (всегда `1.0`). У state-фактов период полураспада равен 45 дням, у event-фактов — 120 дням.
*   **Mood Boost (дополнительно до +0.30):** Ситуационный бонус при совпадении ключевых слов с текущим эмоциональным фоном пользователя.

---

## 5. ИЗВЕСТНЫЕ ПРОБЛЕМЫ В КОДЕ

### 5.1 TODO, FIXME, HACK в кодовой базе
В результате полного статического анализа кодовой базы (папки `companion/`, `tests/` и корневые скрипты) с помощью регулярных выражений:
> [!NOTE]
> В Python-файлах проекта **отсутствуют** классические комментарии `# TODO`, `# FIXME` или `# HACK`. 
> 
> Единственные упоминания слова `todo` в исходном коде относятся к бизнес-логике управления пользовательскими задачами Telegram (функции `show_todos`, `add_todo` в [companion/services/reasoning_service.py](file:///C:/Games/companion/services/reasoning_service.py) и путь `TODO_PATH = todo.json` в [companion/config.py](file:///C:/Games/companion/config.py)).

### 5.2 Смешивание важности и постоянного хранения (Importance / Memory Kind)
Поле `importance` в некоторых местах кода ошибочно используется не только как вес ранжирования, но и как критерий срока жизни/хранения записи:
1.  **Retrival Pinned Override** ([companion/memory/retrieval.py:256-257](file:///C:/Games/companion/memory/retrieval.py#L256-L257)):
    ```python
    elif f.importance >= 9:
        pinned.append(f)
    ```
    Любой факт с `importance >= 9` автоматически попадает в массив `pinned` и полностью защищен от вытеснения токенов (как и permanent-факты), даже если он семантически не релевантен текущему запросу.
2.  **Decay Bypass** ([companion/memory/store.py:379-382](file:///C:/Games/companion/memory/store.py#L379-L382)):
    ```python
    if f.importance >= 8 or f.memory_kind == "permanent" or any(
        t.lower() in ["anchor", "core_identity", "pinned"] for t in f.tags
    ):
        continue
    ```
    Факты со статусом `importance >= 8` полностью исключены из процесса «затухания» важности (importance decay) и никогда не переводятся в статус `dormant`, дублируя логику `memory_kind = 'permanent'`.
*   *Решение:* Четко разделить логику: `importance` должен отвечать исключительно за ранжирование при выборке, а `memory_kind` и статус `active/dormant` — за жизненный цикл в БД.

### 5.3 Защита от Self-Prompt Injection (Реализовано)
Внедрена многоуровневая система защиты от Self-Prompt Injection (SPI) на базе 4 слоев:
1. **Слой 1 (Санитизация при записи):** Любые XML/HTML-подобные теги, поступающие от пользователя, нейтрализуются функцией `sanitize_markup()` (символы `<` и `>` заменяются на `‹` и `›`) перед сохранением в БД (`messages`, `facts`, `reflections`, `beliefs`).
2. **Слой 2 (Инструкции LLM):** В системные промпты (например, `FACT_EXTRACTION_PROMPT`) встроены жесткие правила, запрещающие экстракцию инструкций пользователя как системных правил.
3. **Слой 3 (Эвристический карантин `pending_review`):** При экстракции фактов, рефлексий или убеждений текст проверяется функцией `_looks_like_injection()`. При совпадении с паттернами инъекций записи помечаются статусом `pending_review`, что полностью исключает их из выборки контекста.
4. **Слой 4 (Ретроактивная санитизация):** Все поля `ContextBundle` перед сборкой финального промпта принудительно прогоняются через `sanitize_markup()`.

Дополнительно:
* **Карантин файлов:** Уязвимые legacy-файлы (`permanent_notes.txt` и `world_model.json`) проверяются при запуске бота; подозрительные строки извлекаются и переносятся в `permanent_notes.pending_review.txt` / `pending_review_contexts` и пишутся в `quarantine_review.log`.
* **Защита консолидации:** Метод `add_relation()` блокирует влияние недоверенных записей (`pending_review`) на легитимные активные факты (consolidation/supersede логика применяется только если оба факта имеют статус `active`).
* **Точка ревью:** CLI-скрипт `scripts/review_quarantine.py` предоставляет разработчику единую точку просмотра всех находящихся в карантине объектов в SQLite и файлах.

---

## 6. КОНФИГУРАЦИЯ

### 6.1 Переменные окружения и конфигурационные файлы
Все настройки считываются из конфигурационного файла [api.env](file:///C:/Games/api.env) (или его текстового дубликата `api.env.txt`).

Конфигурационные ключи (без секретных значений):
*   `API_TOKEN` — Токен Telegram Bot API для подключения `aiogram`.
*   `GOOGLE_API_KEY` — Ключ Google AI Studio для запросов к Gemini API.
*   `ADMIN_IDS` — Telegram ID администраторов (через запятую).
*   `LOG_PATH` — Путь к файлу логов (по умолчанию `bot.log`).
*   `LOG_LEVEL` — Уровень логирования (`INFO`, `DEBUG`, `ERROR`).
*   `EMBEDDING_MODEL` — Векторная модель (по умолчанию `gemini-embedding-2`).
*   `EMBEDDING_DIM` — Размерность вектора (по умолчанию `768`).
*   `LLM_TIMEOUT` — Таймаут запроса в секундах (по умолчанию `120`).
*   `LLM_RETRIES` — Лимит повторных попыток LLM при сбое (по умолчанию `3`).
*   `LLM_RETRY_DELAY` — Задержка перед повторным вызовом (по умолчанию `4`).
*   `REFLECTION_EVERY_N` — Интервал рефлексии (каждые N сообщений, по умолчанию `10`).
*   `DORMANT_REVIVAL_THRESHOLD` — Порог воскрешения «уснувших» фактов (по умолчанию `0.80`).
*   `LLM_COMMAND_CONFIDENCE_THRESHOLD` — Порог уверенности LLM для команд (по умолчанию `0.92`).
*   `MAX_VIDEO_DOWNLOAD_BYTES` — Ограничение загрузки видео.
*   `SPEECH_RECOGNITION_LANGUAGE` — Язык аудиосообщений (`ru-RU`).
*   `SAFETY_HARASSMENT`, `SAFETY_HATE_SPEECH`, `SAFETY_SEXUAL`, `SAFETY_DANGEROUS` — Настройки цензуры Gemini API (по умолчанию отключены, значение `BLOCK_NONE`).

### 6.2 Используемые модели LLM и параметры вызова
1.  **Основная модель (Gemini 3.1 Flash Lite):**
    *   `MODEL_NAME = "gemini-3.1-flash-lite"`
    *   Используется для рутинного анализа реплик, суммаризации, рефлексии и сборки памяти.
    *   *Параметры структурированных вызовов (`oneshot_structured`):* `response_mime_type="application/json"`, `temperature=0.1`, `max_output_tokens=8192`.
2.  **Финальный генератор ответа (Gemma 4 31B IT):**
    *   `FINAL_RESPONSE_MODEL = "gemma-4-31b-it"`
    *   Используется для компиляции итогового ответа в чат с пользователем.
    *   *Параметры:* `temperature=0.7`, `system_instruction` генерируется динамически (слайсинг контекста + Policy Engine).
3.  **Модель поиска и заземления (Gemini 2.5 Flash):**
    *   `SEARCH_MODEL = "gemini-2.5-flash"`
    *   Используется для запросов с Google Search Grounding.
    *   *Параметры:* `temperature=0.4`, активирован инструмент `google_search` от Google.

---

## 7. ТЕКУЩЕЕ СОСТОЯНИЕ ЭТАПОВ ВНЕДРЕНИЯ

Согласно планам разработки ([SHARED_LORE_PLAN.txt](file:///C:/Games/SHARED_LORE_PLAN.txt), [DYNAMIC_TONE_PLAN.txt](file:///C:/Games/DYNAMIC_TONE_PLAN.txt), [PROACTIVE_LOOP_PLAN.txt](file:///C:/Games/PROACTIVE_LOOP_PLAN.txt)), текущий статус интеграции выглядит следующим образом:

### 7.1 Спецификация Shared Lore & Inside Jokes
Дорожная карта реализации из [SHARED_LORE_PLAN.txt](file:///C:/Games/SHARED_LORE_PLAN.txt):
*   **Этап 1: Добавление таблицы `shared_lore` в SQLite**
    *   *Статус:* **НЕ реализовано**. В [companion/storage/sqlite_db.py](file:///C:/Games/companion/storage/sqlite_db.py) таблица `shared_lore` отсутствует.
*   **Этап 2: Обновление промпта рефлексии для извлечения `InsideJokes`**
    *   *Статус:* **Частично реализовано**. В [companion/user_model.py](file:///C:/Games/companion/user_model.py) промпт рефлексии расширен полем `shared_lore_candidates` с структурированными ключами (`candidate_phrase`, `candidate_context`, `confidence`).
*   **Этап 3: Запуск в режиме Dry-Run (логирование кандидатов в JSONL)**
    *   *Статус:* **Реализовано**. В методе `UserModel.reflect_after_interaction` ([companion/user_model.py:225-237](file:///C:/Games/companion/user_model.py#L225-L237)) извлеченные кандидаты записываются в файл [data/shared_lore_candidates.jsonl](file:///C:/Games/data/shared_lore_candidates.jsonl).
*   **Этап 4: Интеграция Retrieval'а (векторный поиск + keywords) в сборку промпта**
    *   *Статус:* **НЕ реализовано**. В [companion/llm/sessions.py](file:///C:/Games/companion/llm/sessions.py) и [companion/memory/retrieval.py](file:///C:/Games/companion/memory/retrieval.py) нет кода для поиска и инжекции `shared_lore`.
*   **Этап 5: Реализация MEMORY_CALLBACK в Proactive Loop**
    *   *Статус:* **НЕ реализовано**. Логика пингов не имеет коллбеков из базы знаний `shared_lore`.
*   **Этап 6: Внедрение трекинга успешных вызовов (`successful_recalls`)**
    *   *Статус:* **НЕ реализовано**.

### 7.2 Другие подсистемы (Сверка с PROJECT_STATE.md)
*   **Prompt-Based Policy Engine (Dynamic Tone V6):**
    *   *Статус:* **Реализовано и протестировано (Production Ready)**. Внедрено кэширование с помощью `hashlib.sha256`, жесткая приоритизация промптов (Core Personality -> Strategy -> Tone -> Context) в [companion/llm/sessions.py](file:///C:/Games/companion/llm/sessions.py).
*   **Context-Aware Proactive Loop (Умные пинги):**
    *   *Статус:* **В стадии тестирования (BETA)**. Файлы в `companion/proactive/` (`engagement.py`, `reasons.py`, `collector.py`, `formatter.py`, `loop.py`, `telemetry.py`) собирают контекст под строго выбранные причины (незакрытые цели, прерванный диалог, эмоциональный чекин) и пишут метрики в `proactive_events`.
*   **Миграция Legacy / Файлового хранилища:**
    *   *Статус:* **Частично реализовано (в части защиты и карантина)**. Цели, модель мира и предсказания (`reasoning.py`), а также списки задач (`todo.json`) и дневники (`diary.txt`) по-прежнему используют файловый формат JSON/txt вместо транзакционного SQLite. Однако критические для сборки контекста файлы (`permanent_notes.txt` и `world_model.json`) теперь защищены при старте бота с помощью функции `sanitize_and_scan_legacy_files()`, которая изолирует подозрительные строки в карантинные копии. Полная миграция в [data/companion.db](file:///C:/Games/data/companion.db) запланирована на следующий этап.
