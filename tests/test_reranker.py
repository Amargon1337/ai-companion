"""Tests for CrossEncoderReranker (Phase 5)."""
from __future__ import annotations

from companion.memory.reranker import CrossEncoderReranker
from tests.conftest import make_fact


def test_reranker_preserves_protected_facts():
    reranker = CrossEncoderReranker(threshold=0.25)
    f_anchor = make_fact("Пса зовут Морзик", importance=9, tags=["anchor"])
    f_core = make_fact("Иван — разработчик", importance=10, tags=["core_identity"])
    f_irrel = make_fact("Вчера купил кефир", importance=2, tags=[])

    res = reranker.rerank("когда дедлайн проекта", [f_anchor, f_core, f_irrel])
    res_ids = {f.id for f in res}
    assert f_anchor.id in res_ids
    assert f_core.id in res_ids
    assert f_irrel.id not in res_ids


def test_reranker_prunes_irrelevant_facts():
    reranker = CrossEncoderReranker(threshold=0.25)
    f_weather = make_fact("Сегодня была отличная погода", importance=3, tags=[])
    f_rel = make_fact("Иван изучает асинхронные архитектуры в Python", importance=7, tags=[])

    res = reranker.rerank("что изучает Иван в Python", [f_weather, f_rel])
    res_ids = {f.id for f in res}
    assert f_rel.id in res_ids
    assert f_weather.id not in res_ids


def test_reranker_respects_graphrag_score():
    reranker = CrossEncoderReranker(threshold=0.25)
    f_graph = make_fact("Связанный через граф факт", importance=5, tags=[])
    f_graph.retrieval_score = 3.0
    f_irrel = make_fact("Случайная мысль про кефир", importance=2, tags=[])

    res = reranker.rerank("некоторый запрос", [f_graph, f_irrel])
    res_ids = {f.id for f in res}
    assert f_graph.id in res_ids
    assert f_irrel.id not in res_ids


def test_reranker_with_explicit_search():
    reranker = CrossEncoderReranker(threshold=0.15)
    f_core = make_fact("Иван — тестировщик", importance=10, tags=["core_identity"])
    f_weather = make_fact("Сегодня была хорошая погода", importance=3, tags=[])

    res = reranker.rerank(
        "посмотри в интернете последние новости о погоде",
        [f_core, f_weather],
        explicit_search=True,
    )
    res_ids = {f.id for f in res}
    assert f_weather.id in res_ids
    assert f_core.id not in res_ids
