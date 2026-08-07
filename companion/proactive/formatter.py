from companion.proactive.collector import ContextPayload
from companion.llm.client import aio_oneshot
from companion.config import MODEL_NAME
from companion.llm.prompts import CORE_PERSONALITY

PROACTIVE_PROMPT_TEMPLATE = """# TASK
Generate exactly one proactive message.

# REASON
{reason}

# REASON FACTS
{facts}

# URGENCY
{urgency}

# RECENT PROACTIVE PINGS (DO NOT REPEAT TOPICS OR PHRASES)
{recent_pings_str}

# RANDOM MEMORY ANCHOR
{random_anchor_str}

# INNER DIARY / DREAM INSIGHT (YOUR BACKGROUND RECENT THOUGHT ABOUT IVAN)
{dream_insight_str}

# RULES
- Use only provided facts.
- Do not invent memories.
- Do not invent events.
- Do not ask more than one question.
- Keep message under {max_words} words.
- [CRITICAL] ИЗБЕГАЙ гиперопеки, банальных утешений и чрезмерной жалости.
- [CRITICAL] Действуй как строгий, но справедливый ИИ-партнер. Опирайся на долгосрочные цели пользователя и предостерегай от его глубинных страхов.
- Используй коучинговый подход: стимулируй к действию, бросай интеллектуальный вызов, вместо того чтобы просто "гладить по голове".
- [CRITICAL] DO NOT repeat topics, questions, or opening phrases from RECENT PROACTIVE PINGS.
- [CRITICAL] If INNER DIARY / DREAM INSIGHT is provided, use it as the atmospheric starting point or inspiration for your message.
- If no INNER DIARY is provided, use RANDOM MEMORY ANCHOR to ground your ping in a real memory from Ivan's past.
- NEVER write generic corporate check-ins ("How are you doing?", "Just checking in").

# FACT INTEGRITY
You may only reference facts listed in REASON FACTS, RANDOM MEMORY ANCHOR, or INNER DIARY / DREAM INSIGHT.

# 1. CORE_PERSONALITY (highest priority, use this identity)
{core_personality}

# 2. DIALOGUE_STRATEGY
{strategy}

# 3. EMOTIONAL_TONE
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
    recent_pings_str = "\n".join(f"- {p}" for p in payload.recent_pings) if payload.recent_pings else "None."
    random_anchor_str = payload.random_anchor if payload.random_anchor else "None."
    dream_insight_str = payload.dream_insight if payload.dream_insight else "None."
    
    return PROACTIVE_PROMPT_TEMPLATE.format(
        reason=payload.reason.name,
        facts=facts_str,
        urgency=payload.urgency,
        max_words=max_words,
        core_personality=CORE_PERSONALITY,
        strategy=strategy,
        tone=tone,
        recent_pings_str=recent_pings_str,
        random_anchor_str=random_anchor_str,
        dream_insight_str=dream_insight_str,
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
        response_text = await aio_oneshot(prompt, MODEL_NAME, temperature=0.7)
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
