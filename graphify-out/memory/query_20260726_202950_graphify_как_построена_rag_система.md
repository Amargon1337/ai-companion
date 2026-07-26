---
type: "query"
date: "2026-07-26T20:29:50.707300+00:00"
question: "/graphify как построена rag система"
contributor: "graphify"
outcome: "useful"
source_nodes: ["MemoryStore", "VectorIndex", "RetrievalBudgetManager", "SemanticImportanceRanker"]
---

# Q: /graphify как построена rag система

## Answer

RAG система в этом проекте построенa вокруг единого хранилища памяти (MemoryStore) и модулей индексации, ранжирования и бюджетирования. Основные компоненты: 1. VectorIndex (vector_index.py) - хранение эмбеддингов в SQLite, кеширование и векторный поиск FAISS на базе Gemini API / косинусного сходства. 2. MemoryStore (store.py) - объединение SQLite и VectorIndex для хранения фактов (active/dormant/pinned), сообщений, рефлексий, убеждений и паттернов. 3. RetrievalBudgetManager (retrieval.py) - отбор фактов в рамках токен-бюджета с учетом бустов эмоций и гарантированным включением закрепленных (pinned) фактов. 4. SemanticImportanceRanker (semantic_ranker.py) - многофакторное ранжирование кандидатов (гибрид вектора, свежести, важности и частоты доступа). 5. Importance & Decay (importance.py, text_sim.py) - временное затухание релевантности и n-gram сходство. 6. IdentityVault (identity_vault.py) - гарантированная вставка базового профиля пользователя и идентичности в начало контекста.

## Outcome

- Signal: useful

## Source Nodes

- MemoryStore
- VectorIndex
- RetrievalBudgetManager
- SemanticImportanceRanker