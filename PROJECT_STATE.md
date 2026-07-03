# PROJECT STATE

**Дата:** 2026-07-03

Документ отражает текущее реальное состояние кодовой базы бота AI-Companion на основе проведенных статических аудитов, поиска по файлам и последних ручных исправлений. Все выводы опираются исключительно на существующий код.

---

## 1. Что уже исправлено
*   **Баг с картой знаний (`show_selfmap`)**: Исправлено падение с `KeyError`. Функция в `reasoning_service.py` теперь безопасно обращается к актуальному ключу `.get("knowledge_domains", {})` вместо устаревшего `knowledge_map`.
*   **Счетчик активности (`last_activity`)**: Восстановлено обновление таймера в `bot_core.py`. Теперь переменная заполняется актуальным UNIX-таймстемпом при обработке каждого нового сообщения от пользователя.
*   **Утечка ошибок в `documents.py`**: Убран сырой вывод системных исключений Python (traceback) в чат. Внедрена лаконичная пользовательская заглушка об ошибке обработки.

---

## 2. Ложноположительные результаты прошлых аудитов
*   **Типизация `last_activity`**: Старый `AUDIT_REPORT.md` утверждал, что `last_activity` является словарем `dict[int, datetime]`. Анализ кода подтвердил, что и аннотация (`dict[int, float]`), и реальное использование (результат `time.time()` и математика в секундах) строго опираются на `float`. Ошибки в коде не было, ошибка была в тексте аудита.
*   **"Мертвые" хендлеры (vulture false-positives)**: Инструменты статического анализа часто помечают как неиспользуемые функции хендлеров команд (например, `cmd_start`, `cmd_help`), так как они не вызываются напрямую в коде, а регистрируются фреймворком `aiogram` через декораторы или роутеры.

---

## 3. Проблемы, подтвержденные кодом
*   **Дыра в безопасности Identity (`ShadowEvaluator`)**: Компонент валидирует только одно поле (`who_they_are`). Остальные сущности (`ambitions`, `fears`, `core_traits`, `values`) в `user_model.py` игнорируют проверку и пишутся в БД с аргументом `explicit_overwrite=True`.
*   **Блокирующий I/O и потоки (`UserModel.reflect_after_interaction`)**: Внутри асинхронной среды используется `threading.RLock`, внутри которого производятся блокирующие I/O запросы к SQLite. Это может приводить к зависанию event-loop.
*   **Утечка памяти в `_compression_locks`**: В `bot_core.py` создаются `asyncio.Lock` для каждого `user_id`, но не предусмотрено механизма их очистки при завершении сессий.
*   **Тяжелый старт (`reindex_all`)**: При каждом запуске `bot_core` происходит полная пересборка векторного индекса, независимо от того, изменились ли данные.

---

## 4. Какие подсистемы реально используются в рантайме
*   **Точка входа и оркестрация**: `main.py` инициализирует бота, `bot_core.py` отвечает за маршрутизацию и контекст.
*   **LLM и логика**: Набор модулей `llm/` (`client.py`, `analyzer.py`, `sessions.py`, `pipeline.py`, `master_summary.py`) выполняет задачи от структурированного парсинга намерений до компрессии памяти.
*   **Память и векторный поиск**: Фасад `MemoryStore`, работающий поверх `sqlite_db.py`, `identity_vault.py` и `vector_index.py`.
*   **Background Worker**: Планировщик фоновых задач (`background_scheduler.py`) и проактивный пинг в `bot_core.py`.
*   **Telegram Ingress**: Модули `handlers/chat.py` и `handlers/media.py` для приема данных через aiogram.

---

## 5. Какие файлы были удалены (Мертвый код)
*   `companion/intents.py` (полностью заменен логикой LLM-анализатора).
*   `companion/memory/rollback.py` (был отключен, архитектурно устарел и не покрывал новые таблицы БД).
*   `companion/memory/unified_profile.py` (пережиток старой концепции shadow-mode без инициализации).
*   `companion/llm/grounding.py` (функция локального fallback-классификатора `classify_intent` больше не применялась).

---

