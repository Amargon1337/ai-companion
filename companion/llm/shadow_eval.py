"""Shadow evaluation (вторая модель проверяет первую)."""
import logging
from companion.llm.client import aio_oneshot, parse_json_object
from companion.config import MODEL_NAME

logger = logging.getLogger(__name__)

async def evaluate_identity_change(category: str, old_value: str, proposed_value: str) -> bool:
    """Shadow evaluates if a proposed identity change is valid or a hallucination/drift."""
    
    if not old_value:
        return True # No drift if it's completely new

    prompt = f"""You are a Shadow Evaluator AI protecting the user's core identity from hallucinated or overly dramatic drifts.

Category: {category}
Current Identity State: {old_value}
Proposed Change: {proposed_value}

Evaluate if the proposed change is a natural evolution/addition, or if it contradicts the current state without strong evidence (personality drift).
CRITICAL RULE: The user actively encourages and permits shifts toward depression, cynicism, nihilism, and darker reflections. Do NOT block changes just because they are "passive whining", "victim mentality", or "cynical". Accept these as valid natural evolution.
Reply with exactly valid JSON: {{"is_valid": true}} or {{"is_valid": false, "reason": "why"}}.
"""
    try:
        raw = await aio_oneshot(prompt, MODEL_NAME)
        res = parse_json_object(raw)
        is_valid = res.get("is_valid", True)
        if not is_valid:
            logger.warning(f"ShadowEvaluator rejected identity drift for {category}. Reason: {res.get('reason')}")
        return is_valid
    except Exception as e:
        # A failed second opinion must never authorize an irreversible identity
        # mutation. The candidate remains available to the caller's review path.
        logger.error(f"ShadowEvaluator failed: {e}")
        return False
