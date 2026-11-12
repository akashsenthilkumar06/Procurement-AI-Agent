# Agent setup redesign

Implemented November 12, 2026. The goal is to reduce navigation and expose the decisions that define the study, while keeping the existing research controls and saved configuration model.

## Evidence and decisions

- [Langflow agents](https://docs.langflow.org/agents) groups an agent around its model, instructions and tools and provides a Playground for testing. Applied here: goal and instructions are primary fields, supported tools stay with the group, and testing is adjacent to editing.
- [Dify's agent walkthrough](https://www.dify.ai/de/blog/build-your-first-agent-in-dify) combines task instructions, model choice and capabilities with iterative testing. Applied here: direct editing and a save/test loop, rather than mandatory traversal through five steps.
- [Nielsen Norman Group on progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) recommends presenting primary choices first and deferring less frequent complexity. Applied here: three freely accessible sections; advanced response settings, private rules and accounting details expand when needed. The participant-task disclosure names the current task and precedes group configuration.
- [Amershi et al., Guidelines for Human–AI Interaction, CHI 2019](https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf) emphasizes communicating capabilities and limitations and supporting correction. Applied here: show the selected group's authority and response source, distinguish illustrative samples from actual participant tests, expose validation errors, and invalidate checked results when the draft changes.

These are design inputs, not evidence that this particular redesign has been validated with researchers. A usability pilot should measure time to first valid setup, configuration errors, and researchers' ability to explain the difference between demo, live, and private-policy execution.

## Field coverage and persistence

| Section | Inputs | Saved configuration |
| --- | --- | --- |
| Study | Name, research objective; optional separate library label | `name`, `description`; setup-slot `label` |
| Participant task | Task, title, instructions, public information, supplier criteria/data or negotiation terms | `task`, `scenario`, `negotiation` |
| Case template | Versioned task, data and starting groups | `case_study` and pack configuration |
| Agent & groups | Group name, agent enabled, role, decision authority, goal, instructions | `conditions[]` |
| Response details | Prompt approach, response format, selection policy, examples, private information/bounds | Condition fields and `private_rules` |
| Pack tools | Mode, allowed tools, data access, call and timeout limits | Condition `agent` |
| Study controls | Provider, pinned model, memory, generation controls, token and spend limits, knowledge version | `architecture` |
| Participant access | Assignment, enrollment, consent, interaction tracking | `assignment`, `status`, `scenario.consent_text`, `telemetry` |

The research objective describes the study; the per-group agent goal and instructions control its behavior when supported by the selected execution mode. Demo mode uses deterministic responses. Private policies use controlled server responses. Custom scenarios support supplier selection and negotiation; additional task types come from registered packs. This redesign does not add arbitrary tools, task types, or outcome metrics.

## Validation and testing behavior

`POST /api/admin/builder-preview` validates the full draft and returns the allowlisted participant configuration without saving or creating a session. Save uses `/api/admin/setup-slots`, including revision conflict protection. Save & test then launches the saved slot through the existing participant-preview flow. A configured API key is reported as configured, not as a verified connection. A real provider test is still needed to verify model access and prompt behavior.

Illustrative examples are labeled as such. Setup tests use the saved provider by default, and test sessions retain their preview designation. Saving and testing do not activate enrollment; Use for study remains the explicit action that applies the saved configuration to new study links.

Verification: all 110 Python tests passed; JavaScript syntax checked. Browser checks covered creating a custom setup, editing the task and agent instructions, adding and switching a no-agent group, validation feedback, save/reopen, Save & test, and receiving a demo response with the saved participant task. Tests used an isolated database. No paid live-provider call was made.
