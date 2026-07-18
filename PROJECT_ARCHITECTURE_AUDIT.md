# Архитектурный аудит Amargon's Void

## 1. Общая карта архитектуры

**Точка запуска:**
- Файл `bot.py` является публичной точкой входа. Он вызывает инициализацию в `companion/main.py`. Здесь происходит старт пулинга aiogram, инициализация векторных баз (FAISS), подключение к SQLite и старт планировщика `background_scheduler.py`.

**Основной поток сообщения пользователя:**
1. **Telegram -> Handlers:** Приходит текстовое сообщение в `companion/handlers/chat.py` (или голосовое/фото в `media.py`).
2. **Ядро:** Вызывается `bot_core.py` (функция `handle_message_core`). Сообщение сохраняется в `jsonl.py` и `sqlite_db.py`.
3. **Память и RAG:** `memory/retrieval.py` обращается к `store.py` и `vector_index.py` для векторного поиска фактов, рефлексий и паттернов (бюджет токенов управляется динамически).
4. **Компрессия и LCE:** Если накопилось много новых сообщений, срабатывает `llm/pipeline.py`.
5. **Policy Layer:** В `policy_layer.py` собирается огромный System Prompt, состоящий из инструкций личности (`identity_vault.py`), `CommPref`, модели пользователя `HumanModel` и найденных RAG фактов.
6. **LLM:** Запрос передается в `llm/sessions.py` (через `client.py` к Google Gemini API). Опционально используется движок `reasoning.py` для CoT.
7. **Ответ:** Gemini выдает результат, бот отправляет его в Telegram.

**Фоновые процессы (Proactive & Background):**
- Управляются `background_scheduler.py`. 
- `proactive/loop.py` регулярно проверяет, нужно ли написать пользователю самому (используя `collector.py`, `reasons.py`, `engagement.py`).
- `proactive/subconscious.py` — "Сон" бота. Ночью анализирует диалоги за день и генерирует новые паттерны (Patterns).

**Система памяти (Core System):**
- **Фасад:** `memory/store.py` (`MemoryStore`) — единая точка доступа.
- **Модели:** `models.py` (Fact, Reflection, Pattern, HumanModel, CommPref, LifeTransition).
- **Векторная база:** `memory/vector_index.py` (FAISS).
- **Реляционная/Метадата база:** `storage/sqlite_db.py`.

**LCE (Life Continuity Engine):**
- Модуль, отвечающий за понимание того, как человек *изменился* (Transitions). Находится в `models.py` и управляется через `pipeline.py`.

**Тестовая инфраструктура:**
- Директория `tests/` использует `pytest` (с `pytest-asyncio`). Тестирует логику RAG, политику промптов, компрессию, дедупликацию, асинхронные вызовы и векторную базу.

---

## 2. Карта файлов

### Корень проекта
- **`bot.py`**
  - **Назначение:** Точка входа для запуска бота в production.
  - **Используется:** Внешним вызовом (python bot.py).
  - **Критичность:** CORE
  - **Риск удаления:** Высокий (проект не запустится).
- **`companion/main.py`**
  - **Назначение:** Полная инициализация подсистем, логирования и диспетчера.
  - **Используется:** `bot.py`.
  - **Критичность:** CORE.

### `companion/`
- **`bot_core.py`**
  - **Назначение:** Связующее звено (Orchestrator) между Telegram и RAG/LLM.
  - **Используется:** `chat.py`, `media.py`, `main.py`.
  - **Критичность:** CORE.
- **`config.py`**
  - **Назначение:** Управление настройками, лимитами токенов и API ключами (pydantic BaseSettings).
  - **Используется:** Почти всеми модулями.
  - **Критичность:** CORE.
- **`context.py`**
  - **Назначение:** Содержит `CognitiveContext` — старый механизм упаковки контекста.
  - **Используется:** `bot_core.py`, `sessions.py`.
  - **Критичность:** IMPORTANT.
  - **Причина:** Формирует часть контекста, хотя дублируется с `ContextBundle`.
  - **Риск удаления:** Высокий (без рефакторинга импортов).
