"""Tests for Phase 6: LLM Intelligence — all sub-phases."""
from __future__ import annotations

import pytest
from typing import Any

# 6.1 Prompt Compiler
from companion.llm.prompt_compiler.compiler import PromptSection, PromptCompiler, CompiledPrompt
from companion.llm.prompt_compiler.sections import (
    IdentitySection, PersonaSection, ConversationStateSection,
    MemorySection, ReasoningSection, StyleSection, SafetySection,
    create_default_sections,
)
# 6.2 Context Budget
from companion.llm.prompt_compiler.budget import ContextBudgetManager, SIMPLE_BUDGET, COMPLEX_BUDGET, EMOTIONAL_BUDGET
# 6.3 Profilers
from companion.llm.token_profiler import TokenProfiler, TokenProfile
from companion.llm.latency_profiler import LatencyProfiler, LatencyProfile
# 6.5 Tool Registry
from companion.tools.registry import ToolRegistry, Tool, create_default_registry


# ── 6.1 Prompt Compiler ─────────────────────────────────────────────

def test_prompt_section_build():
    sec = IdentitySection()
    content = sec.build({})
    assert "Amargon" in content
    assert sec.priority == 1


def test_prompt_compiler_assembly():
    compiler = PromptCompiler(total_budget=10000)
    for sec in create_default_sections():
        compiler.register(sec)

    ctx = {
        "memory_context": "Иван любит музыку.",
        "strategy": "развивай мысль",
        "tone": "постирония",
    }
    result = compiler.compile(context=ctx)
    assert isinstance(result, CompiledPrompt)
    assert "Identity" in result.sections_included
    assert "Memory" in result.sections_included
    assert "Style" in result.sections_included
    assert result.total_tokens > 0
    assert "Amargon" in result.text


def test_prompt_compiler_cache():
    compiler = PromptCompiler(total_budget=10000)
    compiler.register(IdentitySection())

    result1 = compiler.compile()
    result2 = compiler.compile()
    assert result1.cache_key == result2.cache_key


def test_persona_section():
    from companion.persona import DynamicPersona
    persona = DynamicPersona(humor=0.9, empathy=0.3, directness=0.8, energy=0.5)
    sec = PersonaSection()
    content = sec.build({"persona": persona})
    assert "0.90" in content  # humor
    assert "0.30" in content  # empathy


def test_empty_sections_excluded():
    compiler = PromptCompiler(total_budget=10000)
    compiler.register(IdentitySection())
    compiler.register(MemorySection())  # No memory_context in ctx

    result = compiler.compile(context={})
    assert "Identity" in result.sections_included
    assert "Memory" not in result.sections_included


# ── 6.2 Context Budget ──────────────────────────────────────────────

def test_budget_simple_scenario():
    budget = ContextBudgetManager.get_budget_override("general", "neutral", "simple")
    assert budget["Memory"] == 800
    assert budget["Persona"] == 500


def test_budget_complex_scenario():
    budget = ContextBudgetManager.get_budget_override("goal_tracking", "neutral", "complex")
    assert budget["Memory"] == 3000
    assert budget["Reasoning"] == 1000


def test_budget_emotional_scenario():
    budget = ContextBudgetManager.get_budget_override("general", "depressed", "simple")
    assert budget["Safety"] == 1200
    assert budget["Style"] == 800


# ── 6.3 Profilers ───────────────────────────────────────────────────

def test_token_profiler():
    profiler = TokenProfiler()
    breakdown = {"Identity": 300, "Memory": 1200, "Reasoning": 530}
    tp = profiler.profile_prompt(breakdown, output_text="Ответ на 200 символов примерно")
    assert tp.total_input == 2030
    assert tp.output_tokens > 0
    assert "Identity" in tp.report()
    assert profiler.last_profile is tp


def test_latency_profiler():
    profiler = LatencyProfiler()
    lp = profiler.profile_turn({
        "Retrieval": 18.0,
        "Reasoning": 33.0,
        "Fusion": 9.0,
        "LLM": 1800.0,
        "Learning": 12.0,
    })
    assert lp.total_ms == 1872.0
    assert "LLM" in lp.report()
    assert profiler.last_profile is lp


# ── 6.5 Tool Registry ───────────────────────────────────────────────

def test_tool_registry_register_and_list():
    registry = create_default_registry()
    tools = registry.list_tools()
    names = [t["name"] for t in tools]
    assert "filesystem.read" in names
    assert "calendar.get" in names
    assert "notes.save" in names
    assert "search.web" in names
    assert "python.run" in names
    assert len(tools) == 5


def test_tool_execute_calendar():
    registry = create_default_registry()
    result = registry.execute("calendar.get")
    assert "date" in result
    assert "time" in result


def test_tool_execute_notes():
    registry = create_default_registry()
    result = registry.execute("notes.save", {"text": "Тестовая заметка"})
    assert result["status"] == "saved"


def test_tool_execute_unknown():
    registry = create_default_registry()
    result = registry.execute("nonexistent.tool")
    assert "error" in result


def test_tool_python_run():
    registry = create_default_registry()
    result = registry.execute("python.run", {"expression": "2 + 2"})
    assert result["result"] == "4"
