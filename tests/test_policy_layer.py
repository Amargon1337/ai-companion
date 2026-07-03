"""Tests for policy_layer — enforce_constraints logic and edge cases."""
from __future__ import annotations

import pytest
from companion.policy_layer import PolicyConstraints, policy_layer


class TestPolicyLayerEnforceConstraints:
    def test_preserves_newlines_and_formatting(self):
        text = "Первая строка.\nВторая строка.\n- Пункт 1.\n- Пункт 2."
        constraints = PolicyConstraints(max_questions=1)
        result = policy_layer.enforce_constraints(text, constraints)
        assert result == text

    def test_preserves_code_blocks(self):
        text = "Привет.\n```python\ndef ask():\n    return \"Что? Как?\"\n```\nПонятно."
        # Even if max_questions=0, the questions inside the code block must NOT be removed
        constraints = PolicyConstraints(max_questions=0)
        result = policy_layer.enforce_constraints(text, constraints)
        assert "Что? Как?" in result
        assert "def ask():" in result

    def test_filters_excess_questions_correctly(self):
        text = "Как дела? Что делаешь? Отлично!"
        # max_questions=1 -> should remove the first question "Как дела?" and keep "Что делаешь? Отлично!"
        constraints = PolicyConstraints(max_questions=1)
        result = policy_layer.enforce_constraints(text, constraints)
        assert result == "Что делаешь? Отлично!"

    def test_adds_period_at_the_end_of_last_sentence_if_missing(self):
        text = "Привет\nВторая строка"
        constraints = PolicyConstraints(max_questions=1)
        result = policy_layer.enforce_constraints(text, constraints)
        assert result == "Привет\nВторая строка."

    def test_does_not_add_period_to_code_block_at_the_end(self):
        text = "Привет.\n```python\nprint(1)\n```"
        constraints = PolicyConstraints(max_questions=1)
        result = policy_layer.enforce_constraints(text, constraints)
        assert result == text

    def test_complex_formatting_and_questions(self):
        text = (
            "Привет! Как дела?\n"
            "Вот пример кода:\n"
            "```python\n"
            "# А тут вопрос?\n"
            "print('Hello?')\n"
            "```\n"
            "Ты понял? Пожалуйста, ответь."
        )
        # Total questions outside code block = 2 ("Как дела?", "Ты понял?")
        # max_questions = 1 -> should remove the first question "Как дела?"
        constraints = PolicyConstraints(max_questions=1)
        result = policy_layer.enforce_constraints(text, constraints)
        
        expected = (
            "Привет!\n"
            "Вот пример кода:\n"
            "```python\n"
            "# А тут вопрос?\n"
            "print('Hello?')\n"
            "```\n"
            "Ты понял? Пожалуйста, ответь."
        )
        assert result == expected

    def test_preserves_urls_with_query_params(self):
        text = "Вот ссылка: https://google.com/search?q=python. Будут ли еще вопросы?"
        constraints = PolicyConstraints(max_questions=1)
        result = policy_layer.enforce_constraints(text, constraints)
        assert result == text