- **`models.py`**
  - **Назначение:** Все дата-классы и Pydantic-схемы (Fact, Pattern, HumanModel).
  - **Используется:** Ядром, памятью и LLM-клиентом.
  - **Критичность:** CORE.
- **`runtime_state.py`**
  - **Назначение:** Модель состояния бота в памяти (state machine).
  - **Критичность:** IMPORTANT.
- **`background_scheduler.py`**
  - **Назначение:** Asyncio задачи для proactive-инициативы и ночной консолидации.
  - **Критичность:** IMPORTANT.
- **`documents.py`**
  - **Назначение:** Утилиты работы с документами (RAG).
  - **Используется:** `media.py`.
  - **Критичность:** OPTIONAL.
- **`reasoning.py`**
  - **Назначение:** Модуль для пошагового логического вывода (Chain-of-Thought).
  - **Используется:** `policy_layer.py`, `sessions.py`.
  - **Критичность:** IMPORTANT.
- **`policy_layer.py`**
  - **Назначение:** Policy Engine для динамической сборки System Instruction.
  - **Используется:** `sessions.py`.
  - **Критичность:** CORE.
- **`user_model.py`**
  - **Назначение:** Старая версия модели пользователя.
  - **Используется:** `bot_core.py`, `chat.py`, `loop.py` и др.
  - **Критичность:** POSSIBLE_DEAD.
  - **Причина:** Заменена на `HumanModel` в `models.py`, но старый класс все еще импортируется.
- **`self_model.py`**
  - **Назначение:** Внутреннее состояние бота (его "эмоции", "энергия").
  - **Критичность:** IMPORTANT.
- **`critique_manager.py`**
  - **Назначение:** Механизм самопроверки ответов (Shadow Evaluation).
  - **Критичность:** OPTIONAL.

### `companion/handlers/`
- **`chat.py`** (CORE) - Обрабатывает текст.
- **`media.py`** (IMPORTANT) - Голосовые/фото.

### `companion/llm/`
- **`client.py`** (CORE) - Запросы к Gemini API.
- **`pipeline.py`** (CORE) - Консолидация новых фактов, дедупликация, слияние.
- **`sessions.py`** (CORE) - Управление сессией чата Gemini.
- **`prompts.py`** (CORE) - Шаблоны инструкций.
- **`analyzer.py`** (POSSIBLE_DEAD) - Legacy анализатор сообщений.
- **`master_summary.py`** (IMPORTANT) - Суммаризация долгих чатов.
- **`shadow_eval.py`** (OPTIONAL) - Фоновый контроль качества.
- **`telemetry.py`** (OPTIONAL) - Трекинг затрат токенов.

### `companion/memory/`
- **`store.py`** (CORE) - Менеджер БД, фасад памяти.
- **`vector_index.py`** (CORE) - FAISS индекс (HNSWFlat).
- **`retrieval.py`** (CORE) - `RetrievalBudgetManager`.
- **`identity_vault.py`** (IMPORTANT) - Защита core-промпта личности.
- **`importance.py`** (IMPORTANT) - Логика "старения" памяти.
- **`semantic_ranker.py`** (OPTIONAL) - Дополнительный re-ranking RAG выдачи.
- **`text_sim.py`** (POSSIBLE_DEAD) - Старая текстовая сверка. Заменена на FAISS.

### `companion/proactive/`
Все файлы (`collector.py`, `engagement.py`, `formatter.py`, `loop.py`, `prospective.py`, `reasons.py`, `subconscious.py`, `telemetry.py`) оцениваются как **IMPORTANT**, так как обеспечивают автономность бота.

### `companion/security/`
- **`sanitizer.py`** (CORE) - Защита от prompt injection и экранирование markdown.

### `companion/services/`
- **`memory_service.py`** (POSSIBLE_DEAD) - Команды показа фактов.
- **`report_service.py`** (POSSIBLE_DEAD) - Команды показа статистики.

### `companion/storage/`
- **`sqlite_db.py`** (CORE)
- **`jsonl.py`** (IMPORTANT)

---

## 3. Кандидаты на удаление

Ниже приведен список файлов, которые с высокой вероятностью являются "мертвым кодом" или legacy.

