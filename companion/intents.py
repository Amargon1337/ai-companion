"""Intent routing — replaced by LLM-based analyzer in companion.llm.analyzer.

This file is kept for backward compatibility. All intent classification
and command routing is now handled by analyze_message() in llm/analyzer.py
and _route_command() in bot_core.py.
"""
from __future__ import annotations
