# Detailed user guide

[Quick start](../README.md) · [Setup reference](SETUP.md)

## Researcher workspace

**Agent setup → New setup** opens a custom scenario. To use a case-study template, open **Design → Participant task → Replace with a case template**. The standard packs create analytical, chatbot, recommender, autonomous, and human-only conditions. Ozer uses its two role conditions. Review and save before launch. [Agent architecture, changed files, and instructions for adding a pack](AGENT_LAYER.md).

**Agent setup.** Start with a custom scenario or a template, then choose **Save draft** from the editor. Reopen or delete drafts from **Saved setups**. If you leave the editor or switch to another setup with unsaved work, choose whether to save, discard, or keep editing. **Save as copy** keeps your current edits in a separate slot. **Test this setup** uses the selected saved version. **Use for study** applies that version to newly issued study links. The card marked **In use** is the source of the study’s configuration; its editor shows **Used for study**. **Update available** means the saved agent differs from the applied study version. Cards show the task, group count, and provider. Delete appears on hover or keyboard focus, and stays visible on touch devices.

**Ozer Game.** Upload a matching protocol in **Generate with AI** or load the built-in Ozer template from **Design → Participant task → Replace with a case template**. Save a preset and use **Test this setup** to play either role without a key. With OpenAI selected and configured, you can opt into a live preview to test the saved counterpart instructions before enrolling anyone. The case has 3 practice rounds and 20 paid rounds. See the [Ozer protocol notes](OZER_GAME.md).

**Generate with AI** opens Setup chat. Describe your study or attach a PDF, DOCX, TXT, or Markdown protocol. OpenAI receives the brief, extracted text, and recent draft context. Use follow-up messages to revise the proposed case, conditions, and prompts, then choose **Save draft & edit** to open the draft in the setup editor. Messages, filenames, and generated drafts are saved to your account; uploaded files and extracted text are not stored separately. Older results are under **Earlier generations**.

Use the **…** menu beside a session to download it, edit its display label or researcher note, revoke access, or delete an ended session. Edits are tracked with the researcher and time. **Restore original** clears the current label and note and records the restore in the same history. Participant codes, transcripts, and decisions stay as recorded. The individual session download includes the edit history.

Use **Session chat** to create or refine an agent setup, analyze past conversations and decisions, inspect saved agents, or look up completion. Agent drafts appear in the conversation; **Save draft & edit** adds the reviewed draft to Saved setups. **Generate with AI** remains a shortcut to the existing drafting conversation. Both composers use **+** for PDF, DOCX, TXT, or Markdown documents.

Follow-up questions refer to the draft in that chat unless you name another setup or ask about session records. The draft card shows its participant response engine. OpenAI generates the draft; new drafts use **Demo responses** unless you ask for OpenAI. Say **“Change it to OpenAI”** to create an updated API draft, then **Save draft & edit** to save it. You can also change the engine through **Study controls → OpenAI → Save changes**. A server API key by itself does not switch the agent to OpenAI. Answers about a draft cite that draft's saved snapshot; they do not treat it as the applied study.

Choose study sessions, previews, or all sessions before asking about records. That scope also applies to follow-ups and is enforced by the backend. Suggested status queries work without a key. Typed requests use OpenAI to choose a lookup, grounded analysis, or the existing setup drafting pipeline. OpenAI receives the question, relevant chat context, scoped metadata, and selected session-message/outcome excerpts or public agent settings as needed. Credentials, raw private configuration, clipboard evidence, and other researchers’ private chats are excluded from this context.

Answers link to retrieved sessions or saved agents. Analysis reads at most six matching sessions per turn, with bounded message excerpts, and reports sampling or truncation. Narrow the question to inspect a particular participant. Status lists and counts are queried directly from SQLite and refresh when reopened. Generated answers and drafts are saved as dated snapshots in the researcher's chat; deleting a source session does not erase an answer already saved in a separate chat. Uploaded files and their extracted text are not stored separately, though a generated answer can quote an excerpt. The filename and generated result remain in chat history.

