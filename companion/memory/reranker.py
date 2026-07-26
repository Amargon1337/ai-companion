"""Cross-Encoder Reranker module for context filtering and relevance ranking (Phase 5)."""
from __future__ import annotations

import logging
import os
from typing import Any
from companion.models import Fact

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Reranks retrieved facts using a local CrossEncoder or LLM-as-a-judge fallback."""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-TinyBERT-L-2-v2",
        threshold: float = 0.15,
        model: Any = None,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._model: Any = model
        self._model_load_failed = False

    def _get_local_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._model_load_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
            return self._model
        except Exception as exc:
            logger.debug("Local CrossEncoder unavailable (%s), will use fallback", exc)
            self._model_load_failed = True
            return None

    def _is_protected(self, fact: Fact, explicit_search: bool = False) -> bool:
        if explicit_search:
            return False
        tags_lower = [str(t).lower() for t in getattr(fact, "tags", [])]
        if any(tag in tags_lower for tag in ["pinned", "core_identity", "anchor"]):
            return True
        if getattr(fact, "memory_kind", "") == "permanent":
            return True
        if getattr(fact, "importance", 0) >= 9:
            return True
        return False

    def rerank(
        self,
        query: str,
        facts: list[Fact],
        top_k: int | None = None,
        threshold: float | None = None,
        faiss_scores: dict[str, float] | None = None,
        explicit_search: bool = False,
    ) -> list[Fact]:
        """Rerank facts and filter out candidates below relevance threshold."""
        if not facts:
            return []
        if not query or not query.strip():
            return facts

        thresh = threshold if threshold is not None else self.threshold

        protected: list[Fact] = []
        regular: list[Fact] = []
        for f in facts:
            if self._is_protected(f, explicit_search=explicit_search):
                protected.append(f)
            else:
                regular.append(f)

        if not regular:
            return protected

        scored: list[tuple[Fact, float]] = []
        model = self._get_local_model()
        if model is not None:
            try:
                pairs = [[query.strip(), f.fact] for f in regular]
                scores = model.predict(pairs)
                for f, score in zip(regular, scores):
                    val = float(score)
                    if getattr(f, "retrieval_score", 0.0) >= 1.0:
                        val = max(val, 0.75)
                    if faiss_scores and faiss_scores.get(f.id, 0.0) >= 0.7:
                        val = max(val, faiss_scores.get(f.id, 0.0))
                    scored.append((f, val))
            except Exception as exc:
                logger.debug("CrossEncoder predict failed (%s), switching to LLM judge", exc)
                scored = self._score_with_llm_judge(query, regular, faiss_scores=faiss_scores)
        else:
            scored = self._score_with_llm_judge(query, regular, faiss_scores=faiss_scores)

        filtered = [
            (f, sc) for f, sc in scored
            if sc >= thresh
        ]
        filtered.sort(key=lambda item: item[1], reverse=True)

        selected_regular = [f for f, _ in filtered]
        if top_k is not None and len(selected_regular) > top_k:
            selected_regular = selected_regular[:top_k]

        return protected + selected_regular

    def _score_with_llm_judge(
        self,
        query: str,
        facts: list[Fact],
        faiss_scores: dict[str, float] | None = None,
    ) -> list[tuple[Fact, float]]:
        """Fallback scoring using lightweight LLM-as-a-judge or semantic/keyword overlap."""
        scored: list[tuple[Fact, float]] = []

        in_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        from companion import config

        for f in facts:
            score = self._compute_keyword_overlap(query, f.fact)
            if getattr(f, "retrieval_score", 0.0) >= 1.0:
                score = max(score, 0.75)
            if faiss_scores and faiss_scores.get(f.id, 0.0) >= 0.7:
                score = max(score, faiss_scores.get(f.id, 0.0))

            if not in_test and getattr(config, "ENABLE_LLM_RERANKER", False):
                try:
                    from companion.llm import client as llm
                    prompt = (
                        f"Оцени релевантность факта запросу от 0.0 до 1.0.\n"
                        f"Запрос: {query}\n"
                        f"Факт: {f.fact}\n"
                        f"Ответь ТОЛЬКО числом от 0.0 до 1.0:"
                    )
                    res = llm.oneshot(prompt, temperature=0.1)
                    if res and res.strip():
                        val_str = "".join(ch for ch in res.strip() if ch.isdigit() or ch == ".")
                        if val_str:
                            try:
                                llm_score = float(val_str)
                                if 0.0 <= llm_score <= 1.0:
                                    score = llm_score
                            except ValueError:
                                pass
                except Exception as e:
                    logger.debug("LLM judge oneshot failed for fact %s: %s", getattr(f, "id", ""), e)
            scored.append((f, score))

        return scored

    def _compute_keyword_overlap(self, query: str, fact_text: str) -> float:
        q_clean = query.lower()
        f_clean = fact_text.lower()
        if q_clean in f_clean:
            return 1.0

        def _stems(text: str) -> set[str]:
            res = set()
            for w in text.split():
                w_clean = "".join(ch for ch in w if ch.isalnum())
                if len(w_clean) >= 4:
                    res.add(w_clean[:4])
                elif len(w_clean) >= 3:
                    res.add(w_clean)
            return res

        q_stems = _stems(q_clean)
        f_stems = _stems(f_clean)
        if not q_stems:
            return 0.0
        overlap = len(q_stems & f_stems) / max(min(len(q_stems), len(f_stems)), 1)
        return min(1.0, overlap)
