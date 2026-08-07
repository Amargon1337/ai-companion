"""VectorStore Protocol — abstract interface for vector storage backends.

Any backend that implements this protocol can be used as the vector storage
for Amargon's Void Memory OS. The protocol is intentionally narrow:

  - upsert()     — insert or update a vector by content hash
  - delete()     — remove a vector by content hash
  - search()     — cosine similarity search
  - count()      — total number of vectors
  - flush()      — persist in-memory state to durable storage
  - rebuild()    — reconstruct index from authoritative store (SQLite)

Backends may offer additional capabilities (hybrid search, metadata filters,
namespacing) but these are NOT required by the protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class VectorSearchResult:
    """Single search result from a vector store.

    Attributes:
        content: The original text that was embedded.
        content_hash: SHA-256 hash of the content (dedup key).
        score: Cosine similarity score (0.0 to 1.0, higher = more similar).
        content_type: Category of the content (fact, reflection, pattern, etc.).
        fact_id: Optional link to the source fact in SQLite.
    """
    content: str
    content_hash: str
    score: float
    content_type: str = "fact"
    fact_id: str = ""


@runtime_checkable
class VectorStore(Protocol):
    """Abstract interface for vector storage backends.

    Implementations must be thread-safe for concurrent search() calls.
    Upsert/delete/flush operations may use internal locking.

    Lifecycle:
        1. __init__()      — connect to backend, load existing index
        2. upsert/delete   — mutate the index
        3. search()        — read-only queries
        4. flush()         — persist to durable storage
        5. rebuild()       — reconstruct from SQLite (crash recovery)
    """

    # ── Mutation ────────────────────────────────────────────────────────

    def upsert(
        self,
        content: str,
        vector: list[float],
        *,
        content_type: str = "fact",
        fact_id: str | None = None,
    ) -> None:
        """Insert or update a vector.

        If content_hash already exists, update the vector and metadata.
        Must persist the vector to the backend's durable store.

        Args:
            content: Original text that was embedded.
            vector: Embedding vector (dimensionality must match backend config).
            content_type: Category label for filtering during search.
            fact_id: Optional link to the source fact in SQLite.
        """
        ...

    def delete(self, content_hash: str) -> None:
        """Remove a vector by its content hash.

        If the hash doesn't exist, this is a no-op (not an error).
        """
        ...

    def delete_batch(self, content_hashes: Sequence[str]) -> None:
        """Remove multiple vectors by content hash.

        More efficient than calling delete() in a loop for backends that
        support batch operations.
        """
        ...

    # ── Search ──────────────────────────────────────────────────────────

    def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 10,
        content_type: str | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Find the most similar vectors to a query vector.

        Args:
            query_vector: Query embedding (same dimensionality as stored vectors).
            top_k: Maximum number of results to return.
            content_type: If set, only return results of this type.
            min_score: Minimum cosine similarity threshold (0.0 to 1.0).

        Returns:
            List of VectorSearchResult, sorted by score descending.
        """
        ...

    # ── Lifecycle ───────────────────────────────────────────────────────

    def count(self, content_type: str | None = None) -> int:
        """Return total number of vectors, optionally filtered by type."""
        ...

    def flush(self) -> None:
        """Persist any in-memory state to durable storage.

        Called periodically and on shutdown. Implementations that write
        through on every upsert() can make this a no-op.
        """
        ...

    def rebuild(self, sources: Any) -> None:
        """Reconstruct the entire index from authoritative sources.

        Called on startup when the index is dirty or corrupted.
        The `sources` parameter is backend-specific (for FAISS: a SQLite
        connection; for Qdrant: a data loader function).

        After rebuild, the index must be consistent with the source data.
        """
        ...

    @property
    def is_initialized(self) -> bool:
        """Whether the index has been loaded or rebuilt at least once."""
        ...
