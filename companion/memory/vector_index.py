"""Vector index — embedding caching and cosine similarity search via Gemini API."""
from __future__ import annotations

import logging
import math
import sqlite3
import struct
from contextlib import closing, contextmanager
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
        logger.info(f"[VECTOR] Отправка запроса на получение эмбеддингов для {len(texts)} элементов...")
        from google.genai import types
        client = _get_genai_client()

        chunk_size = 90
        all_embeddings = []
        for i in range(0, len(texts), chunk_size):
            chunk = texts[i : i + chunk_size]
            batch_embed = getattr(client.models, "batch_embed_content", None)
            if batch_embed:
                try:
                    result = batch_embed(
                        model=_EMBEDDING_MODEL,
                        contents=chunk,
                        config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM),
                    )
                except TypeError:
                    result = batch_embed(
                        model=_EMBEDDING_MODEL,
                        requests=[
                            {
                                "model": _EMBEDDING_MODEL,
                                "content": text,
                                "config": {"output_dimensionality": _EMBEDDING_DIM},
                            }
                            for text in chunk
                        ],
                    )
            else:
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
        import os
        from companion.config import SQLITE_PATH as _SQLITE_PATH
        self.path = path if path is not None else _SQLITE_PATH
        import threading
        self.lock = threading.RLock()
        self._init_table()
        
        self.index_path = os.path.join(os.path.dirname(self.path) if self.path else ".", "faiss_index.bin")
        self.mapping_path = os.path.join(os.path.dirname(self.path) if self.path else ".", "faiss_mapping.json")
        
        self.id_to_content: dict[int, str] = {}
        self.id_to_hash: dict[int, str] = {}
        self.hash_to_id: dict[str, int] = {}
        self.id_to_type: dict[int, str] = {}
        self._next_id = 0
        
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
        with closing(sqlite3.connect(self.path)) as conn:
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
            conn.commit()

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
        import os
        import json
        import faiss
        with self.lock:
            if os.path.exists(self.index_path) and os.path.exists(self.mapping_path):
                try:
                    self.index = faiss.read_index(self.index_path)
                    with open(self.mapping_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        self.id_to_content = {int(k): v for k, v in data.get("id_to_content", {}).items()}
                        self.id_to_hash = {int(k): v for k, v in data.get("id_to_hash", {}).items()}
                        self.hash_to_id = data.get("hash_to_id", {})
                        self.id_to_type = {int(k): v for k, v in data.get("id_to_type", {}).items()}
                        self._next_id = data.get("next_id", 0)
                    self._is_initialized = True
                    return
                except Exception as e:
                    logger.warning(f"Failed to load FAISS index from disk, rebuilding: {e}")
            
            self._rebuild_index()

    def save_index_to_disk(self):
        import faiss
        import json
        with self.lock:
            if self._is_initialized and hasattr(self, 'index') and self.index is not None:
                faiss.write_index(self.index, self.index_path)
                with open(self.mapping_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        "id_to_content": self.id_to_content,
                        "id_to_hash": self.id_to_hash,
                        "hash_to_id": self.hash_to_id,
                        "id_to_type": self.id_to_type,
                        "next_id": self._next_id
                    }, f, ensure_ascii=False)

    def _rebuild_index(self) -> None:
        import faiss
        import numpy as np
        
        self.id_to_content = {}
        self.id_to_hash = {}
        self.hash_to_id = {}
        self.id_to_type = {}
        self._next_id = 0
        
        base_index = faiss.IndexHNSWFlat(_EMBEDDING_DIM, 32)
        self.index = faiss.IndexIDMap(base_index)
        
        seen_hashes: set[str] = set()

        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT fact AS content, embedding, 'fact' AS content_type
                FROM facts
                WHERE embedding IS NOT NULL AND status IN ('active', 'dormant')
                UNION ALL
                SELECT content, embedding, content_type
                FROM embeddings
                WHERE content_type NOT IN ('query', 'fact')
                """
            )
            
            while True:
                rows = cursor.fetchmany(2000)
                if not rows:
                    break
                
                arr_vecs = []
                ids = []
                for content, blob, content_type in rows:
                    content_hash = self._content_hash(content)
                    if content_hash in seen_hashes:
                        continue
                    seen_hashes.add(content_hash)
                    
                    arr_vecs.append(_blob_to_float_list(blob))
                    current_id = self._next_id
                    self.id_to_content[current_id] = content
                    self.id_to_hash[current_id] = content_hash
                    self.hash_to_id[content_hash] = current_id
                    self.id_to_type[current_id] = content_type
                    ids.append(current_id)
                    self._next_id += 1
                
                if arr_vecs:
                    arr = np.array(arr_vecs, dtype=np.float32)
                    faiss.normalize_L2(arr)
                    ids_arr = np.array(ids, dtype=np.int64)
                    self.index.add_with_ids(arr, ids_arr)
                    
                    # explicit memory free for large batches
                    del arr
                    del ids_arr
                    del arr_vecs
                    del ids
            
        self._is_initialized = True
        self.save_index_to_disk()

    def _content_hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def upsert_embedding(
        self,
        text: str,
        embedding: list[float],
        content_type: str = "fact",
        fact_id: str | None = None,
    ) -> None:
        h = self._content_hash(text)
        blob = _float_list_to_blob(embedding)
        with self.lock:
            with self._conn() as conn:
                if content_type == "fact":
                    if fact_id:
                        conn.execute("UPDATE facts SET embedding=? WHERE id=?", (blob, fact_id))
                    else:
                        conn.execute("UPDATE facts SET embedding=? WHERE fact=?", (blob, text))
                else:
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
                
            if h in self.hash_to_id:
                return
            import faiss
            import numpy as np
            
            current_id = self._next_id
            self.id_to_content[current_id] = text
            self.id_to_hash[current_id] = h
            self.hash_to_id[h] = current_id
            self.id_to_type[current_id] = content_type
            self._next_id += 1
            
            vec = np.array([embedding], dtype=np.float32)
            faiss.normalize_L2(vec)
            ids_arr = np.array([current_id], dtype=np.int64)
            self.index.add_with_ids(vec, ids_arr)
            self.save_index_to_disk()

    def get_embedding(self, text: str) -> list[float] | None:
        h = self._content_hash(text)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM facts WHERE fact=? AND embedding IS NOT NULL LIMIT 1", (text,)
            ).fetchone()
            if row:
                return _blob_to_float_list(row[0])
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE content_hash=?", (h,)
            ).fetchone()
        if row:
            return _blob_to_float_list(row[0])
        return None

    def compute_and_cache(
        self,
        text: str,
        content_type: str = "fact",
        fact_id: str | None = None,
    ) -> list[float] | None:
        if not self.embeddings_enabled:
            return None
        existing = self.get_embedding(text)
        if existing:
            if content_type == "fact" and fact_id:
                self.upsert_embedding(text, existing, content_type, fact_id=fact_id)
            return existing
        try:
            vec = _embed_texts([text])[0]
            self.upsert_embedding(text, vec, content_type, fact_id=fact_id)
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
        existing_by_hash: dict[str, list[float]] = {}
        
        with self._conn() as conn:
            if content_type == "fact":
                fact_rows = conn.execute(
                    f"SELECT fact, embedding FROM facts WHERE embedding IS NOT NULL AND fact IN ({','.join(['?'] * len(valid_texts))})",
                    valid_texts,
                ).fetchall()
                for fact, blob in fact_rows:
                    existing_by_hash[self._content_hash(fact)] = _blob_to_float_list(blob)
            query = f"SELECT content, content_hash, embedding FROM embeddings WHERE content_hash IN ({','.join(['?'] * len(hashes))})"
            rows = conn.execute(query, hashes).fetchall()
            for content, content_hash, blob in rows:
                existing_by_hash.setdefault(content_hash, _blob_to_float_list(blob))
            found_hashes = set(existing_by_hash)
            if content_type == "fact":
                conn.executemany(
                    "UPDATE facts SET embedding=? WHERE fact=? AND embedding IS NULL",
                    [(_float_list_to_blob(vec), hash_to_text[h]) for h, vec in existing_by_hash.items() if h in hash_to_text],
                )
            
        missing_texts = [hash_to_text[h] for h in hashes if h not in found_hashes]
        
        if not missing_texts:
            for text in valid_texts:
                vec = existing_by_hash.get(self._content_hash(text))
                if vec:
                    self.upsert_embedding(text, vec, content_type)
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
                idx = int(idx)
                if idx != -1 and idx in self.id_to_content:
                    if content_type and self.id_to_type.get(idx) != content_type:
                        continue
                    # Convert L2 distance squared of normalized vectors to Cosine Similarity
                    score = 1.0 - float(dist) / 2.0
                    if score > 0.3:
                        results.append({
                            "content": self.id_to_content[idx],
                            "content_hash": self.id_to_hash[idx],
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
                conn.executemany(
                    "UPDATE facts SET embedding=NULL WHERE fact=?",
                    [(t,) for t in texts],
                )
            
            import numpy as np
            ids_to_remove = []
            for h in hashes:
                if h in self.hash_to_id:
                    del_id = self.hash_to_id.pop(h)
                    self.id_to_content.pop(del_id, None)
                    self.id_to_hash.pop(del_id, None)
                    self.id_to_type.pop(del_id, None)
                    ids_to_remove.append(del_id)
            if ids_to_remove:
                self.index.remove_ids(np.array(ids_to_remove, dtype=np.int64))
                self.save_index_to_disk()
