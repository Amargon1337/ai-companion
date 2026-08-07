"""Phase 3: Reasoning Engine — Modular 10-stage decision engine before answer generation.

Implements all 10 Reasoning Engine mechanisms:
1. Reasoning Planner (Intent & execution plan routing)
2. Reasoning Modules (Temporal, Relationship, Goal, Emotion, Prediction reasoners)
3. Evidence Builder (Claim -> Evidence -> Confidence structuring)
4. Hypothesis Engine (Probabilistic hypothesis generation for sparse data)
5. Uncertainty Engine (High/Medium/Low confidence & non-assertive mode)
6. Clarification Planner (Targeted missing data -> single clarification question)
7. Multi-step Reasoning (Bounded 4-5 step reasoning chains)
8. Reflection Buffer (Post-response quality check & write bridge)
9. Tool Planner (External tool routing: web_search, calendar, files, weather, music)
10. Final Answer Composer (XML prompt block & orchestrator)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =====================================================================
# 1. REASONING PLANNER & EXECUTION PLAN
# =====================================================================

@dataclass
class ExecutionPlan:
    """Specifies which reasoning modules and external sources are required."""
    intent: str = "general"
    need_memory: bool = True
    need_graph: bool = False
    need_internet: bool = False
    need_clarification: bool = False
    need_prediction: bool = False
    modules_to_run: list[str] = field(default_factory=list)


class ReasoningPlanner:
    """Analyzes incoming query to create an execution plan for reasoning."""

    @classmethod
    def plan(cls, query: str) -> ExecutionPlan:
        lowered = query.lower().strip()

        # Small talk check
        if len(lowered) < 3 or lowered in ("привет", "ок", "спасибо", "да", "нет", "хорошо"):
            return ExecutionPlan(
                intent="small_talk",
                need_memory=False,
                need_graph=False,
                need_internet=False,
                need_clarification=False,
                need_prediction=False,
                modules_to_run=[],
            )

        # 1. Detect Intent & Modules
        modules: list[str] = []
        intent = "general"

        # Check relationship / entity
        is_entity = bool(
            re.search(r"\b[A-ZА-Я][a-zа-я]{2,}\b", query)
            or any(kw in lowered for kw in ("кто", "как там", "где", "отношения", "женя", "морзик"))
        )
        if is_entity:
            intent = "relationship"
            modules.append("relationship")

        # Check goals
        is_goal = any(kw in lowered for kw in ("цел", "план", "задач", "достиг", "прогресс", "goal"))
        if is_goal:
            intent = "goal_tracking"
            modules.append("goal")

        # Check prediction / future
        is_prediction = any(
            kw in lowered for kw in ("прогноз", "будущ", "ожидан", "предсказ", "будет ли", "что дальше")
        )
        if is_prediction:
            intent = "prediction"
            modules.append("prediction")

        # Check temporal / past events
        is_temporal = any(
            kw in lowered for kw in ("когда", "вчера", "раньше", "было", "случилось", "история")
        )
        if is_temporal:
            modules.append("temporal")

        # Check emotion / feeling
        is_emotion = any(
            kw in lowered for kw in ("настроение", "чувству", "груст", "рад", "зол", "обид", "пережива")
        )
        if is_emotion:
            modules.append("emotion")

        if not modules:
            modules.append("temporal")

        # 2. Check external tool need
        need_internet = any(
            kw in lowered
            for kw in ("новости", "погода", "курс валют", "в мире", "что сейчас в интернете", "сегодняшние новости")
        )

        # 3. Check clarification need (unresolved pronouns without context)
        need_clarification = bool(
            re.search(r"^(он|она|они|это)\s+", lowered) and len(lowered.split()) <= 4
        )

        return ExecutionPlan(
            intent=intent,
            need_memory=True,
            need_graph=is_entity,
            need_internet=need_internet,
            need_clarification=need_clarification,
            need_prediction=is_prediction,
            modules_to_run=modules,
        )


# =====================================================================
# 2. REASONING MODULES
# =====================================================================

class TemporalReasoner:
    """Reasons about temporal sequences, elapsed time, and event ordering."""
    @classmethod
    def reason(cls, query: str, context: Any) -> dict[str, Any]:
        episodes = getattr(context, "episodes", []) or []
        timeline = getattr(context, "timeline", []) or []
        summary = "No temporal events found."
        if timeline or episodes:
            events = timeline or episodes
            latest = events[-1]
            summary = f"Latest recorded event: {latest.get('date', '')} — {latest.get('event', latest.get('title', ''))}"
        return {"module": "temporal", "summary": summary, "event_count": len(episodes) + len(timeline)}


class RelationshipReasoner:
    """Reasons about entity relationships, sentiment, and interpersonal context."""
    @classmethod
    def reason(cls, query: str, context: Any) -> dict[str, Any]:
        entities = getattr(context, "entities", []) or []
        if not entities:
            return {"module": "relationship", "summary": "No entities identified in context.", "entity_count": 0}
        names = [e.name for e in entities if hasattr(e, "name")]
        return {
            "module": "relationship",
            "summary": f"Active entities in relationship scope: {', '.join(names)}",
            "entity_count": len(entities),
        }


class GoalReasoner:
    """Reasons about active goals, obstacles, and progress actions."""
    @classmethod
    def reason(cls, query: str, context: Any) -> dict[str, Any]:
        facts = getattr(context, "facts", []) or []
        goal_facts = [
            f.fact for f in facts
            if hasattr(f, "fact") and any(kw in f.fact.lower() for kw in ("цель", "план", "задач"))
        ]
        summary = f"Identified {len(goal_facts)} goal-related memory facts." if goal_facts else "No goal blockers detected."
        return {"module": "goal", "summary": summary, "goal_facts": len(goal_facts)}


class EmotionReasoner:
    """Reasons about user emotional state and empathetic alignment."""
    @classmethod
    def reason(cls, query: str, context: Any) -> dict[str, Any]:
        lowered = query.lower()
        if any(w in lowered for w in ("устал", "плохо", "груст", "срыв", "тяжело")):
            state = "stressed / needing support"
        elif any(w in lowered for w in ("отлично", "сделал", "ура", "хорошо", "рад")):
            state = "positive / celebrating"
        else:
            state = "neutral / focused"
        return {"module": "emotion", "summary": f"Detected emotional state: {state}", "sentiment": state}


class PredictionReasoner:
    """Reasons about future trajectories and causal expectations."""
    @classmethod
    def reason(cls, query: str, context: Any) -> dict[str, Any]:
        beliefs = getattr(context, "beliefs", []) or []
        summary = f"Based on {len(beliefs)} active beliefs, forecasting standard continuation."
        return {"module": "prediction", "summary": summary, "belief_count": len(beliefs)}


class ReasoningModuleRegistry:
    """Dispatches reasoning calls to specialized reasoners."""
    _modules = {
        "temporal": TemporalReasoner,
        "relationship": RelationshipReasoner,
        "goal": GoalReasoner,
        "emotion": EmotionReasoner,
        "prediction": PredictionReasoner,
    }

    @classmethod
    def execute_modules(cls, plan: ExecutionPlan, query: str, context: Any) -> list[dict[str, Any]]:
        results = []
        for mod_name in plan.modules_to_run:
            mod = cls._modules.get(mod_name)
            if mod:
                results.append(mod.reason(query, context))
        return results


# =====================================================================
# 3. EVIDENCE BUILDER
# =====================================================================

@dataclass
class EvidenceItem:
    """Formal piece of evidence backing an assertion."""
    claim: str
    evidence_type: str  # fact, entity, episode
    evidence_id: str
    confidence: float  # 0.0 .. 1.0


class EvidenceBuilder:
    """Extracts and structures formal evidence from MemoryContext."""

    @classmethod
    def build_evidence(cls, query: str, context: Any) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        q_words = set(re.findall(r"\w{3,}", query.lower()))
        if not q_words:
            return items

        # 1. Facts evidence
        facts = getattr(context, "facts", []) or []
        for f in facts:
            if hasattr(f, "fact") and hasattr(f, "id"):
                f_words = set(re.findall(r"\w{3,}", f.fact.lower()))
                if len(q_words & f_words) >= 1:
                    conf = float(getattr(f, "confidence", 0.8))
                    items.append(
                        EvidenceItem(
                            claim=f.fact,
                            evidence_type="fact",
                            evidence_id=f.id,
                            confidence=min(1.0, conf),
                        )
                    )

        # 2. Entity evidence
        entities = getattr(context, "entities", []) or []
        for e in entities:
            if hasattr(e, "name") and hasattr(e, "id"):
                e_name = e.name.lower()
                if any(w in e_name for w in q_words) or any(e_name in w for w in q_words):
                    items.append(
                        EvidenceItem(
                            claim=f"Entity {e.name} ({getattr(e, 'type', 'concept')}) is active in graph.",
                            evidence_type="entity",
                            evidence_id=e.id,
                            confidence=float(getattr(e, "importance", 0.8)),
                        )
                    )

        # 3. Episodes evidence
        episodes = getattr(context, "episodes", []) or []
        for idx, ep in enumerate(episodes):
            text = str(ep.get("event") or ep.get("title", ""))
            ep_words = set(re.findall(r"\w{3,}", text.lower()))
            if len(q_words & ep_words) >= 1:
                items.append(
                    EvidenceItem(
                        claim=f"Episode on {ep.get('date', '')}: {text}",
                        evidence_type="episode",
                        evidence_id=str(ep.get("id", f"ep_{idx}")),
                        confidence=0.85,
                    )
                )

        return items


# =====================================================================
# 4. HYPOTHESIS ENGINE
# =====================================================================

@dataclass
class Hypothesis:
    """A probabilistic hypothesis when direct evidence is sparse."""
    claim: str
    probability: float  # 0.0 .. 1.0
    rationale: str


class HypothesisEngine:
    """Generates probabilistic hypotheses when information is insufficient."""

    @classmethod
    def generate_hypotheses(
        cls, query: str, evidence: list[EvidenceItem]
    ) -> dict[str, Any]:
        # If we have strong evidence (>= 2 items with avg confidence >= 0.7), no need for hypothesis speculation
        if len(evidence) >= 2:
            avg_conf = sum(e.confidence for e in evidence) / len(evidence)
            if avg_conf >= 0.7:
                return {
                    "hypotheses": [],
                    "recommendation": "answer_directly",
                }

        # Otherwise generate hypotheses
        hypotheses = [
            Hypothesis(
                claim="User is referring to recent ongoing project or relationship context.",
                probability=0.60,
                rationale="Default cognitive assumption for sparse queries.",
            ),
            Hypothesis(
                claim="User may be asking about a new unmentioned topic.",
                probability=0.30,
                rationale="No direct keyword match found in memory.",
            ),
            Hypothesis(
                claim="User may need assistance with immediate blocking issue.",
                probability=0.10,
                rationale="Potential implicit goal blocker.",
            ),
        ]

        rec = "ask_user" if len(evidence) == 0 else "answer_cautiously"

        return {
            "hypotheses": [
                {"claim": h.claim, "probability": h.probability, "rationale": h.rationale}
                for h in hypotheses
            ],
            "recommendation": rec,
        }


# =====================================================================
# 5. UNCERTAINTY ENGINE
# =====================================================================

@dataclass
class UncertaintyEvaluation:
    """Evaluation of overall epistemic confidence."""
    level: str  # "High", "Medium", "Low"
    confidence_score: float  # 0.0 .. 1.0
    assertive: bool  # False if Low confidence (prevents dogmatic statements)
    reason: str


class UncertaintyEngine:
    """Computes overall epistemic confidence and sets assertive mode."""

    @classmethod
    def evaluate(
        cls, evidence: list[EvidenceItem], hypotheses_data: dict[str, Any]
    ) -> UncertaintyEvaluation:
        if not evidence:
            return UncertaintyEvaluation(
                level="Low",
                confidence_score=0.3,
                assertive=False,
                reason="No direct evidence retrieved from memory.",
            )

        avg_conf = sum(e.confidence for e in evidence) / len(evidence)
        # Apply bonus for multiple supporting evidence items
        score = min(1.0, avg_conf + (min(3, len(evidence)) * 0.05))

        if score >= 0.80:
            return UncertaintyEvaluation(
                level="High",
                confidence_score=score,
                assertive=True,
                reason="Strong supporting evidence present.",
            )
        elif score >= 0.50:
            return UncertaintyEvaluation(
                level="Medium",
                confidence_score=score,
                assertive=True,
                reason="Moderate evidence present; some details may be inferred.",
            )
        else:
            return UncertaintyEvaluation(
                level="Low",
                confidence_score=score,
                assertive=False,
                reason="Evidence confidence is below threshold; non-assertive mode enabled.",
            )


# =====================================================================
# 6. CLARIFICATION PLANNER
# =====================================================================

@dataclass
class ClarificationQuestion:
    """A targeted single question to resolve missing data."""
    missing_data: str
    question: str


class ClarificationPlanner:
    """Formulates a targeted single clarification question when uncertainty is Low."""

    @classmethod
    def plan_clarification(
        cls,
        query: str,
        plan: ExecutionPlan,
        evidence: list[EvidenceItem],
        uncertainty: UncertaintyEvaluation,
    ) -> ClarificationQuestion | None:
        if not plan.need_clarification and uncertainty.level != "Low":
            return None

        lowered = query.lower()

        # Pronoun ambiguity
        if re.search(r"^(он|она|они|это)\s+", lowered):
            return ClarificationQuestion(
                missing_data="Unresolved pronoun target",
                question="Уточните, пожалуйста, о ком или о чём именно идёт речь?",
            )

        # Entity ambiguity
        if plan.intent == "relationship" and not any(e.evidence_type == "entity" for e in evidence):
            return ClarificationQuestion(
                missing_data="Unknown entity reference",
                question="О ком именно вы спрашиваете? Напомните, пожалуйста, контекст.",
            )

        # Goal ambiguity
        if plan.intent == "goal_tracking" and not any(e.evidence_type == "fact" for e in evidence):
            return ClarificationQuestion(
                missing_data="Unknown goal target",
                question="Уточните, пожалуйста, какую именно цель или задачу вы имеете в виду?",
            )

        if uncertainty.level == "Low":
            return ClarificationQuestion(
                missing_data="Sparse evidence context",
                question="У меня пока недостаточно данных об этом. Расскажите чуть подробнее?",
            )

        return None


# =====================================================================
# 7. MULTI-STEP REASONING
# =====================================================================

@dataclass
class ReasoningStep:
    """A discrete step in the reasoning chain."""
    step_index: int
    action: str
    result: str


class MultiStepReasoning:
    """Executes a bounded multi-step reasoning chain (max 4-5 steps)."""

    @classmethod
    def execute_chain(
        cls,
        query: str,
        plan: ExecutionPlan,
        context: Any,
        evidence: list[EvidenceItem],
        uncertainty: UncertaintyEvaluation,
        max_steps: int = 5,
    ) -> list[ReasoningStep]:
        steps: list[ReasoningStep] = []

        # Step 1: Intent & Scope
        steps.append(
            ReasoningStep(
                step_index=1,
                action="Intent & Scope Analysis",
                result=f"Intent={plan.intent} | Modules={','.join(plan.modules_to_run) or 'none'}",
            )
        )

        # Step 2: Evidence Check
        ev_summary = f"Found {len(evidence)} evidence items" if evidence else "No direct evidence found"
        steps.append(
            ReasoningStep(
                step_index=2,
                action="Evidence Synthesis",
                result=ev_summary,
            )
        )

        # Step 3: Uncertainty & Assertion Mode
        steps.append(
            ReasoningStep(
                step_index=3,
                action="Uncertainty Evaluation",
                result=f"Confidence={uncertainty.level} ({uncertainty.confidence_score:.2f}) | Assertive={uncertainty.assertive}",
            )
        )

        # Step 4: Module Conclusions (if any)
        if plan.modules_to_run and len(steps) < max_steps:
            steps.append(
                ReasoningStep(
                    step_index=4,
                    action="Domain Reasoning Execution",
                    result=f"Executed {len(plan.modules_to_run)} specialized domain reasoners.",
                )
            )

        # Step 5: Final Strategy Check
        if len(steps) < max_steps:
            strategy = "Direct confident response" if uncertainty.assertive else "Cautious probabilistic response or clarification"
            steps.append(
                ReasoningStep(
                    step_index=len(steps) + 1,
                    action="Answer Strategy Selection",
                    result=strategy,
                )
            )

        return steps[:max_steps]


# =====================================================================
# 8. REFLECTION BUFFER
# =====================================================================

@dataclass
class ReflectionEvaluation:
    """Post-response quality evaluation and write action recommendation."""
    is_good: bool
    is_useful: bool
    needs_correction: bool
    write_action: str  # save_to_memory | skip | correct_memory
    reason: str


class ReflectionBuffer:
    """Evaluates generated responses before committing memory updates."""

    @classmethod
    def evaluate_response(
        cls, query: str, response: str, confidence_level: str
    ) -> ReflectionEvaluation:
        # If response was extremely short or fallback, don't write to memory
        if len(response.strip()) < 5:
            return ReflectionEvaluation(
                is_good=False,
                is_useful=False,
                needs_correction=False,
                write_action="skip",
                reason="Response too brief.",
            )

        # If confidence was Low, we avoid creating permanent declarative facts unless user confirms
        if confidence_level == "Low":
            return ReflectionEvaluation(
                is_good=True,
                is_useful=True,
                needs_correction=False,
                write_action="skip",
                reason="Low confidence response; skip automatic declarative memory write.",
            )

        return ReflectionEvaluation(
            is_good=True,
            is_useful=True,
            needs_correction=False,
            write_action="save_to_memory",
            reason="Response high quality and grounded.",
        )


# =====================================================================
# 9. TOOL PLANNER
# =====================================================================

@dataclass
class ToolCallPlan:
    """Plan for an external tool invocation."""
    tool_name: str  # web_search, calendar, files, weather, music
    args: dict[str, Any]
    reason: str


class ToolPlanner:
    """Determines whether external tools should be called based on ExecutionPlan."""

    @classmethod
    def plan_tools(cls, query: str, plan: ExecutionPlan) -> list[ToolCallPlan]:
        tools: list[ToolCallPlan] = []
        lowered = query.lower()

        if plan.need_internet:
            tools.append(
                ToolCallPlan(
                    tool_name="web_search",
                    args={"query": query},
                    reason="Query requires external real-time internet data.",
                )
            )

        if any(w in lowered for w in ("календарь", "расписание", "встреча", "завтра в")):
            tools.append(
                ToolCallPlan(
                    tool_name="calendar",
                    args={"query": query},
                    reason="Query mentions schedule or calendar events.",
                )
            )

        return tools


# =====================================================================
# 10. FINAL ANSWER COMPOSER & REASONING ENGINE SERVICE
# =====================================================================

class FinalAnswerComposer:
    """Composes structured XML reasoning prompt block for LLM execution."""

    @classmethod
    def compose_prompt_block(
        cls,
        plan: ExecutionPlan,
        module_conclusions: list[dict[str, Any]],
        evidence: list[EvidenceItem],
        hypotheses: dict[str, Any],
        uncertainty: UncertaintyEvaluation,
        clarification: ClarificationQuestion | None,
        steps: list[ReasoningStep],
        tools: list[ToolCallPlan],
    ) -> str:
        parts = ["<reasoning_engine_context>"]

        # Execution Plan tag
        parts.append(
            f'  <execution_plan intent="{plan.intent}" assertive="{str(uncertainty.assertive).lower()}">\n'
            f"    Modules: {', '.join(plan.modules_to_run) or 'none'}\n"
            f"  </execution_plan>"
        )

        # Reasoning Steps tag
        if steps:
            step_strs = [f"    {s.step_index}. [{s.action}] {s.result}" for s in steps]
            parts.append("  <reasoning_steps>\n" + "\n".join(step_strs) + "\n  </reasoning_steps>")

        # Evidence tag
        if evidence:
            ev_strs = [
                f'    - [{e.evidence_type.upper()}] (conf={e.confidence:.2f}) {e.claim}'
                for e in evidence
            ]
            parts.append("  <evidence_summary>\n" + "\n".join(ev_strs) + "\n  </evidence_summary>")

        # Uncertainty tag
        parts.append(
            f'  <uncertainty_guidance level="{uncertainty.level}" score="{uncertainty.confidence_score:.2f}">\n'
            f"    {uncertainty.reason}\n"
            f"    Assertive statements permitted: {str(uncertainty.assertive).lower()}\n"
            f"  </uncertainty_guidance>"
        )

        # Clarification tag
        if clarification:
            parts.append(
                f'  <clarification_question missing="{clarification.missing_data}">\n'
                f"    Recommended question: {clarification.question}\n"
                f"  </clarification_question>"
            )

        # Tools tag
        if tools:
            tool_strs = [f'    - [{t.tool_name}]: {t.reason}' for t in tools]
            parts.append("  <tools_plan>\n" + "\n".join(tool_strs) + "\n  </tools_plan>")

        parts.append("</reasoning_engine_context>")
        return "\n".join(parts)


class ReasoningEngineService:
    """Orchestrates all 10 stages of the Phase 3 Reasoning Engine."""

    def __init__(self, db: Any = None, store: Any = None) -> None:
        self.db = db
        self.store = store

    def reason(self, query: str, context: Any = None) -> dict[str, Any]:
        """Executes full reasoning pipeline before answering."""
        # 1. Plan
        plan = ReasoningPlanner.plan(query)

        # 2. Modules
        conclusions = ReasoningModuleRegistry.execute_modules(plan, query, context)

        # 3. Evidence
        evidence = EvidenceBuilder.build_evidence(query, context)

        # 4. Hypotheses
        hypotheses = HypothesisEngine.generate_hypotheses(query, evidence)

        # 5. Uncertainty
        uncertainty = UncertaintyEngine.evaluate(evidence, hypotheses)

        # 6. Clarification
        clarification = ClarificationPlanner.plan_clarification(
            query, plan, evidence, uncertainty
        )

        # 7. Multi-step Reasoning chain
        steps = MultiStepReasoning.execute_chain(
            query, plan, context, evidence, uncertainty, max_steps=5
        )

        # 9. Tool Planner
        tools = ToolPlanner.plan_tools(query, plan)

        # 10. Compose Prompt Block
        prompt_block = FinalAnswerComposer.compose_prompt_block(
            plan=plan,
            module_conclusions=conclusions,
            evidence=evidence,
            hypotheses=hypotheses,
            uncertainty=uncertainty,
            clarification=clarification,
            steps=steps,
            tools=tools,
        )

        return {
            "plan": plan,
            "conclusions": conclusions,
            "evidence": evidence,
            "hypotheses": hypotheses,
            "uncertainty": uncertainty,
            "clarification": clarification,
            "steps": steps,
            "tools": tools,
            "prompt_block": prompt_block,
        }

    def post_answer_reflection(
        self, query: str, response: str, confidence_level: str
    ) -> ReflectionEvaluation:
        """Executes Reflection Buffer after an answer is generated."""
        return ReflectionBuffer.evaluate_response(query, response, confidence_level)