## 6. Компоненты — источники правды (Source of Truth)
В данный момент система живет в гибридном состоянии хранения:
1.  **Основная база (SQLite)**: Главный источник правды для фактов, сообщений, сводок (summaries), убеждений (beliefs) и таймлайнов.
2.  **IdentityVault (SQLite)**: Отдельная защищенная таблица, являющаяся источником правды для идентичности бота.
3.  **Векторный HNSW-индекс (RAM / SQLite)**: Вторичное представление фактов в виде эмбеддингов для семантического поиска (первично хранится в SQL).
4.  **Файловая система (JSON / JSONL)**: Временный, но активный источник правды для подсистемы Reasoning (цели, каузальные связи, модель мира, предсказания) и части логов `UserModel`.
5.  **Text files**: Статические файлы (`ivan.txt`), а также дублирующийся источник для постоянных заметок (`permanent_notes.txt`).

---

## 7. Следующие 10 задач с максимальным приоритетом (Roadmap)

1.  **Перенос подсистемы Reasoning в SQLite**: Отказаться от JSON/JSONL для целей, модели мира и предсказаний. Внедрить их в БД для транзакционной целостности вместе с остальной памятью.
2.  **Устранение дублирования Permanent Notes**: Прекратить запись в `permanent_notes.txt`. Оставить работу с постоянными фактами исключительно через `Fact(memory_kind="permanent")` в SQLite.
3.  **Рефакторинг `UserModel.reflect_after_interaction`**: Вынести синхронные операции с базы внутри `RLock` в отдельный поток через `asyncio.to_thread()` или перевести на полностью асинхронные SQL-вызовы.
4.  **Устранение дублирования личности (Identity/Profile)**: Свести профиль пользователя и личность бота (сейчас разбросано между `UserModel`, `IdentityVault` и SQLite Meta) в одну сущность.
5.  **Оптимизация реиндексации FAISS при старте**: Сохранять хеш состояния базы и перестраивать `reindex_all()` только если были изменения, а не при каждом рестарте бота.
6.  **Добавление очистки `_compression_locks`**: Написать механизм удаления локов для `user_id` после завершения компрессии, чтобы устранить утечку памяти.
7.  **Внедрение `asyncio.Lock` на сессию пользователя**: Запретить параллельную обработку нескольких сообщений от одного пользователя в `aiogram` хендлерах, чтобы избежать состояний гонки (race conditions).
8.  **Полный отказ от Legacy-хранилища**: Завершить миграцию логов рефлексий, списков задач (todo) и дневников из текстовых файлов в SQLite и безопасно удалить `storage/legacy.py`.

---

## 8. Реализованные Фичи (Status: Production Ready)

### Prompt-Based Policy Engine (Dynamic Tone V6)
**Статус:** [DONE] Implemented, Tested, Production Ready

Amargon now adapts communication style and dialogue strategy based on user emotional baseline while preserving a stable core personality.

**Known Constraints (Архитектурные решения V6):**
- `CORE_STATES` are fixed and control routing.
- `signals` are informational only (no routing impact).
- Strategy overrides Tone.
- Memory is excluded from cache key by design (to prevent 0% cache hit rate).
- Unknown states always fallback to `neutral`.

**Changelog (2026-07):**
- CORE / STRATEGY / TONE separation
- deterministic prompt compilation
- sha256 policy cache
- CORE_STATES validation
- signals isolation
- invalid-state fallback
- integration test coverage

---

## 9. Следующий фокус разработки

### Context-Aware Proactive Loop (Умные пинги)
**Статус:** [BETA] Live Validation

Реализован масштабируемый и детерминированный пайплайн проактивности, состоящий из:
1. **Engagement Gate** (`engagement.py`): защита от спама и выгорания (cooldown 12ч, счетчик игноров, min silence).
2. **Reason Selector** (`reasons.py`): строгий выбор причины пинга на основе приоритетов (Unfinished Goal, Emotional Checkin, и т.д.).
3. **Context Collector** (`collector.py`): извлечение данных из БД только под выбранную причину.
4. **Policy Engine + Formatter** (`formatter.py`): жесткий генератор текста с антигаллюцинаторным промптом, динамической длиной сообщения (по urgency) и наложенным Tone/Strategy.
5. **Telemetry / Orchestrator** (`loop.py`, `telemetry.py`): запись метрик в таблицу `proactive_events` для отслеживания Reply Rate и качества пингов.

**Текущий шаг (Live Validation):**
Система развернута и проходит тестирование в реальном времени (5-7 дней). 
Ключевые метрики для проверки:
- Частота срабатывания Engagement Gate (gate passed vs blocked).
- Распределение выбираемых причин.
- **Reply Rate** (отклик пользователя).
- Отсутствие "кринжа" и ложных воспоминаний (строгий мониторинг LLM-галлюцинаций).
