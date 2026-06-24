# AI Companion First Migration Plan

## Scope

This document describes how to move the Telegram bot from a command-centric interface to an **AI Companion First** model without removing useful end-user capabilities.

Goal state:

- user communicates mostly via normal text;
- memory, mood analysis, event extraction, causal reasoning, prediction, and confidence work automatically;
- a small set of explicit high-value commands remains available for manual control.

This is an analysis and migration plan only. No runtime code changes are applied in this step.

---

## 1. Commands That Stay

Required keep set:

- `/start`
- `/help`
- `/search`
- `/summary`
- `/personality`
- `/remember`

### Current status of KEEP commands

| Command | Current status | Handler status | Registration status | Help status | Notes |
|---|---|---|---|---|---|
| `/start` | Exists | `companion/handlers/chat.py:37` | Not in `set_my_commands()` | Implicitly discoverable only | Valid onboarding command |
| `/help` | Exists | `companion/handlers/chat.py:46` | Registered in `companion/handlers/__init__.py:26` | Outdated and overloaded | Needs rewrite to match new command model |
| `/search` | Exists | `companion/handlers/chat.py:114` | Registered in `companion/handlers/__init__.py:31` | Mentioned in help | Keep both forced and automatic search modes |
| `/summary` | Missing | No handler | Not registered | Not mentioned | Current bot uses `/summarize` in `companion/handlers/chat.py:74` |
| `/personality` | Exists | `companion/handlers/analytics.py:51` branch `cmd == "personality"` | Not in `set_my_commands()` | Mentioned in help | Keep as explicit profile snapshot command |
| `/remember` | Missing as slash command | No slash handler | Not registered | Only text `запомни ...` mentioned | Existing logic lives in `companion/handlers/chat.py:144` + `companion/storage/legacy.py:235` |

### KEEP assessment

#### `/start`

- актуальна: yes;
- handler exists and is simple;
- registration is incomplete for bot menu visibility;
- does not duplicate another function;
- recommendation: keep as-is conceptually, but update response text and keyboard to match the reduced command set.

#### `/help`

- актуальна: yes;
- handler exists;
- registered;
- help text is outdated and references commands planned for removal or conversion;
- recommendation: keep, but rewrite into a concise hybrid help page that explains commands plus natural-language examples.

#### `/search`

- актуальна: yes;
- handler exists;
- registered;
- help text already reflects mixed explicit + automatic behavior;
- no harmful duplication: explicit `/search` is still useful as a forced override when the user wants web grounding even if classifier would not trigger it.

#### `/summary`

- currently absent;
- existing near-equivalent is `/summarize` in `companion/handlers/chat.py:74`;
- recommendation: rename the public command surface from `/summarize` to `/summary` and keep the logic as a manual summary trigger.

#### `/personality`

- актуальна: yes;
- implemented inside shared analytics handler in `companion/handlers/analytics.py:51`;
- not exposed via bot command menu;
- recommendation: keep and expose explicitly in bot commands and help; do not remove automatic personality updates behind the scenes.

#### `/remember`

- currently absent as slash command;
- equivalent functionality exists through text parsing of `запомни ...` in `companion/storage/legacy.py:235`;
- recommendation: keep the current natural-language trigger and add explicit `/remember <text>` as a wrapper over the same persistence logic.

---

## 2. Commands Converted To Natural Language Intents

These commands should lose their slash requirement, but their logic should remain available through an intent router.

### Convert list

- `/reset`
- `/context`
- `/facts`
- `/notes`
- `/export`
- `/timeline`
- `/year`
- `/moodweek`
- `/selfie`
- `/week`
- `/monthbook`
- `/retrospective`
- `/todo`
- `/whoami`
- `/selfmap`
- `/goal`
- `/think`
- `/log`

### NL intent mapping

