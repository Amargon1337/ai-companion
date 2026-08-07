"""Policy Layer — выбор поведения, а не текста.

Вместо:
  LLM → генерирует текст

Нужно:
  State → Policy → Action

Пример:
  input: user depressed message
  policy:
    - do NOT explain
    - do NOT theorize
    - do:
        → reduce cognitive load
        → ask one concrete question
        → anchor attention to action

Это начало агентности — система выбирает КАК отвечать, не только ЧТО.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class UserState(Enum):
    """Состояние пользователя."""
    ANXIOUS = "anxious"
    DEPRESSED = "depressed"
    CURIOUS = "curious"
    OVERWHELMED = "overwhelmed"
    NEUTRAL = "neutral"

    @classmethod
    def from_analyzer_state(cls, state_str: str) -> "UserState":
        """Convert analyzer state string to UserState enum."""
        mapping = {
            "ANXIOUS": cls.ANXIOUS,
            "DEPRESSED": cls.DEPRESSED,
            "CURIOUS": cls.CURIOUS,
            "OVERWHELMED": cls.OVERWHELMED,
            "NORMAL": cls.NEUTRAL,
        }
        return mapping.get(state_str.upper(), cls.NEUTRAL)


class ResponseMode(Enum):
    """Режим ответа."""
    EXPLAIN = "explain"
    ANCHOR = "anchor"
    EMPATHY = "empathy"


@dataclass
class PolicyConstraints:
    """Ограничения на поведение."""

    # Что НЕ делать
    avoid_explanation: bool = False
    avoid_theorizing: bool = False
    avoid_questions: bool = False
    avoid_long_text: bool = False

    # Что делать
    reduce_cognitive_load: bool = False
    anchor_to_action: bool = False
    validate_feelings: bool = False
    provide_structure: bool = False

    # Лимиты
    max_questions: int = 1

    # Тональность
    tone: str = "neutral"  # empathic, analytical, casual, supportive


@dataclass
class PolicyDecision:
    """Решение о поведении."""

    response_mode: ResponseMode
    constraints: PolicyConstraints
    reasoning: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_mode": self.response_mode.value,
            "constraints": {
                "avoid": [k for k, v in vars(self.constraints).items() if k.startswith("avoid_") and v],
                "do": [k for k, v in vars(self.constraints).items() if not k.startswith("avoid_") and not k.startswith("max_") and not k == "tone" and v],
                "max_questions": self.constraints.max_questions,
                "tone": self.constraints.tone,
            },
            "reasoning": self.reasoning,
            "confidence": self.confidence,
        }


class PolicyLayer:
    """Слой выбора поведения."""

    def __init__(self):
        # Правила: state → policy
        self.rules: dict[UserState, list[PolicyDecision]] = self._load_default_rules()

    def _load_default_rules(self) -> dict[UserState, list[PolicyDecision]]:
        """Загрузить базовые правила."""
        return {
            UserState.DEPRESSED: [
                PolicyDecision(
                    response_mode=ResponseMode.EMPATHY,
                    constraints=PolicyConstraints(
                        avoid_explanation=True,
                        avoid_theorizing=True,
                        avoid_questions=True,
                        validate_feelings=True,
                        reduce_cognitive_load=False,
                        max_questions=0,
                        tone="empathic",
                    ),
                    reasoning="[ZERO-ADVICE PROTOCOL] В депрессии или при спаде сил важна валидация без советов, списков действий и вопросов (max_questions = 0).",
                    confidence=0.95,
                )
            ],

            UserState.ANXIOUS: [
                PolicyDecision(
                    response_mode=ResponseMode.ANCHOR,
                    constraints=PolicyConstraints(
                        avoid_theorizing=True,
                        reduce_cognitive_load=False,
                        anchor_to_action=True,
                        provide_structure=True,
                        max_questions=1,
                        tone="supportive",
                    ),
                    reasoning="При тревоге помогает структура и конкретные действия",
                    confidence=0.85,
                )
            ],

            UserState.OVERWHELMED: [
                PolicyDecision(
                    response_mode=ResponseMode.EMPATHY,
                    constraints=PolicyConstraints(
                        avoid_questions=False,
                        reduce_cognitive_load=False,
                        provide_structure=True,
                        max_questions=3,
                        tone="supportive",
                    ),
                    reasoning="При перегрузке — даем развернутую поддержку и структуру",
                    confidence=0.88,
                )
            ],

            UserState.CURIOUS: [
                PolicyDecision(
                    response_mode=ResponseMode.EXPLAIN,
                    constraints=PolicyConstraints(
                        max_questions=2,
                        tone="analytical",
                    ),
                    reasoning="При любопытстве можно давать подробные объяснения",
                    confidence=0.80,
                )
            ],

            UserState.NEUTRAL: [
                PolicyDecision(
                    response_mode=ResponseMode.EXPLAIN,
                    constraints=PolicyConstraints(
                        max_questions=3,
                        tone="neutral",
                    ),
                    reasoning="Нейтральное состояние — развернутый, глубокий ответ",
                    confidence=0.70,
                )
            ],
        }

    def decide_policy(
        self,
        user_state: UserState,
        message_context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        base_policies = self.rules.get(user_state, self.rules[UserState.NEUTRAL])
        policy = base_policies[0]
        self._log_decision(user_state, policy, message_context)
        return policy

    def _log_decision(
        self,
        user_state: UserState,
        policy: PolicyDecision,
        context: dict[str, Any] | None,
    ):
        """Залогировать решение policy."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_state": user_state.value,
            "policy": policy.to_dict(),
            "context": context or {},
        }

        audit_logger = logging.getLogger("audit.policy")
        audit_logger.info(json.dumps(log_entry, ensure_ascii=False))

    def enforce_constraints(
        self,
        response_text: str,
        constraints: PolicyConstraints,
    ) -> str:
        """
        Проверить и исправить ответ согласно constraints.

        Это post-processing для проверки что LLM следовал правилам.
        """
        import re

        lines = response_text.split("\n")
        parsed_lines = []
        in_code_block = False

        for line in lines:
            stripped = line.strip()
            # Любая строка, начинающаяся с тройных бэктиков, переключает состояние блока кода
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                parsed_lines.append({"type": "code", "content": line})
            elif in_code_block:
                parsed_lines.append({"type": "code", "content": line})
            else:
                # Разбить строку на предложения и разделители
                parts = re.split(r'((?<!\b\d)(?<!\b[a-zA-Z])(?<=[.!?])\s+)', line)
                line_tokens = []
                for part in parts:
                    if not part:
                        continue
                    is_sep = bool(re.match(r'^\s+$', part))
                    is_question = not is_sep and "?" in part
                    if is_question and any(x in part.lower() for x in ["http://", "https://", "www."]):
                        is_question = False
                    line_tokens.append({
                        "text": part,
                        "is_sentence": not is_sep,
                        "is_question": is_question
                    })
                parsed_lines.append({"type": "normal", "tokens": line_tokens})

        # Считаем общее число вопросов
        total_questions = 0
        for pl in parsed_lines:
            if pl["type"] == "normal":
                for token in pl["tokens"]:
                    if token["is_question"]:
                        total_questions += 1

        # Ограничение по количеству вопросов
        if total_questions > constraints.max_questions:
            to_remove = total_questions - constraints.max_questions
            removed = 0
            for pl in parsed_lines:
                if pl["type"] == "normal":
                    tokens = pl["tokens"]
                    i = 0
                    while i < len(tokens):
                        token = tokens[i]
                        if token["is_question"]:
                            if removed < to_remove:
                                token["text"] = ""
                                token["is_sentence"] = False
                                token["is_question"] = False
                                removed += 1
                                # Также удаляем один из соседних пробельных разделителей
                                if i + 1 < len(tokens) and not tokens[i+1]["is_sentence"]:
                                    tokens[i+1]["text"] = ""
                                elif i - 1 >= 0 and not tokens[i-1]["is_sentence"]:
                                    tokens[i-1]["text"] = ""
                        i += 1
            logger.warning(f"Removed {removed} questions to meet constraint")

        # Добавляем точку в конец последнего предложения, если необходимо
        # Ищем последнюю нормальную непустую строку, содержащую предложения
        for pl in reversed(parsed_lines):
            if pl["type"] == "normal":
                tokens = pl["tokens"]
                last_sent_idx = -1
                for idx in reversed(range(len(tokens))):
                    if tokens[idx]["is_sentence"] and tokens[idx]["text"]:
                        last_sent_idx = idx
                        break
                if last_sent_idx != -1:
                    token_text = tokens[last_sent_idx]["text"].rstrip()
                    if token_text and not token_text.endswith((".", "?", "!")):
                        tokens[last_sent_idx]["text"] = token_text + "."
                    break

        # Собираем обратно
        rebuilt_lines = []
        for pl in parsed_lines:
            if pl["type"] == "code":
                rebuilt_lines.append(pl["content"])
            else:
                line_text = "".join(t["text"] for t in pl["tokens"])
                rebuilt_lines.append(line_text)

        result_text = "\n".join(rebuilt_lines)
        return self.enforce_sensitivity_guards(result_text)

    def enforce_sensitivity_guards(self, text: str, effective_state: str | None = None) -> str:
        """
        Фильтрует проявления «токсичного позитива» и избыточных восклицаний,
        если Иван находится в состоянии спада сил, усталости или депрессии/тревоги.
        """
        if effective_state is None:
            from companion.user_model import user_model
            effective_state, _ = user_model.get_effective_emotional_state()

        if not text or effective_state not in ("depressed", "anxious"):
            return text
        import re

        # 1. Заменяем множественные и избыточные восклицательные знаки на точку
        text = re.sub(r'!{2,}', '.', text)
        text = re.sub(r'([a-zA-Zа-яА-ЯёЁ])!', r'\1.', text)

        # 2. Фильтруем типовые лозунги токсичного позитива
        toxic_patterns = [
            r'(?i)\bне вешай нос\b[.,]?',
            r'(?i)\bвсё будет хорошо\b[.,]?',
            r'(?i)\bвсё будет отлично\b[.,]?',
            r'(?i)\bглавное\s*—?\s*позитивный настрой\b[.,]?',
            r'(?i)\bдавай взбодримся\b[.,]?',
            r'(?i)\bулыбнись\b[.,]?',
            r'(?i)\bдержи хвост пистолетом\b[.,]?',
            r'(?i)\bвыше нос\b[.,]?',
            r'(?i)\bне опускай руки\b[.,]?',
            r'(?i)\bвсё наладится\b[.,]?',
            r'(?i)\bпосмотри на это с позитивной стороны\b[.,]?',
            r'(?i)\bвсё к лучшему\b[.,]?',
        ]
        for pattern in toxic_patterns:
            text = re.sub(pattern, '', text).strip()

        # Очищаем возможные двойные пробелы после удаления фраз
        text = re.sub(r'\s{2,}', ' ', text).strip()
        return text


    def format_prompt_with_policy(
        self,
        base_prompt: str,
        policy: PolicyDecision,
    ) -> str:
        """
        Добавить policy constraints в промпт для LLM.

        Это инструкции для LLM как отвечать.
        """
        constraints_lines = []

        constraints_lines.append(f"Response mode: {policy.response_mode.value}")
        constraints_lines.append("")

        # Что НЕ делать
        avoid_items = []
        if policy.constraints.avoid_explanation:
            avoid_items.append("- НЕ объясняй подробно")
        if policy.constraints.avoid_theorizing:
            avoid_items.append("- НЕ строй теории")
        if policy.constraints.avoid_questions:
            avoid_items.append("- НЕ задавай вопросов")
        if policy.constraints.max_questions == 0:
            avoid_items.append("- [ZERO-ADVICE PROTOCOL] НЕ задавай ни одного вопроса в ответе!")
            avoid_items.append("- [ZERO-ADVICE PROTOCOL] НЕ давай советов, списков шагов или планов по решению проблемы.")
        # avoid_long_text is ignored to enforce maximal answers

        if avoid_items:
            constraints_lines.append("AVOID:")
            constraints_lines.extend(avoid_items)
            constraints_lines.append("")

        # Что делать
        do_items = []
        do_items.append("- Отвечай МАКСИМАЛЬНО ПОДРОБНО И ДЛИННО (минимум 5-10 абзацев)")
        if policy.constraints.reduce_cognitive_load:
            do_items.append("- Снизь когнитивную нагрузку (простые слова, короткие фразы)")
        if policy.constraints.anchor_to_action:
            do_items.append("- Фокус на конкретном действии")
        if policy.constraints.validate_feelings:
            do_items.append("- Валидируй чувства")
        if policy.constraints.provide_structure:
            do_items.append("- Дай структуру (список, шаги)")

        if do_items:
            constraints_lines.append("DO:")
            constraints_lines.extend(do_items)
            constraints_lines.append("")

        # Лимиты
        if policy.constraints.max_questions is not None:
            constraints_lines.append(f"Max questions: {policy.constraints.max_questions}")

        constraints_lines.append(f"Tone: {policy.constraints.tone}")
        constraints_lines.append("")

        # Собрать промпт
        policy_section = "\n".join(constraints_lines)

        return f"""{base_prompt}

═══ RESPONSE POLICY ═══
{policy_section}

Следуй этим правилам строго.
"""


# Global singleton
policy_layer = PolicyLayer()
