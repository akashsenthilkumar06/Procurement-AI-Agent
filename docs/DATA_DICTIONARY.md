# Measurement and export definitions

Every data export includes enough identifiers to join sessions, conditions, and configuration revisions. Preview sessions must normally be excluded from study analysis with `preview == 0`. The full datasets are exported; the on-screen session list displays at most 1,000 recent sessions. Each row opens a researcher-only review page using the session’s immutable configuration revision. The Activity view aggregates all received counts, with filters and progressive expansion over the latest 80 lifecycle/exposure reports. The per-session JSON download includes all raw events, the transcript with response metadata, decision, metrics, and the review setup snapshot. Session capabilities and raw private configuration fields are excluded; full private configuration remains in researcher revision exports.

## Context and v2 migration

Every event, message, request, decision and API-spend row physically stores `participant_id`, `session_id`, `condition_id`, `config_version`, `round_id`, and `model_snapshot`. Values come from the authenticated server session, never client-supplied identity claims. `round_id` is always 1 in this release; it does not denote a chat turn. A session has one scenario and many turns. Decisions are unique per `(session_id, round_id)` to allow future rounds without rebuilding that constraint.

Legacy records are enriched from their saved revisions, with round 1, after an automatic pre-v2 database backup. Original config JSON, hash, messages, decisions and timestamps remain unchanged. Legacy `revision_id` fields remain available; `config_version` refers to the same revision identifier. Session `model_snapshot` is the configured execution model/marker, including demo/relay and human-only conditions. Individual response metadata distinguishes actual API, demo, private-template or relay execution and the returned API snapshot; no API run is inferred from a baseline’s configured model.

Timestamps ending in `_at` are server Unix seconds in UTC. Browser `client_wall_ms` is Unix milliseconds and may be inaccurate. Browser `client_ms` is monotonic milliseconds within one document instance (`page_id`); do not subtract clocks from different page instances or devices.

| Dataset | Main fields | Interpretation |
| --- | --- | --- |
| sessions | id, participant_id, condition_id, revision_id, config_hash, preview | Pseudonymous participant and exact assigned experimental setup. Participant IDs need not be unique; repeat sessions remain separate. |
| sessions | created_at, ended_at, status, total_duration_ms | Session begins after consent. Completed/withdrawn sessions have an end timestamp. An active session with no end is right-censored, not a zero-duration session. |
| sessions | consent_version, consent_at | SHA-256 of consent wording, and server time of affirmative consent. Full wording is preserved in the revision. |
| sessions | prompt_count, prompt_characters | All persisted user prompts, including prompts whose model request failed. Character count uses Unicode code points in SQLite. |
| messages | id, session_id, role, content, created_at, request_id | Complete stored user/assistant conversation. `request_id` pairs each assistant response with its prompt. Failed prompts have no corresponding assistant message. |
| messages | option_id, metadata_json | Structured agent choice; requested and returned model; provider/engine/execution source; API response ID, usage, and latency when applicable. These are researcher-only. |
| messages | seen_at, seen_client_ms, page_id | First reported visible exposure of the response text or its recommendation panel. Browser checks visible document and viewport intersection, then timestamps the exposure. It does not verify eye gaze, comprehension, or active attention. |
| events | id, session_id, type, received_at, client_ms, client_wall_ms, page_id, payload_json | Append-only browser event records; duplicate IDs are ignored. Server receipt time and browser clocks are both retained. Client reports can be tampered with. |
| decisions | action | accept, override, reject, or independent. Reject ends the trial without a final choice. Independent means no agent choice was issued at submission. |
| decisions | message_id, recommended_option, final_option, reason | Agent response used for the decision, agent choice, participant choice, and optional explanation. The server requires the latest response and verifies action/choice consistency. |
| decisions | client_deliberation_ms | Decision time minus first visible exposure, using the browser monotonic clock only when page IDs match. Includes pauses/hidden time after initial exposure; it is not active deliberation time. Null on reload when the original clock is unavailable. |
| decisions | server_since_seen_ms | Server decision receipt minus server receipt of the first exposure report. Includes network delay and idle/hidden time. A fallback measure, not identical to browser deliberation. |
| decisions | task, final_price, recommended_price, agreement, outcome_json | Negotiation records a unit price and an explicit simulated-agreement flag. Accepting an opposing buyer/supplier agent’s current offer is an agreement; overriding, rejecting, recording an independent price, or adopting GenAI-tool advice does not establish counterparty agreement. `outcome_json` includes currency, quantity, recorded unit price, participant role and agreed total when applicable. |
| messages / requests | offer_price | Structured participant price or agent offer. Free-text numbers are not treated as executable offers. Chatbot replies do not carry a structured offer. |
| provider_spend | reserved_micros, charged_micros, status, request_id | API application-budget ledger in integer micro-USD. Pending/uncertain requests retain the maximum configured reservation. Settled rows use usage-based configured rates. This is separate from provider billing and unrelated API clients. |
| decisions | outcome_score, score_regret | For supplier selection: the final option’s normalized public weighted score and best public score minus that score. For negotiation: position in the saved public price range, favouring a lower price for buyers and a higher price for suppliers; regret is 1 minus that position score. Null without a recorded choice/price. These are public benchmarks, excluding private bounds, and not observed payoffs or proof of agreement. |
| revisions | id, created_at, config_hash, config_json | Immutable complete configurations. Researcher-only; contains private information. Archive securely. |

