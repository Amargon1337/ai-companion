"""Tests for critique_manager — apply_critique_to_text edge cases."""
from __future__ import annotations

from companion.critique_manager import apply_critique_to_text


class TestApplyCritiqueToText:
    def test_high_confidence_no_warnings_returns_unchanged(self):
        result = apply_critique_to_text(
            "simple fact text.",
            {"confidence": 0.85, "warnings": [], "flags": []},
        )
        assert result == "simple fact text."

    def test_high_confidence_with_warnings_adds_prefix_and_suffix(self):
        result = apply_critique_to_text(
            "simple fact.",
            {"confidence": 0.85, "warnings": ["could be more precise"], "flags": []},
        )
        assert "simple fact." in result.lower()
        assert "снижен" in result

    def test_low_confidence_adds_prefix(self):
        result = apply_critique_to_text(
            "Python is the best.",
            {"confidence": 0.4, "warnings": [], "flags": []},
        )
        assert "python is the best." in result.lower()
        assert "python" in result.lower()

    def test_medium_confidence_adds_gentle_prefix(self):
        result = apply_critique_to_text(
            "It will rain tomorrow.",
            {"confidence": 0.65, "warnings": [], "flags": []},
        )
        assert "it will rain tomorrow." in result.lower()

    def test_uncertain_language_flag_prevents_prefix(self):
        result = apply_critique_to_text(
            "maybe it will rain tomorrow.",
            {"confidence": 0.4, "warnings": [], "flags": ["uncertain_language"]},
        )
        assert "maybe it will rain tomorrow" in result
        assert "not entirely sure" not in result.lower()

    def test_uncertain_language_with_warnings(self):
        result = apply_critique_to_text(
            "seems true.",
            {"confidence": 0.4, "warnings": ["check source"], "flags": ["uncertain_language"]},
        )
        assert "seems true." in result
        # uncertain_language flag skips prefix but appends warning suffix
        assert "check source" in result

    def test_empty_text_no_crash(self):
        result = apply_critique_to_text(
            "",
            {"confidence": 0.3, "warnings": ["error"], "flags": []},
        )
        assert isinstance(result, str)

    def test_warnings_in_low_confidence_add_both_prefix_and_warning(self):
        result = apply_critique_to_text(
            "this is a statement.",
            {"confidence": 0.35, "warnings": ["doubtful source"], "flags": []},
        )
        assert "this is a statement." in result.lower()
        assert "снижен" in result
