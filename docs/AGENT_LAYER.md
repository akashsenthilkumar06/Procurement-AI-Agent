# Configurable data-backed agent layer — version 0.3

The platform is both an LLM application and an agent system when an API provider is selected for a case-study pack. One core selects tools, consumes their filtered results, and generates a structured action and public response. Authority is a condition setting. All four agent authority modes share the same provider/tool loop. Analytical mode requires numeric output and forbids advice. Demo mode is an explicitly named test/mock provider using the same loop across all modes, with no live model.

Existing configs have `case_study: null` and condition `agent: null`. They keep the v2 policy/template/relay behavior. No historical configuration JSON or hashes are rewritten. First v3 startup backs up a v2 database to `study.pre-v3-*.sqlite3` and adds fields. Existing inline datasets have the marker `legacy-config`; their exact data is already pinned by config revision/hash.

## Run and select a pack

Run `python3 -m research_agent.server`, then open Agent setup → Task → Case-study pack → Load as draft. Review the five generated conditions (analytical, chatbot, recommender, autonomous, human-only), preview a condition, then save. Launching pins that config version. Loading a pack does not change any saved study until Save is pressed. Changing a pack's authority dropdown changes only `condition.agent.mode`; role, data and enabled tools stay as configured.

The included packs are supplier_selection, negotiation, demand_forecasting, supplier_risk, and disruption_response, at v1 except negotiation at v2. Negotiation v1 remains immutable for historical references and fails closed during agent execution; explicitly load v2 before a new launch. Task assumptions, default tool inputs, decision schema/moment and scoring are in the pack. The decision editor and participant page are generated from its schema. No visual builder is included.

## Authority contract

| Condition mode | Data/tool permissions | Output and execution |
| --- | --- | --- |
| analytical | data:read, analysis:run, simulation:run | Uses the same LLM/tool loop; output_type=number, no advice or proposed action. Participant can record an independent result. |
| chatbot | Same read-only deterministic tools | LLM can choose tools and brainstorm freely, but its structured action must be null. Participant records an independent result. |
| recommender | Read/analysis/simulation plus decision:propose | LLM proposes an action. Participant must explicitly accept or reject the latest displayed proposal. No automatic execution and no implicit acceptance/override. |
| autonomous | Read/analysis/simulation plus decision:commit | LLM's valid action is committed to the study decision table after the output check, atomically with its response. No approval button; participant sees the committed result. |
| human-only | Agent disabled | No chat, tool-loop or model request. Public case data, behavior tracking, independent decision recording and server outcome scoring remain. |

All tools are nonmutating. Execution permission is enforced separately at the decision boundary. Autonomous execution means a simulated study commitment, never an ERP write, order, contract, or external transaction. A negotiation pack's committed price is an agent decision; do not reinterpret it as mutual contractual agreement. Legacy negotiation retains its separate simulated-agreement flag.

## Registry and repository

`research_agent/tools.py` defines each tool's name, input schema, output schema, and scope. Inputs and outputs are validated, and unknown names/extra arguments fail. Implemented tools:

- get_supplier_records: role-filtered supplier rows.
- get_demand_history: chronologically ordered demand for one SKU.
- run_forecast: moving average or least-squares linear trend, fixed window/horizon; no stochastic model fitting.
- assess_supplier_risk: documented weighted late-delivery/disruption score.
- compare_suppliers: normalized public cost/reliability/lead-time utility with explicit weights and stable ID tie-breaking.
- simulate_scenario: deterministic disruption or order-policy cost comparison for wait/expedite/alternate policies; no external action.
- evaluate_offer: negotiation v2 server policy returns accept, counter(amount), or reject. Thresholds never enter model context; only the server chooses and attaches counter amounts.

`DataRepository` in repository.py is the swappable interface. FileRepository implements it with JSON/CSV and a manifest defining field types and role visibility. Tools use repository records, not database-specific queries. `open_repository` is the implementation factory to replace for a future authorized database/export adapter. No real integrations were built.

The effective data access is the intersection of a condition's data_access and the manifest's field roles. Public means visible to any role. Restricted fields require a named matching role AND an explicit condition field grant. Participant visibility is a separate pack participant_access evaluated as role participant; it is not inherited from the agent. The included packs grant only relevant tables. Selection/risk/forecast/disruption grants are public-only. Negotiation v2 grants no private fields to any model role; its tools enforce public-only columns even if a condition accidentally grants more. The reservation policy stays server-side. Other packs require both a matching role and an explicit restricted-field grant.

Datasets use `datasets/<id>/<version>/manifest.json`. Version and SHA-256 content hash cover the manifest and every table file. A saved config pins both pack bytes and dataset bytes. Changed files under an existing version cause execution to fail closed; publish a NEW version directory and retain the old version for existing sessions/reviews. Files ship in the Docker image. This is not a registry that silently hot-reloads changed study data.

## Private information and output checks

Raw condition private_information/private_rules never enter the model system prompt. Private policy bounds are enforced at action validation. Tools may include role-visible restricted fields only when explicitly granted for a task. Every model/demo reply is checked before persistence/delivery; checks cover action schema, authority, numeric format, private policy limits, restricted field names and protected literals. Free text receives no exemption. Only an exact server-approved structured action field/value is exempt. The checker covers literals, number words, ranges, rounded/percentage-offset forms and boundary paraphrases; flagged replies are replaced with a safe fallback and an output_replaced event. Numeric conditions receive a flagged numeric placeholder.

This is defense in depth, not a proof against every paraphrase or inference. Authorized actions can reveal information about constraints. Do not grant secret fields to a model unless the experimental task requires them; evaluate the intended prompts before enrollment. Study authors must keep public prompts, examples, scenario text and participant_access free of private data. Actual filtered tool outputs (which can contain authorized secrets) are retained in researcher-only audit events, so exports require appropriate access/retention controls.