| Current command | Future natural language entry points | Keep logic? | Why convert |
|---|---|---|---|
| `/reset` | "сбрось контекст", "давай начнем заново" | Yes | Session control is valid, slash syntax is not necessary |
| `/context` | "что было недавно?", "дай последний контекст" | Yes | Summary retrieval is useful but should feel conversational |
| `/facts` | "что ты обо мне помнишь?" | Yes | Natural memory inspection is core companion behavior |
| `/notes` | "покажи постоянную память" | Yes | Same data, better UX |
| `/export` | "экспортируй дневник" | Yes | Utility action, no need for slash |
| `/timeline` | "покажи хронологию" | Yes | Timeline readout fits intent routing |
| `/year` | "что было в 2025?" | Yes | Structured query, not command-bound |
| `/moodweek` | "как у меня с настроением за 2 недели?" | Yes | Read-only analytic request |
| `/selfie` | "сделай психопортрет" | Yes | LLM report should be intent-driven |
| `/week` | "сделай сводку недели" | Yes | Report request, not infrastructure |
| `/monthbook` | "собери главу за май" | Yes | Optional narrative report |
| `/retrospective` | "что изменилось за месяц?" | Yes | Strong fit for natural language |
| `/todo` | "добавь задачу...", "покажи задачи", "отметь вторую как готово" | Yes | Classic intent-router use case |
| `/whoami` | "кто ты?" | Yes | Conversational self-description |
| `/selfmap` | "что ты хорошо про меня понимаешь, а где пробелы?" | Yes | Companion introspection request |
| `/goal` | "моя цель...", "какие у меня цели?" | Yes | Goal logic should survive, syntax should not |
| `/think` | "какая у тебя сейчас модель моей ситуации?" | Yes | Explainable reasoning should be queryable via text |
| `/log` | "запиши в дневник..." | Yes | User intent is obvious without slash |

### Files impacted by CONVERT_TO_NATURAL_LANGUAGE

- `companion/handlers/chat.py`
- `companion/handlers/memory.py`
- `companion/handlers/analytics.py`
- `companion/handlers/reasoning.py`
- `companion/handlers/diagnostics.py`
- likely a new intent router module, for example `companion/intents.py` or `companion/services/intents.py`

### Handler migration strategy

Do not delete the business logic first. Split each slash handler into:

- transport layer: command parsing;
- service layer: actual operation;
- intent router: natural language mapping to the same service layer.

Recommended extraction targets:

- memory services;
- analytics/report services;
- todo service;
- reasoning service;
- self-model presentation service.

---

## 3. Commands To Fully Automate

These capabilities should become automatic subsystems in the regular response pipeline. Some may still be queryable indirectly, but they should no longer require dedicated slash commands.

### Fully automate list

- `/mood`
- `/causal`
- `/predict`
- `/confidence`
- `/addevent`
- `/search` automatic mode stays enabled, but `/search` itself remains in KEEP as a manual override

### Automation assessment

| Current command | Can run automatically | Can still be triggered by plain text | Can be part of reasoning pipeline | Keep command? | Notes |
|---|---|---|---|---|---|
| `/mood` | Yes | Yes | Yes | No | Auto mood inference should run per message or per important message |
| `/causal` | Yes | Yes | Yes | No | Reasoning engine should detect cause-analysis prompts and use causal links automatically |
| `/predict` | Yes | Yes | Yes | No | Future-oriented prompts should trigger forecast generation automatically |
| `/confidence` | Yes | Implicitly | Yes | No | Confidence should become metadata for every answer, not a separate command |
| `/addevent` | Yes | Yes | Yes | No | Event extraction should happen from high-importance user messages and summaries |
| `/search` auto mode | Already partly yes | Yes | Yes | Yes, manual override remains | Keep `/search`, automate everything else around it |

### Required automation integrations

#### Automatic mood analysis

Current state:

- retrieval has lightweight mood awareness in `companion/memory/retrieval.py:49`;
- explicit mood persistence exists only in `companion/handlers/analytics.py:21`.

Migration target:

- infer mood automatically for incoming text;
- persist mood candidates when confidence is high;
- allow explicit correction through plain text and optional `/remember`-style storage if needed.

#### Automatic event extraction

Current state:

- events are only created manually through `/addevent` in `companion/handlers/memory.py:98`;
- facts are extracted automatically during compress in `companion/llm/pipeline.py:269`.

Migration target:

- add event-candidate extraction either:
  - during `run_compress_pipeline()`, or
  - in a background task after high-importance messages.

#### Automatic causal reasoning

Current state:

- reasoning engine exists in `companion/reasoning.py`;
- causal logic is only surfaced via `/causal` in `companion/handlers/reasoning.py:55`.