**Settings** stores each researcher’s appearance, start page, desktop sidebar, display name, and default chat scope in SQLite. It shows the current study, provider configuration, and API budget. Create the first owner account there to enable username and password sign-in. The owner can add or disable researchers in the shared study workspace. Researchers can change their password, sign out other browsers, and download their own chat history. Use the chat options menu to rename or delete a conversation. Saved conversations show results from current session records when reopened. Existing local chats and settings carry into the owner account.

**Demo agents.** On **Overview**, try **Supplier advisor** and ask “Which supplier best balances cost, reliability, and lead time?” Try **Demand forecaster** and ask “What is the forecast for the next period?” Both open isolated participant previews. They use OpenAI with the pinned model when the server has a key, or built-in responses when no key is configured.

**Try the agent** and **Test this setup** offer **Use live OpenAI for this preview** for public supplier setups with text replies. Live preview uses the saved supplier table and the participant’s stated constraints. Clear the checkbox to preview the saved response settings, including fixed scoring when configured.

A v2-to-v3 startup automatically saves a `data/study.pre-v3-*.sqlite3` backup before adding dataset/action fields. The first v2 startup automatically creates a `data/study.pre-v2-*.sqlite3` backup before migrating a v1 database. Existing transcripts, outcomes, exact configuration JSON, and hashes remain intact. Audit context is backfilled from each session’s original revision. Backups contain research data and should follow the same storage/retention rules as the database.

## Configure and test

1. **Design:** name the study and state the research objective. Expand **Participant task** to edit the brief, public information, data and scoring, or load a case template. Custom tasks support supplier selection and negotiation.
2. **Agent & comparison groups:** set each agent’s goal, instructions, role, and decision authority. Add a no-agent baseline or duplicate a group. Response details, private data and examples remain available in disclosures. Pack agents additionally expose tools, access rules, and execution limits.
3. **Study controls:** choose the provider, model, memory and budget; review assignment, enrollment, consent and interaction tracking.
4. **Check & test:** **Check draft** validates the current configuration on the server and shows its participant-facing brief without creating a session. **Save & test** saves changes before opening the participant preview. Setup tests default to the saved provider; live OpenAI is an explicit optional override where supported. Demo responses do not interpret arbitrary prompts.
5. **Review:** inspect the configuration and research controls, jump to any section to edit, and use the saved setup for the study when ready. [Design rationale and sources](SETUP_DESIGN.md).

6. **Share a session:** select balanced random, random, or an externally chosen condition. The result identifies the assigned condition and configuration version. Real enrollment requires active status; previews are labeled separately.
7. **Recent sessions / Sessions:** open a review page for the conversation, observed exposure/decision timing, activity, outcomes and the original setup. Download individual session JSON or study-wide CSV/JSON for R/Python. No comparison dashboard is required.

Example configurations are in [examples/supplier-study.json](../examples/supplier-study.json), [examples/negotiation-study.json](../examples/negotiation-study.json), and [examples/api-study.json](../examples/api-study.json). Open **Configuration JSON** in the setup’s more-actions menu, apply the JSON, then review and save the version. Do not edit a live database to change a study.

## Negotiating through chat

For the negotiation v2 case, configure the company name, industry/business context, item, quantity, and agent instructions in the setup editor. Participant offers are entered in chat. Company and market context are available beside the conversation; the current offer panel shows the approved unit price, order total, and finish actions.

The server evaluates the participant's recorded price with `evaluate_offer`. A model-supplied price cannot replace it. If the model skips the required evaluation, the server performs it before requesting a grounded reply. Public business arguments can be discussed without exposing private price thresholds. Flagged replies get one rewrite attempt, then a safe response based on the approved offer when possible; the audit records the repair.

