"""Vector index — embedding caching and cosine similarity search via Gemini API."""
from __future__ import annotations

import logging
import math
import sqlite3
import struct
from contextlib import contextmanager
from collections.abc import Generator
from typing import Any

def _configure_conn(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

logger = logging.getLogger(__name__)

from companion.config import EMBEDDING_MODEL as _EMBEDDING_MODEL
from companion.config import EMBEDDING_DIM as _EMBEDDING_DIM


EMBEDDING_FAILURES = 0
ZERO_VECTOR_GENERATIONS = 0
_GENAI_CLIENT = None


def _get_genai_client():
    global _GENAI_CLIENT
    if _GENAI_CLIENT is None:
        from google import genai
        from companion.config import GOOGLE_API_KEY

        if not GOOGLE_API_KEY or "test" in GOOGLE_API_KEY.lower():
            raise ValueError("Invalid or test GOOGLE_API_KEY")
        _GENAI_CLIENT = genai.Client(api_key=GOOGLE_API_KEY)
    return _GENAI_CLIENT

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Gemini API."""
    if not texts:
        return []
    try:
        from google.genai import types
        client = _get_genai_client()

        chunk_size = 90
        all_embeddings = []
        for i in range(0, len(texts), chunk_size):
            chunk = texts[i : i + chunk_size]
            result = client.models.embed_content(
                model=_EMBEDDING_MODEL,
                contents=chunk,
                config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM)
            )
            for embedding in result.embeddings:
                values = list(embedding.values)
                if values and not any(values):
                    global ZERO_VECTOR_GENERATIONS
                    ZERO_VECTOR_GENERATIONS += 1
                    logger.warning("Embedding API returned an all-zero vector")
                all_embeddings.append(values)

        return all_embeddings
    except Exception as exc:
        global EMBEDDING_FAILURES
        EMBEDDING_FAILURES += 1
        logger.warning("Embedding API call failed: %s. Total failures: %d", exc, EMBEDDING_FAILURES)
        raise exc

def get_embedding_stats() -> dict[str, int]:
    return {
        "failures": EMBEDDING_FAILURES,
        "zero_vectors_generated": ZERO_VECTOR_GENERATIONS,
    }


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
        import threading
        self.lock = threading.RLock()
        self._init_table()
        
        # In-memory FAISS structures
        import faiss
        self.index = faiss.IndexHNSWFlat(_EMBEDDING_DIM, 32)
        self.content_list: list[str] = []
        self.hash_list: list[str] = []
        self.content_type_list: list[str] = []
        self._is_initialized = False
        self.embeddings_enabled = True
        
        # Load existing database embeddings into memory index at startup
        self._load_index()

    def test_embeddings(self) -> bool:
        """Perform a test embedding request to validate API config."""
        try:
            vec = _embed_texts(["test validation"])
            if vec and any(v != 0.0 for v in vec[0]):
                return True
            return False
        except Exception as exc:
            logger.error("Embedding validation failed on startup: %s", exc)
            return False

    def _init_table(self) -> None:
        with sqlite3.connect(self.path) as conn:
            _configure_conn(conn)
            
            # Check if table exists
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'"
            ).fetchone()
            
            if table_exists:
                cursor = conn.execute("PRAGMA table_info(embeddings)")
                cols = {row[1] for row in cursor.fetchall()}
                required_cols = {"content_hash", "content", "embedding", "content_type"}
                if not required_cols.issubset(cols):
                    logger.info("Embeddings table schema is outdated. Dropping and recreating table.")
                    conn.execute("DROP TABLE IF EXISTS embeddings")

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

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path)
        _configure_conn(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _load_index(self) -> None:
        import faiss
        import numpy as np
        
        with self.lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT content, embedding, content_type FROM embeddings WHERE content_type != 'query'"
                ).fetchall()
            
            self.content_list = []
            self.hash_list = []
            self.content_type_list = []
            arr_vecs = []
            
            for content, blob, content_type in rows:
                arr_vecs.append(_blob_to_float_list(blob))
                self.content_list.append(content)
                self.hash_list.append(self._content_hash(content))
                self.content_type_list.append(content_type)
                
            self.index = faiss.IndexHNSWFlat(_EMBEDDING_DIM, 32)
            if arr_vecs:
                arr = np.array(arr_vecs, dtype=np.float32)
                faiss.normalize_L2(arr)
                self.index.add(arr)
            self._is_initialized = True

    def _content_hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def upsert_embedding(self, text: str, embedding: list[float], content_type: str = "fact") -> None:
        h = self._content_hash(text)
        blob = _float_list_to_blob(embedding)
        with self.lock:
            with self._conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO embeddings (content_hash, content, embedding, content_type)
                       VALUES (?, ?, ?, ?)""",
                    (h, text, blob, content_type),
                )
                
            if content_type == "query":
                return
                
            if not self._is_initialized:
                self._load_index()
                return
                
            if text in self.content_list:
                return
            import faiss
            import numpy as np
            self.content_list.append(text)
            self.hash_list.append(h)
            self.content_type_list.append(content_type)
            
            vec = np.array([embedding], dtype=np.float32)
            faiss.normalize_L2(vec)
            self.index.add(vec)

    def get_embedding(self, text: str) -> list[float] | None:
        h = self._content_hash(text)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE content_hash=?", (h,)
            ).fetchone()
        if row:
            return _blob_to_float_list(row[0])
        return None

    def compute_and_cache(self, text: str, content_type: str = "fact") -> list[float] | None:
        if not self.embeddings_enabled:
            return None
        existing = self.get_embedding(text)
        if existing:
            return existing
        try:
            vec = _embed_texts([text])[0]
            self.upsert_embedding(text, vec, content_type)
            return vec
        except Exception:
            return None

    def compute_and_cache_batch(self, texts: list[str], content_type: str = "fact") -> None:
        if not self.embeddings_enabled:
            return
            
        valid_texts = [t for t in texts if t.strip()]
        if not valid_texts:
            return
            
        hashes = [self._content_hash(t) for t in valid_texts]
        hash_to_text = dict(zip(hashes, valid_texts))
        
        with self._conn() as conn:
            query = f"SELECT content_hash FROM embeddings WHERE content_hash IN ({','.join(['?'] * len(hashes))})"
            rows = conn.execute(query, hashes).fetchall()
            found_hashes = {row[0] for row in rows}
            
        missing_texts = [hash_to_text[h] for h in hashes if h not in found_hashes]
        
        if not missing_texts:
            return
            
        try:
            vectors = _embed_texts(missing_texts)
        except Exception as exc:
            logger.error("Batch embedding failed: %s", exc)
            return
            
        for text, vec in zip(missing_texts, vectors):
            self.upsert_embedding(text, vec, content_type)

    def search(self, query: str, top_k: int = 10, content_type: str | None = None) -> list[dict[str, Any]]:
        if not self.embeddings_enabled:
            return []
        with self.lock:
            if not self._is_initialized:
                self._load_index()
            
        qvec = self.compute_and_cache(query, content_type="query")
        if qvec is None:
            return []
        
        import faiss
        import numpy as np
        
        q = np.array([qvec], dtype=np.float32)
        faiss.normalize_L2(q)
        
        with self.lock:
            ntotal = self.index.ntotal
            if ntotal == 0:
                return []
            distances, indices = self.index.search(q, min(ntotal, top_k * 5))
            
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx != -1 and idx < len(self.content_list):
                    if content_type and self.content_type_list[idx] != content_type:
                        continue
                    # Convert L2 distance squared of normalized vectors to Cosine Similarity
                    score = 1.0 - float(dist) / 2.0
                    if score > 0.3:
                        results.append({
                            "content": self.content_list[idx],
                            "content_hash": self.hash_list[idx],
                            "score": round(score, 4)
                        })
                        if len(results) >= top_k:
                            break
            return results

    def count(self, content_type: str | None = None) -> int:
        with self._conn() as conn:
            if content_type:
                return conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE content_type=?", (content_type,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def delete_for_content(self, text: str) -> None:
        self.delete_for_content_batch([text])

    def delete_for_content_batch(self, texts: list[str]) -> None:
        """Удалить несколько эмбеддингов и перестроить индекс ОДИН раз.
        Раньше delete_for_content делал _load_index() на каждый вызов →
        apply_importance_decay перестраивал весь HNSW N раз подряд."""
        if not texts:
            return
        hashes = [self._content_hash(t) for t in texts]
        with self.lock:
            with self._conn() as conn:
                conn.executemany(
                    "DELETE FROM embeddings WHERE content_hash=?",
                    [(h,) for h in hashes],
                )
            # Одна перестройка на весь батч.
            self._load_index()