**1. Файл:** `companion/user_model.py`
- **Почему кажется мусором:** Логика перешла в `HumanModel` (`models.py`) и `CommPref`.
- **Последняя роль:** Глобальный словарь состояния.
- **Есть ли импорты:** Да (`bot_core.py`, `chat.py`, `loop.py` и др.).
- **Есть ли обращения:** Да.
- **Что сломается при удалении:** Приложение не запустится из-за `ImportError`.
- **Рекомендация:** удалить после рефакторинга (перенос на `MemoryStore`).

**2. Файл:** `companion/llm/analyzer.py`
- **Почему кажется мусором:** Новая архитектура извлекает инсайты напрямую через `pipeline.py`.
- **Последняя роль:** Инлайн анализ каждого сообщения.
- **Есть ли импорты:** Да (`bot_core.py`, `policy_layer.py`).
- **Есть ли обращения:** Да.
- **Что сломается при удалении:** Сломаются импорты.
- **Рекомендация:** перенести в archive/.

**3. Файл:** `companion/memory/text_sim.py`
- **Почему кажется мусором:** FAISS `vector_index.py` работает точнее и быстрее.
- **Последняя роль:** Дедупликация через Jaccard/Cosine similarity по тексту.
- **Есть ли импорты:** Да (`pipeline.py`, `reasoning.py`).
- **Есть ли обращения:** Да.
- **Что сломается при удалении:** Некоторые проверки дедупликации.
- **Рекомендация:** удалить после рефакторинга.

**4. Файлы:** `companion/services/memory_service.py` и `report_service.py`
- **Почему кажется мусором:** Это слой утилит для старых команд `/remember` и отображения статистики, который смешивает Telegram API с логикой БД.
- **Последняя роль:** Обработка сервисных команд бота.
- **Есть ли импорты:** Да (`bot_core.py`, `chat.py`).
- **Есть ли обращения:** Да.
- **Что сломается при удалении:** Перестанут работать дебаг-команды в Telegram.
- **Рекомендация:** перенести в archive/ (или в `handlers/commands.py`).

---

## 4. Дублирование

1. **Схемы памяти (User Models):** Одновременно существуют: `HumanModel` в `models.py` и `user_model.py`. 
2. **Дедупликация:** Векторная (FAISS), текстовая (`text_sim.py`) и LLM-based (`pipeline.py`).
3. **Контекст (Context):** Существуют `CognitiveContext` в `context.py` и `ContextBundle` в `models.py`. 

---

## 5. Технический долг

- **Опасные места:** `bot_core.py` является "God Object". В нём смешаны роутинг, вызовы базы данных, инициализация RAG, управление кэшем.
- **Хрупкие зависимости:** `user_model.data` импортируется как глобальный singleton, который мутируется из разных корутин (race condition).
- **Потенциальные ошибки агентов:** Обилие legacy-файлов (типа `context.py` и `user_model.py`). Следующий ИИ-агент может случайно обновить старый файл, думая, что он используется для RAG.

---

## 6. Рекомендованная структура проекта

Идеальная структура (Domain-Driven Design), чтобы упростить поддержку:

```text
AmargonsVoid/
├── core/                   # Запуск, конфигурация, state
│   ├── config.py
│   ├── runtime.py
│   └── main.py
├── telegram/               # bot.py и хендлеры (chat.py, media.py)
├── llm/                    # Работа с Gemini (client, pipeline, prompts)
├── rag/                    # Вся логика памяти (самое важное)
│   ├── storage/            # store.py, sqlite_db.py, vector_index.py
│   └── models/             # schemas
├── proactive/              # Фоновые процессы
└── scripts/                # Утилиты
```

---

## 7. Финальное резюме

| Файл | Действие |
|---|---|
| `companion/user_model.py` | удалить после проверки |
| `companion/context.py` | переписать позже |
| `companion/llm/analyzer.py` | архивировать |
| `companion/memory/text_sim.py` | удалить после проверки |
| `companion/services/*.py` | архивировать |
| `bot.py` | оставить |
| `companion/bot_core.py` | переписать позже |
| Файлы в `companion/models.py`, `store.py`, `vector_index.py`, `client.py` | оставить |