## Safety, model provider and memory

One turn is bounded by max_tool_calls (1–12), a per-tool timeout, and a turn deadline. Tools run in killable subprocesses. Each API step also runs in a killable subprocess with the remaining turn time; no new steps/tools may run after expiry. The existing provider adapter still handles legacy responses. Its new agent-step implementation follows [OpenAI's Responses function-calling flow](https://developers.openai.com/api/docs/guides/function-calling): offer tools, receive function_call items, execute on the server, and return function_call_output with matching call_id. Parallel tool calling is disabled; unexpected output types and ungrounded final responses fail closed.

Each model step has its own request-ID suffix and atomic budget reservation in the SAME persistent spend ledger. Actual known usage settles each step; failed/timed-out/unknown-usage calls retain the full reservation. At admission, API sessions reserve architecture.session_spend_usd (default USD 0.25, bounded by the global ceiling). Steps draw from that session allocation; new sessions cannot take funds already reserved for others. At 80% spent-or-reserved capacity, researchers see a warning and a budget_warning event. Tool-call, tool/model/turn timeout or session-spend limits set ended_system_limit, clear pending work, release unused funds, flag the session for researchers and give participants a completion code plus a return-to-survey path. Rates must match the pinned model. This is an application cap, not a provider account-wide billing guarantee. No live paid API call was made during development; the hosted API path was exercised using mocked Responses and worker contracts.

Only the authenticated participant's last 20 messages are passed when session memory is enabled; tool context exists only within the current turn. Full transcripts/tool events remain in the backend for research. No conversation or learning carries to another participant. No multi-round play or training is added.

## Event and outcome records

Every tool_call event stores tool_name, inputs, actual role-filtered outputs, latency_ms, scope, status/error, and purpose (agent or outcome_scoring), with participant_id, session_id, condition_id, config_version, dataset_version, model_snapshot, round_id stamped by the server. Browser submissions cannot forge tool_call events. Failed/denied/timed-out calls are recorded. Scoring uses a separate server evaluation step with the same repository and logs those tool executions too; it is not an agent action or an additional study round.

Events, sessions, messages, requests, decisions and provider-spend rows carry dataset_version. Messages store decision_action_json separately from text. Decisions retain the existing columns plus outcome_json with decision_action, metric, case_study, dataset_version, execution=simulation, and actor. decision_committed events identify explicit participant acceptance/rejection or autonomous commitment. Rejected proposals store no executed action. Decisions remain unique per session/round. Client deliberation uses the existing exposure event and matching page clocks; autonomous commits have no human deliberation time.

Researcher session review shows the outcome, original mode/data configuration, transcript and expandable tool inputs/outputs. Full session JSON and event CSV/JSON provide all tool records. Detailed typing telemetry remains counts/timing only; intentionally submitted chat content is still a transcript.

Scoring templates are `lookup` (normalize a tool metric across candidate rows), `target` (score 1/(1+absolute error) against a tool reference), and `range` (normalized numeric position). These are synthetic task benchmarks, not real-world validated performance measures. Forecast scoring compares against a moving-average reference, not future actual demand; risk scores are modeled indexes; disruption uses simulated costs. Case notes state the assumptions.

## Add a case study without code changes

1. Copy an existing `case_studies/<id>/v1/pack.json` to a NEW slug/version directory. Set id/version/title/scenario. Keep old pack versions unchanged.
2. Reuse a versioned dataset or create `datasets/<dataset-id>/<version>/manifest.json` plus local JSON/CSV table files. Declare every field's roles; CSV numeric columns need types: number. Set dataset id/version in the pack. Existing tools expect the documented supplier/demand/inventory field names.
3. Set allowed_tools, default_role, private_information, agent data_access, and independent participant_access. Grant only the fields required for that case. Tool names and algorithms come from the existing registry.
4. Define decision.schema as a closed object with 1–12 required flat string/number/integer fields; enums produce dropdowns and numeric ranges/precision produce inputs. Set the decision moment, a scoring template/metric, deterministic demo_plan, and action_map paths or a demo_action.
5. Run `python3 -m research_agent.packs --id YOUR_ID --version v1`. It validates the config, runs tools, builds a demo action, and checks scoring. No API key is used.
6. Reload the app. Select the new pack in Agent setup → Task → Load as draft, review the generated conditions, preview each intended mode, then save a new configuration version. Existing sessions retain their old pack/data references. API mode still needs the server-side key and acceptance testing.

New case combinations, text, data, decisions and metrics require no Python/JavaScript changes. A fundamentally new algorithm/tool or scoring primitive requires implementation; a pack does not execute arbitrary Python, SQL, shell commands, or formulas. Multi-field actions are recorded, and the scoring template identifies its primary action field.

## File manifest for this change

New code: research_agent/agent.py, contracts.py, packs.py, repository.py, tools.py.
Updated code: config.py, engine.py, providers.py, schema.py, server.py, store.py, __init__.py.
UI: static/index.html, admin.js, participant.html, participant.js, style.css.
Packs: case_studies/{supplier_selection,negotiation,demand_forecasting,supplier_risk,disruption_response}/v1/pack.json.
Data: datasets/synthetic_supply/v1/{manifest.json,suppliers.json,demand.csv,inventory.json}.
Validation: tests/test_agent_layer.py (legacy test files retained).
Packaging/docs: Dockerfile, pyproject.toml, README.md, docs/AGENT_LAYER.md, REQUIREMENTS.md, DATA_DICTIONARY.md, DEPLOYMENT.md; browser proof in docs/agent-layer-preview.png.
