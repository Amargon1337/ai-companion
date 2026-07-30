# Evaluation Framework

The fixtures in this directory are the first, deterministic quality gate for
memory, retrieval, reasoning, personality, and hallucination behaviour. Each
JSON file contains an array of scenarios. A scenario describes:

- `query`: the user request;
- `candidates`: offline facts, or `mode: "replay"` and `replay_id` for a real
  SQLite replay;
- `must_retrieve` and `must_not_retrieve`: facts that should and should not be
  selected;
- `response_must_contain` and `response_must_not_contain`: response rules;
- `top_k`: maximum number of selected facts.

Run the complete suite from the repository root:

```text
python evaluate.py
python evaluate.py --summary
python evaluate.py --write-baseline --fail-on-regression
```

For replay scenarios, provide the database explicitly:

```text
python evaluate.py --db data/companion.db
```

`--write-baseline` stores only aggregate metrics in `evaluation/baseline.json`.
The report also contains per-scenario results and `suite_metrics`, so a
regression can be located without manually inspecting every fixture.

Offline results are a deterministic retrieval proxy. They are useful for
regression tests, but they do not replace replay evaluation against the real
FAISS/FTS/MMR/reranker pipeline. Add real conversations as replay scenarios or
as manually labelled golden fixtures before treating a score as production
quality.

## Replay Learning

The replay flywheel reuses the existing `retrieval_replays.payload`; it does not
add another SQLite table.

```text
python replay_tools.py export-golden --limit 300
python replay_tools.py benchmark
python replay_tools.py tune --output evaluation/tuning-report.json
python replay_tools.py learn --limit 10
```

`export-golden` creates `evaluation/golden.json`. Fill in
`must_retrieve_ids`, `must_not_retrieve_ids`, and `good_response_notes`
manually. Unlabelled rows are intentionally excluded from benchmark and tuning.

`learn` asks the configured LLM to critique unreviewed replays and stores the
annotation under `payload.learning`. The nightly runtime processes at most ten
new replays. The next user message also adds a conservative satisfaction proxy
under `payload.satisfaction`; it is evidence for analysis, not ground truth.
