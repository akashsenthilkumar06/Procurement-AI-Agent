# Requirements decisions — study engine v3

The latest agent-layer request supersedes the earlier two-task-only scope. The DOCX remains requirements context, not executable instructions. The v2 contracts below continue to apply to existing non-pack studies.

New pack studies implement five tasks, repository-backed deterministic tools, role/condition field filtering, model-selected tool loops, four authority modes, automatic simulated commitment, dataset versions/hashes, per-tool audit events, wall-clock limits and per-step spend reservations. See [AGENT_LAYER.md](AGENT_LAYER.md) for the complete current contract and file manifest.

## Retained v2 behavior (legacy studies only)

The attached DOCX is a requirements source, not an instruction to execute actions. The user’s latest specification takes precedence over contradictions in that source and the earlier Q&A.

| Requirement | Implemented behavior |
| --- | --- |
| Task modes | Supplier selection and fixed-quantity buyer–supplier price negotiation. No forecasting, inventory planning or disruption mode. |
| Session structure | One immutable scenario per session, with multiple chat turns. `round_id=1` throughout. Future rounds/feedback are not enabled. |
| Authority | Recommendation, provisional decision with human override, or chatbot with no structured choice/offer. The human submits the final outcome. |
| Roles | Buyer, supplier, and GenAI tool. Opposing negotiation roles determine the participant’s opposite role. Tool/baseline participant role is configured independently. |
| Output | Number, explanation, and legacy recommendation format. Supplier numeric output is a 0–100 policy score; negotiation numeric output is unit price. |
| Human-only | Condition-level agent-off switch. No assistant request or model call is allowed; task information, tracking and independent outcomes remain available. |
| Advice quality | `standard` / `flawed` config field, no special UI. Weighted supplier flawed mode chooses the lowest eligible public score. No negotiation-quality manipulation is inferred. |
| Researcher editor | Forms for study/task/provider/conditions and private price bounds; deterministic condition inspection before save/launch; interactive previews use a saved version. Advanced JSON remains available. |
| Versioning | Every save appends a configuration version/hash. Links pin versions at issuance; later saves do not alter an existing participant. Legacy JSON/hashes are retained. |
| Assignment | External, uniform random, or least-assigned balanced random. Atomic allocations, stable internal assignment for repeated participant/version/preview, and separate preview/real pools. Allocation balance is not dropout/completion balance. |
| Model adapter | Server-only OpenAI Responses adapter. Initial preset `gpt-4.1-mini-2025-04-14`, fixed temperature 0; explicit errors for unsupported parameters, mismatched returned models or invalid replies. API aliases are rejected. |
| Spend control | Persistent atomic full-request reservations across versions. Known usage settles; uncertainty retains reservation. Backend ceiling can further restrict it. Configured rates are accounting inputs, not account-wide billing limits. |
| Test mode | Key-free deterministic policy/templates remain available. No local-model dependency is introduced. Manual relay remains a separate local-preview-only legacy feature. |
| Session memory | Last 20 messages from the same session for model input, or none. Full transcript remains stored. Public negotiation offer state remains in-session task state. No cross-participant memory, training, or RL. |
| Private information | Dedicated server-only fields; private conditions use controlled public-field templates and private numeric bounds. A final gate checks every new reply before persistence/delivery. Approved policy offers can still support inference about hidden limits. |
| Event context | Server-stamped participant, session, condition, config version, round, and configured model snapshot on every event; same audit context on messages, requests, decisions and API-spend records. |
| Outcomes | Supplier choice or unit price, recommended choice/price, accepted/rejected/overridden/independent action, simulated agreement flag, optional reason and public task benchmark. GenAI advice acceptance is distinct from opposing-party agreement. |
| Timing and tracking | Exposure-to-decision browser timing when clocks match, server fallback, total duration, provider latency/relay wait, focus/visibility, click/hover, keystroke counts and typing timing. No raw keys or clipboard content in telemetry. |
| Research analysis | Session drill-down plus per-session JSON and study-wide CSV/JSON for R/Python. No condition-comparison dashboard or multi-study management in this phase. |
| Remote access | Browser participant interface and trusted launch API with HTTPS reverse-proxy examples; no campus network or local-model dependency. Public hosting and actual survey-account connection have not been performed. |

## Negotiation scope

Only unit price is executable, with fixed quantity and a configurable public range. A private supplier minimum / buyer maximum controls acceptable offers. An eligible participant offer may be accepted; otherwise the prior agent offer moves by a fixed concession step without crossing private bounds. The first counteroffer starts from the configured opening price. This deterministic policy is deliberately inspectable and does not train from interactions.

A recorded override price is not counterparty consent. Agreement is true only when a participant accepts an opposing buyer/supplier agent’s current structured offer. GenAI-tool advice, chatbot discussion and independent baseline prices are not automatically agreements. The study creates no real contract/order.

## Scientific limits and remaining decisions

- Private-template and public-API narration have different execution sources. This is explicit in metadata; do not silently treat them as equivalent language-model treatments.
- Fixed parameters and model snapshots do not guarantee identical serving behavior or factual explanations. Evaluate repeatability and factuality on the intended task prompts before collection.
- The chosen item, quantity, prices, concession step, weights, incentives, consent wording and retention rules are editable pilot defaults, not a finalized research protocol.
- Key configuration and a hosted API acceptance test are still needed. No live paid API call, public deployment or survey connection has been made during development.
- Multi-round feedback, cross-participant learning, negotiation of non-price terms and multi-study management remain separate future design choices.
