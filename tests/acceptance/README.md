# Research Agent acceptance harness

From the project folder:

```sh
python3 tests/acceptance/run.py keyless
```

This executes synthetic HTTP sessions, deterministic tools, exact-wire recording mock responses, a files-only pack/data probe, state-isolation checks, and the full regression suite. It writes docs/ACCEPTANCE_REPORT.md and timestamped evidence under tests/acceptance/artifacts/keyless/. Exit 1 is expected while acceptance findings remain FAIL. No study data/config at data/ is read or changed.

Live tests are a separate explicit action. First export OPENAI_API_KEY in your environment, then:

```sh
RESEARCH_AGENT_LIVE_TESTS=1 python3 tests/acceptance/run.py live --max-spend 2
```

Both the environment opt-in and cap flag are required. `--max-spend` alone selects the default USD 2. The ledger is persistent at artifacts/live/budget.sqlite3 and covers cumulative harness spending across live reruns. Do not delete/reset it as a retry strategy. A would-exceed cap stops provider dispatch, exits 3 and marks uncompleted tests NOT RUN. Known usage settles reservations; uncertain/failing calls retain full allowances. The application ceiling depends on the configured pinned-model token rates and does not set an account-wide provider billing limit.

The live command runs 10 fresh-session repeats of one question/condition, then 32 adversarial supplier-negotiation prompt cases (41 turns total, including four multi-turn sequences). It writes docs/ACCEPTANCE_LIVE_REPORT.md, exact adapter request bodies, per-prompt replies/findings and budget evidence. It uses the existing `_agent_step_http` adapter in a killable recorder worker; the normal request builder and response parser are retained. No authorization headers or environment keys are captured. Artifacts can contain synthetic private reservation values.

The recording mock uses the same existing adapter, replacing only its HTTP transport and process boundary. Scripts return raw Responses function-call/message items so input serialization and response parsing are exercised. Capture includes the complete JSON request: system input, tools, tool results, history, model, temperature, structured-output schema and generation limits. Mock tool-choice variance is not live LLM variance.

`secret_requirements.json` is an independently specified authorization expectation: no shipped pack requires a reservation value in model context. Negotiation v2 evaluates offers server-side. Scanners list every secret-value occurrence, including known public-data/schema collisions and participant-supplied values. Unknown secret matches fail. Exact/range/paraphrase detection is bounded, so inspect live replies manually too.

The harness's gate, hard-cap abort, recorder and scanner controls run keylessly as part of unittest discovery. These control tests never constitute a live model PASS. Unexecuted live tests remain NOT RUN.

The safety-fix suite now profiles the same provider/tool code path across all four authority modes, tests safe fallback replacement for 13 disclosure candidates (including an explicitly private-context role fixture), and exercises terminal limits plus session admission reservations/80% warnings. New negotiation configurations use v2; v1 is retained only for immutable history and fails closed if executed. Analytical calls the provider with numeric-only output. Demo remains an explicitly named mock provider across modes.

Architecture.session_spend_usd defaults to USD 0.25. A session receives that bounded allocation at start; unused funds are released on completion, system-limit termination or withdrawal. The global warning counts spent plus reserved capacity, and new admissions cannot consume an existing allocation. The live repeat/leak runner releases test sessions between cases.
