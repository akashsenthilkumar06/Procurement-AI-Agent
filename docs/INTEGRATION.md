# Qualtrics and oTree integration

A trusted researcher/server issues an opaque one-time launch token. Assignment can be external (e.g. Qualtrics/oTree), random, or balanced random. The backend pins the assigned condition and configuration version before the participant opens the link.

## Launch API

Configure `INTEGRATION_KEY` on the agent server. A trusted backend sends:

```http
POST /api/launch
Content-Type: application/json
X-Research-Request: 1
X-Integration-Key: <server-side integration key>

{"participant_id":"P-0001","condition_id":"recommend"}
```

The response contains `launch_token`, `expires_in_seconds` (900), `revision`, `config_version`, `condition_id`, and `assignment_method`. To request internal assignment, omit `condition_id` and send `assignment_method: "random"` or `"balanced"`. Do not send both a random assignment method and a condition ID. Construct a participant URL on the same agent server:

```text
https://your-study-host.example/participate#launch=<launch_token>
```

Tokens are random capabilities checked against hashed server records. They bind the condition, participant, preview status, and exact revision. They cannot be used twice. There is no participant-facing API that accepts an arbitrary condition ID. The fragment is cleared after opening. The browser requires affirmative consent before starting, and then uses a session capability stored only in sessionStorage.

The study must be active for real launches. Preview links are issued only through the authenticated researcher API or console. The integration API cannot mark a real session as a preview or bypass enrollment state.

## Internal assignment

```json
{"participant_id":"P-0001","assignment_method":"balanced"}
```

Balanced random selects at random among the least-assigned conditions under the pinned configuration version. Assignments and counts are committed atomically. Reissuing for the same participant/version/preview flag preserves the internally assigned condition; it does not consume another allocation. A new version has a new allocation pool. Preview and real assignments are separate.

Balance concerns issued allocations, including unused or expired links, rather than completed sessions. Dropout does not trigger reassignment. The participant cannot select a condition or override identity/version/model context. External assignment is preserved when an explicit condition ID is supplied. Local preview relay cannot issue real launches.

## Qualtrics

1. Configure your survey flow to randomize to the desired condition IDs, such as `recommend` and `delegate`, and supply a pseudonymous study code.
2. Use a trusted server-side bridge or a server-side survey facility available in your installation to call the launch API. Validate the assignment at the bridge; a public unauthenticated relay accepting arbitrary condition IDs defeats assignment integrity.
3. Pass only the resulting launch URL into the participant's survey flow. Embed it in an iframe or use a redirect/link and collect the completion code on return.
4. For embedding, set `FRAME_ORIGINS` to the exact HTTPS origin of your survey installation. Account-specific survey domains must be configured explicitly. Test whether the survey environment itself allows your frame.

Do not put `INTEGRATION_KEY` or `ADMIN_TOKEN` in Qualtrics JavaScript, embedded HTML, a participant-visible query string, or browser source. This repository does not connect to a Qualtrics account or assume a particular license includes a particular server-side feature. Manual preissued links work for a small pilot.

## oTree

Call the launch API from Python server code after your oTree randomization and participant-code assignment, using the request logic in [the launcher example](../examples/issue_launch.py). Keep the integration key in the oTree server environment. Store the returned URL for the appropriate trial, and render it as a link or iframe. Avoid issuing a new token on every template render or reload; persist the token/URL and issue a replacement deliberately if it expires.

This is an integration contract rather than a drop-in oTree app tied to an unselected oTree version. The exact page lifecycle and persistence field belong in your experiment's existing server code.

## Embedding and completion

Example iframe markup, populated with the server-issued URL:

```html
<iframe id="research-agent" title="Supplier decision study"
  src="SERVER_ISSUED_PARTICIPANT_URL" width="100%" height="1000"></iframe>
```

Set `FRAME_ORIGINS="https://your-survey-domain.example"` on the agent server. Its CSP uses exact origins, with no wildcard framing. Default policy allows same-origin embedding only. No third-party cookies are required for the participant interface. Researcher cookies are for direct access to the workspace.

When embedded, the app attempts to notify its immediate parent with:

```json
{"type":"research-agent:complete","session_id":"...","status":"completed","completion_code":"..."}
```

The parent must verify `event.origin` equals the agent's exact HTTPS origin and `event.source` equals its iframe window. Completion notifications do not contain transcripts, private information, or condition IDs. If the parent suppresses referrers, the return code still appears on screen but automatic postMessage may be unavailable.

```js
const frame = document.getElementById("research-agent");
window.addEventListener("message", event => {
  if (event.origin !== "https://your-study-host.example" || event.source !== frame.contentWindow) return;
  if (event.data?.type !== "research-agent:complete") return;
  // Record event.data.completion_code using your platform's supported interface.
});
```

Treat browser completion as a convenience signal. For incentive/payment validation, reconcile the session code and completion status with the protected server-side sessions/decisions dataset. A participant can forge client-side messages. There is no payment or automatic redirect in this pilot.