Migration target:

- when user asks "почему", "из-за чего", "что привело", automatically retrieve relevant causal links;
- optionally generate new candidate causal links from repeated evidence;
- inject top causal context into the prompt.

#### Automatic prediction

Current state:

- prediction storage exists in `companion/reasoning.py:266`;
- only manual `/predict` uses it in `companion/handlers/reasoning.py:87`.

Migration target:

- detect future-oriented prompts, planning prompts, and explicit uncertainty about future outcomes;
- generate prediction candidates automatically;
- periodically verify predictions against new facts or events.

#### Automatic confidence

Current state:

- self-critique function exists in `companion/self_model.py:155`;
- it is not used in normal response generation;
- only `/confidence` exposes static confidence domains through `companion/handlers/diagnostics.py:172`.

Migration target:

- run self-critique after draft generation and before send;
- use critique result to:
  - soften unsupported claims,
  - add uncertainty markers,
  - optionally trigger search fallback on factual requests.

---

## 4. Commands To Delete

These are developer/debug/manual-ops features and should be removed from the Telegram companion surface.

### Delete list

- `/code`
- `/logs`
- `/dbinfo`
- `/errors`
- `/stats`
- `/memory_audit`
- `/yearbook`

### Delete rationale and cleanup map

| Command | Why remove | Files to change | Handler to remove | Extra cleanup |
|---|---|---|---|---|
| `/code` | Developer self-code inspection, not user-facing companion behavior | `companion/handlers/diagnostics.py`, `companion/handlers/chat.py`, `companion/handlers/__init__.py` | `cmd_code` | Remove help/menu references |
| `/logs` | Operational telemetry, not user conversation | `companion/handlers/diagnostics.py`, `companion/handlers/chat.py`, `companion/handlers/__init__.py` | `cmd_logs` | Move observability outside Telegram |
| `/dbinfo` | Database admin introspection | `companion/handlers/diagnostics.py`, `companion/handlers/chat.py` | `cmd_dbinfo` | Remove unused imports like `sqlite3` if diagnostics module is reduced |
| `/errors` | Internal error report; logging already exists automatically | `companion/handlers/diagnostics.py`, `companion/handlers/chat.py` | `cmd_errors` | Keep backend logging, remove chat report |
| `/stats` | Raw system counters, not companion UX | `companion/handlers/chat.py` | `cmd_stats` | Remove from help |
| `/memory_audit` | Internal audit, partial implementation, not end-user safe | `companion/handlers/diagnostics.py` | `cmd_memory_audit` | Replace later with background integrity checks if needed |
| `/yearbook` | No real implementation exists | `companion/handlers/chat.py` | None | Remove dead help reference only |

### Dependency check before delete

Checked current code references:

- handlers exist in `companion/handlers/diagnostics.py:18`, `:91`, `:126`, `:191`, `:237`;
- `/stats` exists in `companion/handlers/chat.py:94`;
- `/yearbook` exists only as help text mention in `companion/handlers/chat.py:56`;
- bot command menu includes debug items in `companion/handlers/__init__.py:24`.

No evidence was found that these commands are required by the normal user response pipeline.

---

## 5. Current Command Inventory By Target Category

### KEEP

- `/start`
- `/help`
- `/search`
- `/summary` (to be introduced as replacement for current `/summarize`)
- `/personality`
- `/remember` (to be introduced as explicit wrapper over existing remember logic)

### CONVERT_TO_NATURAL_LANGUAGE

- `/reset`
- `/context`
- `/facts`
- `/notes`
- `/export`
- `/timeline`
- `/year`
- `/moodweek`
- `/selfie`
- `/week`
- `/monthbook`
- `/retrospective`
- `/todo`
- `/whoami`
- `/selfmap`
- `/goal`
- `/think`
- `/log`

### FULLY_AUTOMATE

- `/mood`
- `/causal`
- `/predict`
- `/confidence`
- `/addevent`
- `/search` auto-mode enhancement only; command itself remains KEEP

### DELETE

- `/code`
- `/logs`
- `/dbinfo`
- `/errors`
- `/stats`
- `/memory_audit`
- `/yearbook`

---

## 6. Current Runtime Usage Audit

This section answers the required question: are the following systems used during an ordinary user response?

### Active goals

