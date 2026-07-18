# Architecture Cleanup Decisions

This document details the components evaluated for deletion, archiving, or refactoring during the Ultimate Refactor.

## KEEP
- `companion/bot_core.py` (Core orchestrator, needs refactoring but keeping).
- `companion/llm/client.py`, `companion/llm/pipeline.py`, `companion/llm/sessions.py`.
- `companion/memory/store.py`, `companion/memory/vector_index.py`, `companion/memory/retrieval.py`.
- `companion/models.py`.
- `companion/handlers/chat.py`, `companion/handlers/media.py`.

## REFACTOR
- `companion/bot_core.py`: Drop `memory_service.py` and `user_model.py`. Shift all DB actions to `MemoryStore`.
- `companion/proactive/*`: Strip `UserModel` dependencies, inject `MemoryStore` or `HumanModel` directly.
- `companion/handlers/chat.py`: Remove `services` module dependencies.

## ARCHIVE (Move to `archive/` or delete)
- `companion/services/memory_service.py`: Contains outdated Telegram commands (`/remember`, `/timeline`) that directly manipulate the database. Refactor commands into `handlers/commands.py` if needed, otherwise delete.
- `companion/services/report_service.py`: Outdated telemetry/stats reporting via Telegram.
- `companion/llm/analyzer.py`: Legacy message analyzer (replaced by `pipeline.py`).
- `companion/context.py`: Legacy `CognitiveContext` wrapper. Replaced by `ContextBundle` in `models.py`.
- `companion/memory/text_sim.py`: Legacy exact-text matching deduplication. Replaced by FAISS vector search in `store.py`.
- `companion/documents.py`: Old document processing utilities. Media handling is covered in `media.py`.

## DELETE
- `companion/user_model.py`: Dangerous global singleton `user_model = UserModel()`. Replaced completely by `HumanModel` in `models.py` + `MemoryStore` operations. This is the biggest source of race conditions and legacy bugs.