In a recommender negotiation, **Accept & finish** or a clear chat reply such as **“Ok I accept”** records the latest viewed quote and completes the session. An explicitly named acceptance price must match that quote. Conditional replies such as “I accept if delivery is free” remain conversation. Acceptance retries with the same request ID do not duplicate the decision. Participants can instead finish without agreement.

The researcher decision summary presents readable unit prices, quantity, and order totals. Negotiation outcomes are simulations; they do not execute a purchase or contract. [Agent behavior and audit details](AGENT_LAYER.md).

## Legacy study task and authority semantics

**Supplier selection:** public normalized weighted scores provide a benchmark. The recommendation condition suggests an option; provisional authority preselects it with human override; chatbot authority produces no structured selection. The participant always submits the final outcome. Numeric output is the policy utility score on a 0–100 scale.

**Negotiation:** only unit price is executable; item and quantity are fixed for the session. A supplier agent faces a buyer participant, and vice versa. GenAI-tool and baseline conditions use the configured participant role. Structured offers use the public price range and server-side private price bounds. An unacceptable counteroffer moves the agent’s previous offer by the configured concession step, without crossing its bounds. An eligible participant offer can be accepted. Participants discuss the scenario and propose unit prices in ordinary chat, for example “I can offer $105 per unit.” The backend extracts explicit offers and distinguishes them from market percentages, quantities, and hypothetical conditions.

Accepting an opposing buyer/supplier agent’s current price records a **simulated agreement**. Recording another price is an override and does not assert counterparty acceptance. Accepting GenAI-tool advice records adoption of advice, not a negotiated agreement. Human-only and chatbot outcomes can record an independent preferred price or finish without agreement. No purchase, contract or external action is executed.

`advice_quality` is retained in condition configuration with no dedicated control. `standard` uses the highest eligible public score for weighted supplier selection; `flawed` uses the lowest eligible public score. It does not override private eligibility and is not a negotiation-quality manipulation. Final outcomes are scored against the unmodified public benchmark. Model-choice conditions are not guaranteed to implement a particular error rate.

## API adapter, model and budget

The provider adapter is in `research_agent/providers.py`. Add **OPENAI_API_KEY** to the project's `.env`, restart the server, select OpenAI, and save a configuration. The server loads `.env` automatically on startup; existing process environment variables take priority. The key is never accepted in study configuration, returned to the browser, or exported.

The initial API preset is **`gpt-4.1-mini-2025-04-14`**, fixed temperature `0`. The dated snapshot and Structured Outputs support are verified in [official OpenAI documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini). API configurations require a dated model ID and numeric temperature. Returned model IDs must match the requested snapshot. Unsupported parameters, model retirement, refusals and malformed responses fail explicitly; there is no silent provider/model fallback. Fixed temperature does not promise identical answers.

The adapter uses Responses with `store: false`, explicit per-session history and an output-token ceiling. Pack agents can invoke registered server tools; legacy studies use the original structured response path. No external actions are enabled. Provider retention policies are separate from that API parameter.

The default **application budget is USD 5** across the database, including all configuration versions and previews. API sessions reserve a configurable allowance at admission (architecture.session_spend_usd, default USD 0.25). Researchers see a warning at 80% spent-or-reserved capacity; new admissions cannot consume existing session allocations. A tool, timeout or spend limit ends the session with a researcher flag and participant return-to-survey instructions. Accounting begins with version 0.2; historical API costs from older installations are not reconstructed. A conservative full-request allowance is reserved atomically before calling the provider. Known usage settles the reservation; failures, missing usage, crashes and uncertain delivery retain it. Concurrent requests cannot reserve past the configured remaining allowance. An optional `SERVER_SPEND_CAP_USD` backend setting provides a lower operational ceiling. A usage overrun blocks subsequent API calls for investigation. The researcher can export the spend ledger.