## Timing questions

“How fast was the agent?” is `metadata_json.latency_ms`: elapsed server time spent running the decision/model adapter, including the provider round trip. It excludes browser network and rendering time and does not measure time to first token because responses are not streamed. Template responses report policy/template time rather than a nonexistent provider call.

Manual chat relay uses `latency_ms: null` and `relay_wait_ms` for time from queued prompt to reply delivery. `response_source` is `manual_chat_relay` and `returned_model` is unknown. Do not combine relay wait with API/model latency. Relay sessions are always previews, and the browser may take up to roughly 1.5 seconds to display a delivered reply.

“How long did the participant take after seeing advice?” is `client_deliberation_ms`, with `server_since_seen_ms` kept separately for auditing or fallback. Both concern the latest recommendation. The first recommendation exposure across a session is also available from `messages.seen_at` if the protocol calls for first-advice-to-final-decision time.

“How long did the session last?” is `total_duration_ms` for completed or withdrawn sessions. Active observation time must be reconstructed from focus/visibility events with a prespecified rule. Browser closure does not reliably deliver a final event, so abandonment remains an incomplete session. Do not impute a precise exit time from a missing event.

## Interaction events

- `page_open` and `page_exit`: page lifecycle. Exit events are best effort.
- `visibility`: visible/hidden tab transitions; `focus`: focused/blurred window. These establish leave/return sequences inside this study page.
- `recommendation_visible`: response `message_id`. Generated after DOM rendering when visible content intersects the viewport, rather than at network response arrival. Repeated reports do not reset first exposure.
- `interaction_counts`: `keystrokes`, `clicks`, `copies`, `pastes`, `cut`, `paste_characters`, `typing_ms`, `hover_ms`. Buffered for about five seconds and flushed before final submission.

Keystrokes are keydown counts while an input/textarea is focused. Raw keys are not stored. `typing_ms` sums intervals between successive keydowns with gaps under two seconds; it is a burst-duration estimate. Hover time measures time over supplier comparison rows, aggregated across cards. Interaction counts contain no clipboard text. Text a participant pastes and submits as a chat prompt becomes part of the complete transcript. Mobile keyboards, accessibility tools, IME composition, paste, and browser restrictions can make counts incomplete. No off-page activity is observed.

Setting `telemetry.enabled` to false disables detailed interaction counts. Core page lifecycle and recommendation exposure events remain for experimental timing. Update consent text to match the actual study protocol.

`telemetry.clipboard_text` and `telemetry.outside_ai_question` are separate, optional flags. The per-session `usage_evidence` section contains permission, clipboard excerpts, self-report, recorded agent outputs, and researcher assessments. Clipboard text stays out of the bulk events export. See [AI use evidence](AI_USE_EVIDENCE.md).

## CSV, JSON, and analysis

CSV is UTF-8 with a BOM for Excel. Nested payloads/configs are JSON strings; use `json.loads` in Python or `jsonlite::fromJSON` in R. Strings beginning with formula markers are prefixed with an apostrophe in CSV; use JSON exports when exact text fidelity matters. Credentials and token hashes are not exported.

Human-only baselines retain core lifecycle/count tracking and outcome records but have no assistant messages or provider calls. `advice_quality` is a stored condition field; weighted supplier `flawed` advice selects the lowest eligible public score, while outcomes keep the unmodified public benchmark.

Download datasets in the researcher console. `examples/analyze_exports.py` joins the CSV files without external packages and prints behavior, timing, and example task outcomes. Keep original exports unchanged and archive configurations alongside data.


## V3 pack-agent records

See [AGENT_LAYER.md](AGENT_LAYER.md#event-and-outcome-records). `dataset_version` is now physically stored on every event/message/request/decision/spend row and session/launch. Existing inline studies use `legacy-config`; new packs use dataset-id@version with content hashes pinned in config. `tool_call` is server-only and records inputs, role-filtered outputs, latency, status, scope, purpose, and full audit context. `decision_committed` records the outcome actor/action.

Messages add `decision_action_json`; pack decisions put structured actions, metric/scoring details and actor in `outcome_json`. `action=autonomous` means the server committed without participant approval after the output check. `accept`/`reject` mean explicit recommender decisions; `independent` covers analytical/chatbot/human-only. Existing v2 columns and semantics remain for non-pack studies. Human-only outcome scoring is a server evaluation, not agent usage. A scoring tool event has `purpose=outcome_scoring`. API-step budget rows use request_id suffixes, one reservation per step.

Researcher-visible tool outputs may contain role-authorized restricted fields; treat these exports as private research data. Public benchmark scores are not causal performance claims, and forecast reference error is not future forecast accuracy.
