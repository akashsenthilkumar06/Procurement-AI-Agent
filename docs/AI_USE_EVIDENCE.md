# AI use evidence

In **Agent setup → Study controls → Interaction tracking**, turn on **Offer optional clipboard text recording** and/or **Ask about outside AI use after the task**. Both are off by default and apply to new sessions launched against that saved revision. Review the study's consent wording. A separate unchecked checkbox lets participants decline clipboard text recording and still participate. The question appears only after completion, and answering is optional. For surveys that automatically advance when the task ends, ask the same question in the survey or leave the completion page visible.

Open **Sessions → [session] → AI use** to see:

- **Study agent outputs** count recorded assistant replies or Ozer counterpart moves. **Model-generated outputs** counts outputs produced by the language model separately from fixed demo or policy responses. A recorded output does not prove that the participant read or relied on it.
- **Clipboard text** shows browser-reported plain text copied from selectable task content or messages, or pasted into chat, task answers or the decision reason. Each record includes direction, area, round and server receipt time. Capture is limited to 2,000 characters per action and 100 actions per session. Excerpts are marked. A complete paste can be matched to identical text copied earlier in the session; all other sources remain unknown. The app does not inspect other tabs, the system clipboard in the background, images, or raw keys.
- **Outside AI self-report** distinguishes Yes, No, Prefer not to say, and no answer. **Researcher judgment** is a separate annotation with the reviewer, time, optional reasoning, and prior judgments. A paste, a tab switch, or a lack of either cannot establish outside AI use. Use Unclear when the evidence is insufficient.

Clipboard records are stored locally with the session and are available in its researcher-only JSON download. They are excluded from the general event export and are not passed to a model for classification. Text that a participant explicitly submits to the study assistant follows the configured model's normal response path. Deleting a session also deletes its clipboard evidence, self-report and assessments.

The browser's [clipboard event API](https://www.w3.org/TR/clipboard-apis/) does not identify the source application of pasted text. [Independent testing of AI text detectors](https://doi.org/10.1007/s40979-023-00146-z) found errors in both directions; the app does not convert writing style or paste activity into an AI probability.
