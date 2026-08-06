"""Vector store abstraction layer.

Defines the VectorStore protocol that all vector backends must implement.
Current implementation: FaissVectorStore (in companion.memory.vector_index).
Future backends: Qdrant, Chroma, SQLite-vec.

The protocol is deliberately minimal — it captures the operations that the
Memory OS actually needs, not every feature of every vector DB.
"""
from companion.memory.vector_backend.protocol import (
    VectorSearchResult,
    VectorStore,
)

__all__ = [
    "VectorSearchResult",
    "VectorStore",
]
