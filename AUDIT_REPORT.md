# AUDIT REPORT

## 1. Dead Code (можно удалить сразу)

### 1.1 Целиком мёртвые файлы

| Файл | Строк | Почему мёртв | Решение |
|------|-------|-------------|---------|
| `companion/intents.py` | 7 | Пустая заглушка — `"replaced by LLM analyzer"` | **Удалить** |
| `companion/llm/grounding.py` | 67 | `classify_intent()` нигде не вызывается | **Удалить** |
| `companion/memory/rollback.py` | 133 | `RollbackManager` не импортируется и не инстанцируется | **Удалить** (если rollback не планируется) |
| `companion/memory/unified_profile.py` | 152 | `UnifiedProfile` не импортируется, не инстанцируется | **Удалить** (если shadow-mode abandoned) |

### 1.2 Мёртвые функции/методы

| Файл | Функция | Решение |
|------|---------|---------|
| `memory/importance.py:9` | `score_message_importance()` | Удалить (заменена LLM analyzer) |
| `memory/importance.py:58` | `retrieval_score()` | Удалить (импорт в `retrieval.py` не используется) |
| `storage/legacy.py:69` | `save_summary()` | Удалить (используется MemoryStore) |
| `storage/legacy.py:75` | `load_latest_summary()` | Удалить |
| `storage/legacy.py:86` | `load_all_summaries()` | Удалить |
| `storage/legacy.py:96` | `load_master_summary()` | Удалить |
| `storage/legacy.py:117` | `save_master_summary()` | Удалить |
| `storage/legacy.py:177` | `count_permanent_notes()` | Удалить |
| `storage/legacy.py:230` | `count_diary_entries()` | Удалить |
| `storage/sqlite_db.py:565` | `increment_fact_usage()` | Удалить (есть `increment_fact_usage_batch`) |
| `llm/client.py:263` | `aio_get_file()` | Удалить |
| `llm/client.py:227` | `aio_search_with_grounding()` | Удалить |
| `llm/client.py:274` | `async_search_with_grounding()` | Удалить |

### 1.3 Мёртвые переменные/константы

| Файл | Переменная | Решение |
|------|-----------|---------|
| `bot_core.py:53` | `_compressing_users: set[int]` | Удалить (никогда не читается) |
| `bot_core.py:266` | `SAFE_COMMANDS` | Удалить (нигде не используется) |
| `config.py:69-73` | `FACTS_PATH`, `FACT_RELATIONS_PATH`, `MESSAGES_PATH`, `REFLECTIONS_PATH`, `BELIEFS_PATH` | Удалить (SQLite заменил файлы) |
| `config.py:160` | `MONTH_NAMES` | Удалить |

---

## 2. Dead Imports (можно удалить)

| Файл | Импорт |
|------|--------|
| `bot_core.py:21` | `BASE_DIR` (из config) — используется только `SUMMARY_THRESHOLD` |
| `bot_core.py:35` | `UserState as PolicyUserState` — алиас нигде не используется |
| `policy_layer.py:23` | `import json` — never used |
| `policy_layer.py:25` | `import os` — never used |
| `memory/retrieval.py:9` | `retrieval_score` (из importance) — импортирован, но не вызван |
| `background_scheduler.py:8` | `from typing import Any` — не используется |

---

## 3. Что сломанo (требует починки)

| Путь | Проблема | Приоритет |
|------|---------|-----------|
| `services/reasoning_service.py:show_selfmap()` | Читает `self_model.data["knowledge_map"]`, но ключ называется `knowledge_domains` | **P0** — KeyError при вызове |
| `bot_core.py` | `last_activity: dict[int, datetime]` определён, но **никогда не заполняется** | **P1** — silent bug, любое чтение вернёт KeyError |
| `storage/sqlite_db.py:rollback_to()` | Не восстанавливает relations, summaries, beliefs, emotions (только факты + identity) | **P1** — если rollback когда-нибудь включат |

---

## 4. Дублирование систем (можно объединить)

| Что дублируется | Где хранится | Рекомендация |
|----------------|-------------|-------------|
| Permanent notes | `permanent_notes.txt` + `facts` (memory_kind="permanent") | **Объединить в SQLite**, файл удалить |
| User identity | `UserModel` + `IdentityVault` + personality meta + fact tags | **Свести к одному источнику истины** |
| Summaries | SQLite (`summaries` + `master_summary`) + legacy helpers | Legacy helpers уже мертвы — удалить |
| Long-term context | summaries + master summary + permanent notes + facts + `ivan.txt` | Назначить одного владельца |
| Reasoner state | файлы + JSONL + delayed file flush | Перевести весь reasoner на SQLite |

---

## 5. Что можно улучшить (не сломано, но плохо)

### 5.1 Async/Sync границы
- `user_model.py` — sync файловое I/O, вызывается из async контекста
- **Решение:** обернуть в `asyncio.to_thread()` или `run_in_executor()`

### 5.2 Private API coupling
- `sqlite_db.py` экспортирует `_insert_*`, `_content_hash`, `_load_index`, `_conn` — внутренние методы, используемые снаружи
- **Решение:** сделать публичный API, приватное — спрятать

### 5.3 ShadowEvaluator
- `shadow_eval.py` проверяет только **одно поле** (`session_data["user"]["id"]`)
- Остальные identity-проверки — `fail-open` (считают валидным при любой ошибке)
- **Решение:** расширить проверки или убрать иллюзию безопасности

### 5.4 Locking
- Нет блокировок при конкуррентном доступе к `user_chats`, `user_message_counts`, compression state
- aiogram хендлеры могут выполняться параллельно
- **Решение:** `asyncio.Lock()` на сессию

### 5.5 Startup costs
- `main.py:reindex_all()` перестраивает весь векторный индекс при каждом старте
- **Решение:** проверять хеш данных перед реиндексом

---

## 6. Что можно интегрировать (не удалять, а доработать)

| Компонент | Текущий статус | Что нужно для интеграции |
|-----------|---------------|------------------------|
| `UnifiedProfile` | Полностью мёртв | Нужен producer, который будет его заполнять. Если нужна unified identity — реанимировать. Если нет — удалить. |
| `RollbackManager` | Полностью мёртв | Нужен полный рефакторинг под текущую схему (добавить relations, summaries, beliefs, emotions). Если не нужен — удалить. |
| `classify_intent()` в `grounding.py` | Мёртв | Можно переписать как локальный fallback, если LLM недоступен. Если не нужно — удалить. |
| `score_message_importance()` в `importance.py` | Мёртв | Полезен как локальный fallback без LLM. Можно оставить, но доработать API. |

**Общий принцип:** если на интеграцию нет ресурса — удалить. Мёртвый код = технический долг.

---

## 7. Итоговая сводка

| Категория | Количество | Строк кода |
|-----------|-----------|-----------|
| Файлы целиком на удаление | 4 | ~360 |
| Мёртвые функции/методы | 13 | ~250 |
| Мёртвые переменные/константы | 7 | ~10 |
| Dead imports | 6 | 6 |
| Сломанные пути | 2 | — |
| Дублирующиеся системы | 5 | — |
| Можно улучшить | 5 | — |
| Можно интегрировать (с доработкой) | 4 | — |

**Первоочередные действия:**
1. Удалить 4 мёртвых файла
2. Удалить 13 мёртвых функций
3. Починить `show_selfmap()` (P0)
4. Починить `last_activity` (P1)
5. Объединить permanent notes в SQLite
