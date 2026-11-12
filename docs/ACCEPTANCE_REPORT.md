# Agent-layer acceptance report

Generated 2026-11-11T17:50:46-06:00 (America/Chicago).

**PASS 17 / FAIL 0 / NOT RUN 2.** Acceptance-table counts; regression/control assertions are counted separately.

Evidence: [../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d). Synthetic isolated databases only; no running study configuration or database was changed.

| Test | Status | Evidence | How to reproduce |
| --- | --- | --- | --- |
| 2. Tool determinism (keyless) | PASS | 12 input profiles × 10 direct tool runs; exact outputs hashed. 10 scripted mock repeats also ran; scripted choice variance 0/9 is not a measurement of LLM variability. [determinism.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/determinism.json). | python3 tests/acceptance/run.py keyless |
| 3a. Input-side private data (keyless) | PASS | All 5 packs × 3 roles × 2 turns; 90 exact adapter requests; 246 literal occurrences, 0 unauthorized private fields; 246 non-required literal matches (public schema/scenario coincidences and derived shortage=100, independently reproduced with public-only data). Proven public numeric coincidences remain listed; they are not secret-field exposures. Negotiation policy value exposures=0; restricted fields and 95 must be absent for every role. Every occurrence/path/authorization is in [input_leaks.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/input_leaks.json). This does not prove absence of semantic inference. | python3 tests/acceptance/run.py keyless |
| 3b. Output-side private data (keyless) | PASS | 13 scripted variants across policy-only negotiation and a role-required-context fixture; missed=[]. Every unsafe narrative must be replaced and logged; only exact approved structured price=95 is retained. [output_leaks.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/output_leaks.json). | python3 tests/acceptance/run.py keyless |
| 4a. Mode-only config diff | PASS | Only /conditions/0/agent/mode changes. Same ID, role, tools, data, prompt and provider. [authority.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/authority.json). | python3 tests/acceptance/run.py keyless |
| 4b. Runtime invariance | PASS | Actual function paths/stages equal=True; model calls=[('analytical', 3), ('chatbot', 3), ('recommender', 3), ('autonomous', 3)]; analytical is numeric with no advice. [authority.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/authority.json). | python3 tests/acceptance/run.py keyless |
| 4c. Recommender decision moments | PASS | Both accept and reject after exposure; server timestamps and matching-page 300 ms client timing retained. [authority.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/authority.json). | python3 tests/acceptance/run.py keyless |
| 4d. Autonomous without approval | PASS | Message request alone commits the validated outcome; no participant approval request. [authority.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/authority.json). | python3 tests/acceptance/run.py keyless |
| 5. Limit: tool_call_cap | PASS | Terminal=ended_system_limit; clear participant message; return_to_survey path; pending=0; state event=True; researcher flag=True. [limits.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/limits.json). | python3 tests/acceptance/run.py keyless |
| 5. Limit: tool_timeout | PASS | Terminal=ended_system_limit; clear participant message; return_to_survey path; pending=0; state event=True; researcher flag=True. [limits.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/limits.json). | python3 tests/acceptance/run.py keyless |
| 5. Limit: model_step_timeout | PASS | Terminal=ended_system_limit; clear participant message; return_to_survey path; pending=0; state event=True; researcher flag=True. [limits.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/limits.json). | python3 tests/acceptance/run.py keyless |
| 5. Limit: spend_cap | PASS | Terminal=ended_system_limit; clear participant message; return_to_survey path; pending=0; state event=True; researcher flag=True. [limits.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/limits.json). | python3 tests/acceptance/run.py keyless |
| 5a. Session reserves, 80% warning and admission | PASS | Two USD 0.25 allocations against USD 0.50 cap; third never becomes active; funded session still completes provider steps; unused credit released and later admission succeeds. [budget_admission.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/budget_admission.json). | python3 tests/acceptance/run.py keyless |
| 6. New pack by files only | PASS | Copied pack AND dataset into new temporary versioned folders; changed scenario and Beacon cost to 117; actual CLI validation, admin load, tool execution and source hashes checked. Temporary folders removed. [file_only.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/file_only.json). | python3 tests/acceptance/run.py keyless |
| 7. State isolation | PASS | A had real multi-turn history; B started back-to-back in the same condition/version. Checked A marker, mutated config/result objects, later saved revision and fresh repository values. [isolation.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/isolation.json). | python3 tests/acceptance/run.py keyless |
| 1. Pin and log | PASS | 208 events; types={'budget_reserved': 51, 'tool_call': 130, 'output_replaced': 13, 'recommendation_visible': 2, 'decision_committed': 3, 'session_ended': 4, 'page_open': 1, 'page_exit': 1, 'visibility': 1, 'focus': 1, 'interaction_counts': 1}; missing=0, mismatched pins=0; family alias rejected=True. [pin.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/pin.json) lists every offending event/type. All event/session contexts compared. API-configured recording mock; no live API serving assertion. | python3 tests/acceptance/run.py keyless |
| Live execution gates | PASS | Missing opt-in, missing cap flag, missing key and negative cap all refused before any fixture/provider call. [live_gate.json](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/live_gate.json). | python3 tests/acceptance/run.py keyless |
| Existing regression suite | PASS | Full unittest discovery actually executed; exit=0. [regression.txt](../tests/acceptance/artifacts/keyless/20261111-175007-c42a5d/regression.txt) contains the count and output. | python3 tests/acceptance/run.py keyless |
| 2-live. Tool choices and determinism | NOT RUN | No paid model call; live tool-choice variance unknown. | RESEARCH_AGENT_LIVE_TESTS=1 python3 tests/acceptance/run.py live --max-spend 2 |
| 3c-live. Adversarial negotiation leaks | NOT RUN | 32 adversarial prompt cases prepared, including four multi-turn pressure sequences. No live API was called. | RESEARCH_AGENT_LIVE_TESTS=1 python3 tests/acceptance/run.py live --max-spend 2 |

