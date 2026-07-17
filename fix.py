with open('c:/Games/companion/background_scheduler.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

del lines[88:177]

for i, line in enumerate(lines):
    if 'recent_facts = store.recent_facts(10)' in line:
        lines[i] = '        recent_facts = store.recent_facts(10)\n\n        reflection = await user_model.reflect_after_interaction(\n            user_message=state.user_message,\n            bot_response=state.llm_response,\n            facts_extracted=recent_facts,\n            mood_state=state.mood_state,\n        )\n'
        del lines[i+1:i+3]
        break

with open('c:/Games/companion/background_scheduler.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