- status: **not used in normal response generation**;
- evidence: goal data is only surfaced via `companion/handlers/reasoning.py:12` and `/think` in `companion/handlers/reasoning.py:102`;
- no references from `companion/bot_core.py` or `companion/llm/sessions.py` during standard reply generation.

### Causal links

- status: **not used in normal response generation**;
- evidence: only used in `companion/handlers/reasoning.py:55` and read inside reasoning engine methods;
- not injected into retrieval or prompt building.

### Predictions

- status: **not used in normal response generation**;
- evidence: prediction summary and add flows are command-only in `companion/handlers/reasoning.py:87`.

### World model

- status: **loaded but effectively unused in normal response generation**;
- evidence:
  - world model is loaded in `companion/reasoning.py:153`;
  - `active_contexts` is only displayed through `/think` in `companion/handlers/reasoning.py:118`;
  - no normal pipeline injection path exists.

### Self critique

- status: **implemented but not integrated**;
- evidence:
  - critique function exists in `companion/self_model.py:155`;
  - `_generate_and_send_response()` in `companion/bot_core.py:314` contains a comment `Meta-critique skipped for brevity, but should be integrated` near `companion/bot_core.py:337`;
  - no active call path was found.

### What is already used automatically

- auto grounding decision in `companion/bot_core.py:216`;
- auto compress trigger in `companion/bot_core.py:224`;
- reflection background task in `companion/bot_core.py:342`;
- personality micro-update in `companion/bot_core.py:311`;
- compress pipeline updates facts/reflections/personality/master summary in `companion/llm/pipeline.py:269`, `:274`, `:276`, `:283`;
- master summary is included in system instruction in `companion/llm/sessions.py:24` and `:39`.

---

## 7. Pipeline Integration Plan

The reasoning engine, world model, self model, and retrieval should remain in the system and be integrated into the main runtime.

### 7.1 Normal response pipeline target

Future response pipeline:

1. Receive message.
2. Classify high-level intent:
   - chat;
   - memory query;
   - report request;
   - action request;
   - forced command.
3. Run automatic side analysis:
   - mood inference;
   - event candidate detection;
   - goal signal detection;
   - causal trigger detection;
   - future/prediction trigger detection.
4. Build retrieval context from:
   - facts;
   - reflections;
   - summaries;
   - master summary;
   - permanent notes;
   - personality snapshot;
   - active goals;
   - relevant causal links;
   - pending predictions;
   - world model active contexts.
5. If needed, perform web grounding automatically.
6. Generate draft response.
7. Run self-critique/confidence pass.
8. Soften claims, trigger search fallback, or add uncertainty markers if needed.
9. Send response.
10. Run background consolidation jobs.

### 7.2 Integration points by module

#### `companion/bot_core.py`

Add orchestrated hooks for:

- intent routing before raw LLM call;
- reasoning context assembly;
- self-critique after draft generation;
- background reasoning updates after important messages.

#### `companion/llm/sessions.py`

Extend `build_system_instruction()` to optionally include:

- active goals summary;
- relevant causal links;
- prediction snapshot;
- compact world model block.

#### `companion/memory/retrieval.py`

Extend retrieval bundle inputs to support:

- reasoning context objects;
- world model contexts;
- prediction relevance.

#### `companion/reasoning.py`

Keep the engine and data files, but add runtime-facing helpers:

- `get_active_goal_snapshot(query)`;
- `get_relevant_causal_context(query)`;
- `get_prediction_context(query)`;
- `update_world_model_from_message(...)`;
- `maybe_extract_goal_from_message(...)`;
- `maybe_extract_prediction_from_message(...)`;
- `maybe_extract_causal_signal_from_message(...)`.

#### `companion/self_model.py`

Keep the model and expose:

- critique metadata suitable for response post-processing;
- domain confidence tied to query classification;
- optional factual-risk scoring.

---

## 8. Files Expected To Change

### Command surface and routing

- `companion/handlers/__init__.py`
- `companion/handlers/chat.py`
- `companion/handlers/memory.py`
- `companion/handlers/analytics.py`
- `companion/handlers/reasoning.py`
- `companion/handlers/diagnostics.py`

### Runtime integration

