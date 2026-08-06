# Memory OS Architecture Contract

**Версия:** 3.0 (C1.x Baseline)  
**Статус:** Зафиксировано перед этапом C2 Governance  
**Охват:** Архитектурные инварианты Event Sourcing, Provenance, Replay и Audit.

---

## 1. Фундаментальные инварианты (Memory Invariants)

Память Amargon's Void функционирует на принципах Event Sourcing и строгой трассируемости. Любая дальнейшая разработка (включая политики Governance, конфликт-резолюцию и жизненный цикл) **не имеет права нарушать** следующие инварианты:

### 1.1 Event Invariant (Неразрывность истории событий)
- **Правило:** Любое изменение состояния persistent-памяти (создание факта, обновление статуса, изменение важности, замещение, восстановление) **обязано** сопровождаться записью соответствующего доменного события `MemoryEvent` через `EventStore`.
- **Запрет:** Прямые модификации SQLite-таблицы `facts` (`UPDATE facts`, `DELETE FROM facts`) в обход записи события в `memory_events` **строго запрещены** (Event Store bypass is illegal).
- **Порядок выполнения:** 
  $$\text{Domain Event} \longrightarrow \text{Event Store} \longrightarrow \text{Projection (SQLite facts)} \longrightarrow \text{Commit}$$

### 1.2 Replay Invariant (Воспроизводимость проекции)
- **Правило:** Таблица `facts` является материализованной проекцией (projection) истории событий `memory_events`.
- **Правило:** Проекция должна быть 100% воспроизводима из Event Store посредством `ProjectionRebuilder` (`replay_events`) без потерь и рассинхронизаций.
- **Предохранитель:** При добавлении новых полей в модель `Fact` эти поля обязаны включаться в payload/metadata событий и поддерживаться в слое воспроизведения (`REQUIRED_REPLAY_FIELDS`).

### 1.3 Provenance Invariant (Обязательность происхождения)
- **Правило:** Каждый persistent-факт (`Fact`), хранящийся в системе, обязан обладать полным набором метаданных Phase C1:
  - `origin`: источник создания факта (`MemoryOrigin` — `USER_STATEMENT`, `LLM_EXTRACTION`, `SYSTEM_INFERENCE` и др.).
  - `identity_layer`: уровень идентичности (`IdentityLayer` — `CORE_VALUE`, `PREFERENCE`, `BIOGRAPHY`, `LEGACY_UNKNOWN` и др.).
  - `source_message_id`: идентификатор исходного сообщения в диалоге (при наличии).
  - Декомпозированная уверенность: `conf_observed`, `conf_inferred`, `conf_stability`, `conf_verification` в диапазоне `[0.0, 1.0]`.

### 1.4 Audit Invariant (Контроль целостности)
- **Правило:** Любой релиз или переход к следующей архитектурной фазе требует прохождения автоматизированного контроля целостности и регрессионного тестирования:
  ```bash
  # 1. Диагностическая проверка целостности Memory OS (должна возвращать PASS [OK])
  python -m companion.memory.audit

  # 2. Полный прогон тестов памяти
  pytest tests/memory/ -v
  ```

---

## 2. Архитектурные границы для C2 Governance

Этап **C2 Governance** расширяет Memory OS правилами управления, но подлежит ограничениям контракта C1.x:

1. **Кто имеет право менять память:** Любая операция изменения (архивация, карантин, разрешение конфликтов, затухание) должна быть инкапсулирована в авторизованные контроллеры/сервисы (Governance Controller) с фиксацией `actor` в `MemoryEvent`.
2. **Безопасность удалений:** Физическое удаление записей (`DELETE`) не должно использоваться для стандартного жизненного цикла; вместо этого должны применяться переходы статусов (`active -> dormant -> archived -> quarantined`) с соответствующими событиями.
