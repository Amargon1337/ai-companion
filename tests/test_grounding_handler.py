"""Tests for grounding_handler — should_retry_with_grounding only (async functions need EventLoop)."""
from __future__ import annotations

from companion.grounding_handler import should_retry_with_grounding


class TestShouldRetryWithGrounding:
    def test_factual_question_triggers_retry(self):
        """WH-questions (когда, где, кто...) should trigger grounding retry."""
        critique = {"warnings": [], "flags": [], "confidence": 0.5}
        assert should_retry_with_grounding("когда был запущен Python?", critique) is True
        assert should_retry_with_grounding("где находится Москва?", critique) is True
        assert should_retry_with_grounding("кто открыл Америку?", critique) is True
        assert should_retry_with_grounding("сколько человек на Земле?", critique) is True

    def test_non_factual_no_retry(self):
        """Non-factual questions should not trigger retry."""
        critique = {"warnings": [], "flags": [], "confidence": 0.5}
        assert should_retry_with_grounding("как дела?", critique) is False
        assert should_retry_with_grounding("расскажи о себе", critique) is False
        assert should_retry_with_grounding("что ты думаешь?", critique) is False

    def test_warning_about_source_triggers_retry(self):
        """Warning containing 'источник' should trigger retry regardless of query."""
        critique = {"warnings": ["Нет источника"], "flags": [], "confidence": 0.5}
        assert should_retry_with_grounding("как дела?", critique) is True

    def test_high_confidence_no_retry(self):
        """Should not retry when critique confidence is high."""
        critique = {"warnings": [], "flags": [], "confidence": 0.9}
        assert should_retry_with_grounding("когда?", critique) is True

    def test_empty_query_no_retry(self):
        critique = {"warnings": [], "flags": [], "confidence": 0.5}
        assert should_retry_with_grounding("", critique) is False
