"""Vector index — embedding caching and cosine similarity search via Gemini API."""
from __future__ import annotations

import logging
import math
import sqlite3
import struct
from contextlib import contextmanager
from collections.abc import Generator
from typing import Any

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "text-embedding-004"
_EMBEDDING_DIM = 768  # text-embedding-004 output dimension


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Gemini API. Falls back to zero vectors on failure."""
    try:
        from google import genai
        from companion.config import GOOGLE_API_KEY
        if not GOOGLE_API_KEY or "test" in GOOGLE_API_KEY.lower():
            return [[0.0] * _EMBEDDING_DIM for _ in texts]
        client = genai.Client(api_key=GOOGLE_API_KEY)
        result = client.models.embed_content(
            model=_EMBEDDING_MODEL,
            contents=texts,
        )
        return [e.values for e in result.embeddings]
    except Exception as exc:
        logger.warning("Embedding API call failed: %s. Using zero vectors.", exc)
        return [[0.0] * _EMBEDDING_DIM for _ in texts]


def _float_list_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_float_list(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """Manages embedding storage in SQLite and provides vector search."""

    def __init__(self, path: str | None = None) -> None:
        from companion.config import SQLITE_PATH as _SQLITE_PATH
        self.path = path if path is not None else _SQLITE_PATH
        self._init_table()

    def _init_table(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    content_hash TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    content_type TEXT DEFAULT 'fact',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(content_type)")
            except sqlite3.OperationalError:
                pass
            try:
                cursor = conn.execute("PRAGMA table_info(embeddings)")
                cols = [row[1] for row in cursor.fetchall()]
                if "embedding" not in cols:
                    conn.execute("ALTER TABLE embeddings ADD COLUMN embedding BLOB NOT NULL DEFAULT (x'')")
            except sqlite3.OperationalError:
                pass

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _content_hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def upsert_embedding(self, text: str, embedding: list[float], content_type: str = "fact") -> None:
        h = self._content_hash(text)
        blob = _float_list_to_blob(embedding)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO embeddings (content_hash, content, embedding, content_type)
                   VALUES (?, ?, ?, ?)""",
                (h, text, blob, content_type),
            )

    def get_embedding(self, text: str) -> list[float] | None:
        h = self._content_hash(text)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE content_hash=?", (h,)
            ).fetchone()
        if row:
            return _blob_to_float_list(row[0])
        return None

    def compute_and_cache(self, text: str, content_type: str = "fact") -> list[float]:
        existing = self.get_embedding(text)
        if existing:
            return existing
        vec = _embed_texts([text])[0]
        self.upsert_embedding(text, vec, content_type)
        return vec

    def compute_and_cache_batch(self, texts: list[str], content_type: str = "fact") -> None:
        missing: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            if not t.strip():
                continue
            if self.get_embedding(t) is None:
                missing.append((i, t))
        if not missing:
            return
        batch_texts = [t for _, t in missing]
        try:
            vectors = _embed_texts(batch_texts)
        except Exception as exc:
            logger.error("Batch embedding failed: %s", exc)
            return
        for (_, text), vec in zip(missing, vectors):
            self.upsert_embedding(text, vec, content_type)

    def search(self, query: str, top_k: int = 10, content_type: str | None = None) -> list[dict[str, Any]]:
        qvec = self.compute_and_cache(query, content_type="query")
        with self._conn() as conn:
            if content_type:
                rows = conn.execute(
                    "SELECT content, embedding FROM embeddings WHERE content_type=?",
                    (content_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT content, embedding FROM embeddings WHERE content_type != 'query'"
                ).fetchall()

        scored: list[tuple[float, str]] = []
        for content, blob in rows:
            vec = _blob_to_float_list(blob)
            score = cosine_similarity(qvec, vec)
            scored.append((score, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"content": text, "score": round(score, 4)}
            for score, text in scored[:top_k]
            if score > 0.3
        ]

    def count(self, content_type: str | None = None) -> int:
        with self._conn() as conn:
            if content_type:
                return conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE content_type=?", (content_type,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def delete_for_content(self, text: str) -> None:
        h = self._content_hash(text)
        with self._conn() as conn:
            conn.execute("DELETE FROM embeddings WHERE content_hash=?", (h,))