- `companion/bot_core.py`
- `companion/llm/sessions.py`
- `companion/memory/retrieval.py`
- `companion/reasoning.py`
- `companion/self_model.py`
- `companion/llm/grounding.py`
- `companion/llm/pipeline.py`

### Storage/service support

- `companion/storage/legacy.py`
- possibly new modules such as:
  - `companion/intents.py`
  - `companion/services/memory_service.py`
  - `companion/services/report_service.py`
  - `companion/services/reasoning_service.py`

---

## 9. Risks

### Product risks

- intent misclassification may make former command actions feel less predictable;
- automatic event extraction may create noisy timeline entries;
- automatic mood capture can overfit sarcasm or rhetorical language;
- implicit causal/predictive reasoning can sound overconfident if self-critique is not integrated first.

### Technical risks

- handlers currently mix transport and business logic, so direct deletion can break functionality;
- `/remember` exists only as a text parser, so adding slash support requires careful reuse of the same persistence path;
- `/summary` is not implemented yet and must replace `/summarize` cleanly without breaking user habits;
- reasoning engine files are append-based JSONL stores and currently lack strong deduplication or verification workflows;
- world model persistence is incomplete in practice and may remain decorative unless update flows are added.

### Migration risks

- deleting diagnostics before cleaning imports and help/menu references will leave dead imports and stale command descriptions;
- converting too many commands at once can blur regression boundaries;
- keeping both old slash commands and new NL intents for too long increases maintenance complexity.

---

## 10. Recommended Migration Phases

### Phase 1: Normalize the public command contract

- keep `/start`, `/help`, `/search`, `/personality`;
- introduce `/summary` as public alias/replacement for current `/summarize`;
- introduce `/remember` as explicit wrapper over existing remember logic;
- rewrite bot command registration and help text to reflect only the supported command surface.

### Phase 2: Extract service layer

- move logic out of slash handlers into service functions;
- make command handlers call services;
- prepare the same services for natural-language intent routing.

### Phase 3: Add intent router

- route memory/report/action requests from ordinary text;
- convert `/goal`, `/facts`, `/context`, `/todo`, `/timeline`, `/retrospective`, and related commands to NL-only entry points.

### Phase 4: Integrate automatic reasoning

- integrate active goals, causal links, predictions, and world model into prompt building and retrieval;
- add background update hooks for event extraction, goal updates, and prediction candidates.

### Phase 5: Integrate self-critique

- run `self_model.critique_response()` on ordinary responses;
- use output to adjust certainty, trigger grounding, or annotate uncertainty.

### Phase 6: Remove debug command surface

- delete `/code`, `/logs`, `/dbinfo`, `/errors`, `/stats`, `/memory_audit`, `/yearbook` references;
- remove stale imports, registrations, and help entries;
- optionally move diagnostics into non-Telegram admin tooling.

---

## 11. Final Architecture Target

### User experience

- user mostly talks in plain text;
- explicit commands remain only for high-value manual overrides and companion affordances;
- the bot feels like a persistent companion, not a toolbox menu.

### Final public commands

- `/start`
- `/help`
- `/search`
- `/summary`
- `/personality`
- `/remember`

### Automatic subsystems

- memory consolidation;
- summary stack maintenance;
- personality updates;
- mood analysis;
- event extraction;
- causal reasoning;
- prediction tracking;
- confidence/self-critique;
- auto grounding when external knowledge is required.

### NL-first capabilities

- memory inspection;
- context recall;
- goals;
- todos;
- timeline queries;
- weekly/monthly reports;
- retrospectives;
- self-description and self-map requests.

---

## 12. Executive Summary

The codebase already contains the foundations of an AI companion:

- automatic compress;
- automatic grounding;
- reflections;
- personality updates;
- master summary;
- retrieval.

What it lacks is not intelligence, but **integration discipline**:

- many companion abilities are still exposed as slash commands instead of natural intents;
- reasoning engine state exists but is not part of ordinary replies;
- self-critique exists but is not executed;
- help and command registration are out of sync with the intended product shape.

The correct migration is **not** “remove all commands,” but:

- keep six high-value commands;
- convert report/action/memory utilities into NL intents;
- automate internal analysis subsystems;
- remove developer/debug commands from the user-facing Telegram surface;
- integrate reasoning, world model, predictions, and self-critique into the main response pipeline.