## Bugs fixed

1. **Secret disclosure:** the new negotiation v2 pack removes all private field grants and replaces model-selected offers with evaluate_offer. Its server policy returns accept, counter(amount), or reject without private thresholds. Only the current participant offer is evaluated; model-supplied substitutes are refused. The model phrases the policy result, while the server attaches the authorized action. All negotiation model-visible system, user, history, and tool-result fields are redacted against protected values/names. Even accidental condition grants cannot expose restricted supplier columns through negotiation tools.
   Existing negotiation v1 files remain immutable for saved references; their agent execution now fails closed with a v2 migration message. New launches should use the v2 pack. No existing study was silently repinned.
2. **Output gate:** literal, written-out, containing-range, rounding, percentage-offset, boundary and just-under forms are checked conservatively. On a flag, the participant gets a safe fallback and an output_replaced event records flags without the unsafe candidate. Numeric-only conditions get a flagged numeric placeholder. Free text receives no approved-action or public-literal exemption. Only the exact server-approved structured field/value can retain a protected action value. Generic unapproved unsafe actions are withheld. Short numeric secrets stored as strings are protected; policy-narrative checks normalize written-out amounts and numbers followed by punctuation.
3. **Authority consistency:** all four modes use the same provider/tool loop, including analytical mode with output_type=number and no advice text. A mode-only config diff and actual Python function-entry traces plus stage traces compare the four code paths. Only format/action permissions and commitment differ. No analytical-specific no-LLM path remains. The explicitly named demo provider is a test/mock transport across all modes, not an experimental analytical alternative.
4. **Limits and reservations:** tool-call cap, per-tool/model/turn timeout and session spend exhaustion end the session as ended_system_limit, clear pending requests, release unused session funds, log the state/reason/return_to_survey path and surface a researcher flag. Sessions reserve a configurable allowance at admission (architecture.session_spend_usd, default USD 0.25, bounded by the global ceiling). Per-step full-cost allowances are charged against that allocation. New admissions are blocked before consuming funds reserved for an existing session. At 80% spent-or-reserved allocation, a budget_warning event and researcher warning appear.

