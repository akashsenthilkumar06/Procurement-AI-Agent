# Remote deployment

The included files describe a small single-server pilot with a persistent SQLite volume and Caddy terminating HTTPS. Nothing has been deployed publicly. Domain registration, hosting, secrets, and the actual survey integration remain researcher-controlled choices.

## Environment

Copy `.env.example` to `.env` on the deployment server, generate independent strong credentials, and fill in the domain. Never commit `.env` or participant data. At least 32 characters are required for the remote researcher key. Generate keys in your secret manager or with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.

| Variable | Purpose |
| --- | --- |
| STUDY_HOST | Public domain that resolves to the server; used by Caddy for HTTPS. |
| ADMIN_TOKEN | Strong researcher access key. Remote startup refuses a missing/short value. |
| INTEGRATION_KEY | Separate secret used only by trusted Qualtrics/oTree integration servers. An empty value disables integration issuance. |
| OPENAI_API_KEY | Optional provider key, held only on the backend. Not needed for deterministic or private template conditions. |
| SERVER_SPEND_CAP_USD | Optional server application-budget ceiling; the lower of this and the saved cap is used. The ledger is shared across configuration versions in the database. |
| FRAME_ORIGINS | Space-separated exact HTTPS survey origins; blank permits same-origin frames only. |
| HOST / PORT / DATA_DIR | Defaults in Docker are 0.0.0.0 / 8765 / /app/data. |

## Start the service

From the project folder on your deployment server:

```sh
docker compose up -d --build
```

Only Caddy publishes ports 80 and 443; the agent port remains on the internal Docker network. Visit your domain and enter `ADMIN_TOKEN`. Researcher cookies are Secure and HttpOnly for remote operation. Participant capabilities are bearer tokens, so HTTPS is required outside local development.

The health endpoint confirms process availability. API calls to a configured LLM still require valid provider credentials and a compatible model. The researcher workspace tells you whether a provider key is configured without displaying it. Set enrollment status to active after running hosted previews; do not simply treat a successful health check as study validation.

## Operational limits

- Run one application process against this SQLite database. Back it up with SQLite's online backup API or stop the service and copy the database together with its WAL state. Do not copy a live main database alone and assume it is complete.
- The standard-library threaded HTTP service is a pilot backend, not a hardened high-volume server. Caddy provides TLS and a request-size boundary. For broad recruitment, migrate to a production application server with explicit concurrency limits, shared storage, monitoring, and load tests. Do not expose the Python port directly to the internet.
- Configure host access, storage encryption, backups, retention, and researcher access according to your approved protocol. The application does not encrypt the SQLite database, provide per-researcher accounts, automated deletion, or a participant data-erasure workflow.
- On the first v2 startup, an online SQLite backup named `study.pre-v2-*.sqlite3` is created before migration. Preserve it with the same access controls as the study database. Migrations retain immutable configuration JSON and hashes. Test the upgrade on a copy before a hosted collection window.
- API budget reservations survive restart; uncertain or interrupted charges are not automatically released. Do not delete the ledger to restore budget. Check provider usage and configure an appropriate cap, with provider billing controls as an additional boundary.
- Unfinished model requests are marked failed when the application restarts so a session can retry. Full user prompts remain logged. A retry is a new request with its own timing; do not merge it with the original latency.
- Freeze source/runtime versions for data collection. Save archived source, configurations, engine version, provider model identifiers, and analysis rules. Hosted model drift and retirement need an explicit study contingency.
- Restarting invalidates in-memory researcher cookies; researchers can sign in again. Participant sessions and launch consumption persist in SQLite. Participant access expires 24 hours after session creation.

## Changes during a study

Researchers can edit configurations and save revisions without redeploying. Previously issued links retain their pinned revision, including private policy, knowledge, and consent wording. Link issuance uses the latest saved revision. Closing or drafting the study blocks new non-preview starts even for earlier links; participants who already started may finish.

Changing application code, API adapter behavior, or task policy requires deployment and software version tracking. This distinction is deliberate: a configuration save cannot safely implement an unreviewed training algorithm or arbitrary executable code.


## V3 case-study files

The Docker image includes case_studies/ and datasets/. Retain published version directories when rebuilding; saved configs pin their content hashes, and modifying/deleting them blocks old session execution/review. Publish new directories for changes. The first v3 startup backs up v2 SQLite data before its additive migration. Budget and event storage remain in the persistent data volume. Tools and API steps use bounded subprocesses, so deploy with permission for the Python service to spawn its own interpreter. No external system writes are enabled.