Accounting defaults are USD 0.40 / 1.60 per million input/output tokens for the pinned preset, verified against [official pricing](https://developers.openai.com/api/docs/pricing) on October 24, 2026. Verify configured prices when changing models or prices. This application ledger is not an account-wide provider billing limit, and it cannot account for unrelated API clients or incorrectly configured prices. It uses a conservative UTF-8 input allowance and never silently truncates a task to fit the configured limit.

No paid live API call was made during development. Adapter, model matching and budget flows were tested with mocked provider responses. A valid server key and a hosted acceptance test are still required for API deployment.

## Legacy demo and private information

**Demo** remains a key-free deterministic test adapter with reproducible scripted templates and policy outcomes. It does not interpret arbitrary prompts like an LLM.

**Private conditions deliberately use controlled server responses**, regardless of the selected provider. Private values and eligibility/reservation bounds are not supplied to a model or manual relay. The policy computes an eligible supplier or approved price; a final output gate checks structured choices, price limits, numeric format, approved private-response sources, and literal private values before persistence/delivery. The single policy-approved offer and already-public attributes are intentionally permitted. The gate also applies to provider, mock and relay replies.

This protects source information; it does not promise zero inference from the sequence of authorized offers or choices. Keep confidential values out of public task fields, objectives, role instructions and examples. Private-template execution is logged and is a protocol difference from free-form API narration; account for it in experimental comparisons.

## Data and memory

Every event row has `participant_id`, `session_id`, `condition_id`, `config_version`, `round_id`, and `model_snapshot`, stamped by the server from the authenticated session. Messages, requests, decisions and provider-spend records carry the same context. Legacy `revision_id` aliases remain in exports. `model_snapshot` is the session’s configured snapshot; per-response metadata identifies actual provider/source/returned model, including mock or private-template execution. Baselines have no model response.

Memory is bounded to the latest 20 stored messages from **this session** for model context; the full transcript stays in research storage. `none` omits conversational history from model input. Negotiation’s public offer state is task state, not cross-participant learning. Nothing carries across participants and no reinforcement-learning updates occur.

Outcomes include the selected supplier or recorded unit price, AI choice/offer, accept/reject/override/independent action, agreement flag, optional reason, and task benchmark. Timing distinguishes browser exposure-to-decision, server receipt timing, provider latency and relay wait. Keystrokes are counts and timing only, never raw keys. By default, copy/paste telemetry records counts and length. [AI use evidence](AI_USE_EVIDENCE.md) explains optional clipboard text capture, the outside AI question, and researcher assessment. Submitted chat text is intentionally part of the transcript.

See [data definitions](DATA_DICTIONARY.md), [protocol decisions](REQUIREMENTS.md), [launch integration](INTEGRATION.md), and [remote deployment](DEPLOYMENT.md). The included Docker/Caddy setup supports a remote HTTPS host; no public service or survey account has been deployed or connected.

## Navigation and verification

Compare/Terms, Split and Conversation views retain their layout in the participant tab. Unsent messages, reasons and choices survive refresh in sessionStorage for that session, and are cleared at completion/withdrawal. **Enter** sends a message and **Shift+Enter** inserts a line break; Command/Ctrl+Enter also sends. The composer grows as you type. New messages animate, the waiting state shows thinking dots, and checked AI replies reveal progressively; reduced-motion preferences are respected. Command/Ctrl+Alt+1/2/3 changes views, and Command/Ctrl+Alt+D reviews the outcome. Researcher Command/Ctrl+K opens quick navigation; Command/Ctrl+S saves configuration while Agent setup is open.

```sh
.venv/bin/python -m unittest discover -s tests -q
node --check static/admin.js
node --check static/participant.js
```

Node.js is optional and is only needed for the JavaScript syntax checks above.

Tests cover local environment setup, legacy migration, immutable snapshots, randomized/balanced assignment, task outcomes, private checks, model contracts, spend reservations, isolation, raw-key rejection, authentication, exports and the full HTTP participant flow. HTTP tests use temporary loopback servers.
