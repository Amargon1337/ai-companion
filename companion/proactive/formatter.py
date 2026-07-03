import json
from companion.proactive.collector import ContextPayload
from companion.llm.client import aio_oneshot
from companion.config import MODEL_NAME

PROACTIVE_PROMPT_TEMPLATE = """# TASK
Generate exactly one proactive message.

# REASON
{reason}

# REASON FACTS
{facts}

# URGENCY
{urgency}

# RULES
- Use only provided facts.
- Do not invent memories.
- Do not invent events.
- Do not ask more than one question.
- Keep message under {max_words} words.

# FACT INTEGRITY
You may only reference facts listed in REASON FACTS.
If facts are insufficient, write a generic message.

# POLICY
{strategy}

# TONE
{tone}

# OUTPUT
Return ONLY the message text. No prefixes, no quotes, no metadata.
"""

def assemble_prompt(payload: ContextPayload, strategy: str, tone: str) -> str:
    # Determine max words based on urgency
    if payload.urgency >= 80:
        max_words = 80
    elif payload.urgency >= 50:
        max_words = 50
    else:
        max_words = 25
        
    facts_str = "\n".join(f"- {f}" for f in payload.facts) if payload.facts else "No specific facts. Use generic reason."
    
    return PROACTIVE_PROMPT_TEMPLATE.format(
        reason=payload.reason.name,
        facts=facts_str,
        urgency=payload.urgency,
        max_words=max_words,
        strategy=strategy,
        tone=tone
    )

async def format_ping(
    payload: ContextPayload, 
    strategy: str, 
    tone: str, 
    debug: bool = False
) -> str | dict:
    """Форматирует проактивное сообщение через LLM.
    
    В production возвращает только строку с сообщением.
    В debug режиме возвращает словарь с полной трассировкой промпта.
    """
    prompt = assemble_prompt(payload, strategy, tone)
    
    try:
        response_text = await aio_oneshot(prompt, MODEL_NAME)
        message = response_text.strip().strip('"').strip("'")
    except Exception as e:
        message = f"Error generating ping: {e}"
        
    if debug:
        return {
            "reason": payload.reason.name,
            "urgency": payload.urgency,
            "prompt": prompt,
            "message": message
        }
        
    return message