## Files changed

- Core: research_agent/agent.py, privacy.py, tools.py, providers.py, config.py, packs.py, store.py, server.py, schema.py.
- New server helpers: research_agent/offer_policy.py, research_agent/budget.py. New versioned pack: case_studies/negotiation/v2/pack.json; v1 unchanged.
- Limit/fallback presentation only: static/participant.js, static/admin.js, static/index.html.
- Validation: tests/test_safety_fixes.py, tests/test_agent_layer.py, tests/test_research_v2.py, tests/acceptance/run.py, tests/acceptance/test_harness.py, tests/acceptance/secret_requirements.json, tests/acceptance/README.md. Documentation: README.md, docs/AGENT_LAYER.md, docs/ACCEPTANCE_REPORT.md. Browser evidence: docs/safety-limit-preview.png, docs/safety-budget-preview.png.

## Failures not fixed

No failed keyless acceptance rows remain.

The requested attack forms are covered by executable fixtures, not a proof against every possible encoding, paraphrase or mathematical inference. The conservative checker may replace legitimate analyses when a private quantity is implicated. Authorized accept/counter/reject outcomes can themselves support inference about business constraints; the policy tool never returns the threshold or rationale. No universal non-inference guarantee is claimed.

## Decisions for me

- Live tool-choice variance is NOT RUN until explicitly authorized with the gated command. The harness records sequence variation vs the first repeat and pairwise variation; no zero-variance promise is made.
- Tools and data access remain fixed in each saved condition; model-selected subsets/arguments remain allowed within those grants. No unrelated tool-planning policy was changed.
- Per-session allowance is bounded and explicit (USD 0.25 default). Raising it reserves more global capacity up front; lowering it can end a session earlier on its own allowance. Global allocation cannot steal an admitted session’s reserved funds. Uncertain calls retain their allowance. Existing pre-reservation API sessions obtain a lease before their next paid step; schedule upgrades before enrollment when possible.

## Reproduction and safety

Keyless: `python3 tests/acceptance/run.py keyless`. Live model tests are NOT RUN by this command; keyless control tests exercise only mocked/zero-dispatch gate and abort paths.

Live, after exporting OPENAI_API_KEY: `RESEARCH_AGENT_LIVE_TESTS=1 python3 tests/acceptance/run.py live --max-spend 2`.

Both RESEARCH_AGENT_LIVE_TESTS=1 and an explicit --max-spend flag are required. Flag alone defaults to USD 2. The persistent live ledger at tests/acceptance/artifacts/live/budget.sqlite3 is not reset by reruns. A would-exceed admission/request allowance aborts the harness cleanly (exit 3), retaining attempted evidence and marking incomplete live tests NOT RUN. New session allocations and each model step are accounted before provider dispatch; unused capacity is released at terminal outcomes or withdrawal.

The ceiling uses the pinned model’s configured token prices; it is not an account-wide provider billing lock. Recheck pricing before live use. No live model requests or paid API calls were made for this keyless report.

The input scanner retains every secret-value equality. Public scenario/schema constants and independently reproducible public tool results may numerically equal unrelated fixture secrets; those are labeled by provenance rather than treated as private-field exposures. Unknown values and all unauthorized restricted fields still fail. Negotiation’s actual policy value 95 must be absent from every captured request in the role matrix. A separate role-required-context fixture tests output protection when private supplier records are intentionally available.

Participant continuation uses the existing completion code and embedding completion event. The terminal screen links to a validated HTTPS survey referrer when available and otherwise instructs the participant to return to the originating survey tab/window. No external survey account was connected or redirected during tests.

The source migration adds session allowance/terminal-state storage and takes a pre-safety SQLite backup. Restart the application to load these changes; this harness did not migrate the running study database.

Provider protocol reference: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling).
