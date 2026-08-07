"""Application Container — single composition root for all dependencies.

Replaces scattered global singletons with an explicit dependency graph.
Every component receives its dependencies through the container, not
through module-level imports.

Architecture:
    Application
      └── AppContainer
            ├── config: AppConfig
            ├── db: MemoryDatabase
            ├── facts: FactRepository
            ├── entities: EntityRepository
            ├── messages: MessageRepository
            ├── vector: VectorIndex
            ├── event_bus: MemoryEventBus
            ├── index_sync: IndexSyncService
            ├── memory_store: MemoryStore (facade over all above)
            ├── retrieval: RetrievalBudgetManager
            ├── governor: MemoryGovernor
            ├── persistence: MemoryPersistenceLayer
            ├── feedback: MemoryFeedbackLoop
            ├── hygiene: MemoryHygieneService
            ├── world_model: WorldModelService
            ├── cognitive: CognitiveLoopService
            ├── reasoning: ReasoningEngineService
            ├── learning: LearningEngineService
            └── identity: IdentityVault

Usage:
    container = create_container()
    container.memory_store.add_fact(fact)
    container.memory_store.search_facts(query)

Backward compatibility:
    The module-level get_container() function provides a default singleton
    so existing code (bot_core.py, handlers) continues to work without
    changes. New code should accept a container parameter explicitly.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """Typed configuration extracted from environment / api.env.

    This is the single source of truth for runtime configuration.
    companion.config module remains the env-parsing layer; AppConfig
    is the typed, injectable representation.
    """
    sqlite_path: str = ""
    data_dir: str = ""
    base_dir: str = ""
    api_token: str = ""
    google_api_key: str = ""
    admin_ids: list[int] = field(default_factory=list)
    model_name: str = "gemini-3.5-flash-lite"
    final_response_model: str = "gemini-3.5-flash-lite"
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 768
    llm_timeout: int = 120
    llm_retries: int = 3
    llm_retry_delay: int = 4
    summary_threshold: int = 50
    faiss_flush_every: int = 25
    faiss_flush_interval: int = 30

    @classmethod
    def from_env(cls) -> AppConfig:
        """Build config from companion.config module (which reads api.env)."""
        from companion.config import (
            BASE_DIR, DATA_DIR, SQLITE_PATH,
            API_TOKEN, GOOGLE_API_KEY, ADMIN_IDS,
            MODEL_NAME, FINAL_RESPONSE_MODEL,
            EMBEDDING_MODEL, EMBEDDING_DIM,
            LLM_TIMEOUT, LLM_RETRIES, LLM_RETRY_DELAY,
            SUMMARY_THRESHOLD, FAISS_FLUSH_EVERY, FAISS_FLUSH_INTERVAL_SECONDS,
        )
        return cls(
            sqlite_path=SQLITE_PATH,
            data_dir=DATA_DIR,
            base_dir=BASE_DIR,
            api_token=API_TOKEN or "",
            google_api_key=GOOGLE_API_KEY or "",
            admin_ids=list(ADMIN_IDS),
            model_name=MODEL_NAME,
            final_response_model=FINAL_RESPONSE_MODEL,
            embedding_model=EMBEDDING_MODEL,
            embedding_dim=EMBEDDING_DIM,
            llm_timeout=LLM_TIMEOUT,
            llm_retries=LLM_RETRIES,
            llm_retry_delay=LLM_RETRY_DELAY,
            summary_threshold=SUMMARY_THRESHOLD,
            faiss_flush_every=FAISS_FLUSH_EVERY,
            faiss_flush_interval=FAISS_FLUSH_INTERVAL_SECONDS,
        )


@dataclass
class AppContainer:
    """Composition root — wires all dependencies together.

    Components are lazily initialized on first access to avoid startup
    cost for components that may not be needed (e.g., hygiene service
    only runs during GC).
    """
    config: AppConfig

    # ── Storage layer ───────────────────────────────────────────────────
    _db: Any = None
    _facts: Any = None
    _entities: Any = None
    _messages: Any = None
    _vector: Any = None

    # ── Event layer ─────────────────────────────────────────────────────
    _event_bus: Any = None
    _index_sync: Any = None

    # ── Memory layer ────────────────────────────────────────────────────
    _memory_store: Any = None
    _retrieval: Any = None
    _governor: Any = None
    _persistence: Any = None
    _feedback: Any = None
    _hygiene: Any = None
    _identity: Any = None

    # ── Cognitive layer ─────────────────────────────────────────────────
    _world_model: Any = None
    _cognitive: Any = None
    _reasoning: Any = None
    _learning: Any = None

    # ── Properties (lazy init) ──────────────────────────────────────────

    @property
    def db(self):
        if self._db is None:
            from companion.storage.sqlite_db import MemoryDatabase
            self._db = MemoryDatabase(self.config.sqlite_path)
        return self._db

    @property
    def facts(self):
        if self._facts is None:
            from companion.storage.repositories.fact_repository import FactRepository
            self._facts = FactRepository(self.db)
        return self._facts

    @property
    def entities(self):
        if self._entities is None:
            from companion.storage.repositories.entity_repository import EntityRepository
            self._entities = EntityRepository(self.db)
        return self._entities

    @property
    def messages(self):
        if self._messages is None:
            from companion.storage.repositories.message_repository import MessageRepository
            self._messages = MessageRepository(self.db)
        return self._messages

    @property
    def vector(self):
        if self._vector is None:
            from companion.memory.vector_index import VectorIndex
            self._vector = VectorIndex(db=self.db)
        return self._vector

    @property
    def event_bus(self):
        if self._event_bus is None:
            from companion.memory.events.bus import MemoryEventBus
            self._event_bus = MemoryEventBus(async_mode=True)
        return self._event_bus

    @property
    def index_sync(self):
        if self._index_sync is None:
            from companion.memory.events.sync import IndexSyncService
            self._index_sync = IndexSyncService(self.event_bus, self.vector, self.db)
        return self._index_sync

    @property
    def governor(self):
        if self._governor is None:
            from companion.memory.governor import MemoryGovernor
            self._governor = MemoryGovernor(self.db)
        return self._governor

    @property
    def persistence(self):
        if self._persistence is None:
            from companion.memory.persistence import MemoryPersistenceLayer
            self._persistence = MemoryPersistenceLayer(self.db, self.governor, event_bus=self.event_bus)
        return self._persistence

    @property
    def identity(self):
        if self._identity is None:
            from companion.memory.identity_vault import IdentityVault
            self._identity = IdentityVault(self.config.sqlite_path, db=self.db)
        return self._identity

    @property
    def memory_store(self):
        """The main MemoryStore facade.

        This is the existing MemoryStore that all current code depends on.
        It is constructed with all the sub-components from this container,
        so it has the same behavior as before — but now its dependencies
        are explicit and injectable.
        """
        if self._memory_store is None:
            from companion.memory.store import MemoryStore
            self._memory_store = MemoryStore(
                db=self.db,
                vector=self.vector,
                event_bus=self.event_bus,
                governor=self.governor,
            )
        return self._memory_store

    @property
    def retrieval(self):
        if self._retrieval is None:
            from companion.memory.retrieval import RetrievalBudgetManager
            self._retrieval = RetrievalBudgetManager(store=self.memory_store)
        return self._retrieval

    @property
    def feedback(self):
        if self._feedback is None:
            from companion.memory.feedback import MemoryFeedbackLoop
            self._feedback = MemoryFeedbackLoop(self.db, self.governor)
        return self._feedback

    @property
    def hygiene(self):
        if self._hygiene is None:
            from companion.memory.hygiene import MemoryHygieneService
            self._hygiene = MemoryHygieneService(
                self.db, self.governor, vector_index=self.vector
            )
        return self._hygiene

    @property
    def world_model(self):
        if self._world_model is None:
            from companion.memory.world_model import WorldModelService
            self._world_model = WorldModelService(self.db, vector=self.vector)
        return self._world_model

    # ── Lifecycle ───────────────────────────────────────────────────────

    def close(self) -> None:
        """Graceful shutdown of all components."""
        try:
            if self._event_bus is not None and hasattr(self._event_bus, "shutdown"):
                self._event_bus.shutdown()
        except Exception:
            pass
        try:
            if self._db is not None:
                self._db.close()
        except Exception:
            pass

    def explain_memory(self, fact_id: str) -> dict[str, Any]:
        """Memory Explainability API — answer 'why do you believe this?'.

        Returns the full provenance chain for a fact: creation date,
        epistemic type, confidence, evidence, mutation history, and
        current status.
        """
        from companion.memory.explainability import explain_memory
        return explain_memory(self.memory_store, fact_id)


# ── Global container singleton (backward compatibility) ─────────────────

_default_container: AppContainer | None = None


def create_container() -> AppContainer:
    """Create a new AppContainer from environment configuration."""
    config = AppConfig.from_env()
    return AppContainer(config=config)


def get_container() -> AppContainer:
    """Get the default global container (creates on first call)."""
    global _default_container
    if _default_container is None:
        _default_container = create_container()
    return _default_container


def reset_container() -> None:
    """Reset the global container (for testing)."""
    global _default_container
    if _default_container is not None:
        _default_container.close()
    _default_container = None
