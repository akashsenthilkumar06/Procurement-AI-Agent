"""Consented clipboard evidence, self-report, and researcher judgments."""

import json
import time


CLIP_LIMIT = 100
CLIP_CHARACTERS = 2000
CLIPBOARD_NOTICE = (
    "Optional: allow the study to record text you copy from this task or paste into study answers or chat. "
    "Researchers can read up to 2,000 characters per action, for up to 100 actions. "
    "The study cannot read your clipboard outside this page. Leave this unchecked to participate without sharing clipboard text."
)
REPORT_NOTICE = "After the task, you can optionally report whether you used an AI tool outside this study page."
REPORT_ANSWERS = ("yes", "no", "prefer_not")
VERDICTS = ("likely_used", "likely_not_used", "unclear")
PASTE_AREAS = ("prompt", "reason", "task_answer")
COPY_AREAS = (*PASTE_AREAS, "study_task", "agent_reply", "participant_message")


def consent_fields(config):
    telemetry = config["telemetry"]
    return {
        "clipboard_notice": CLIPBOARD_NOTICE if telemetry.get("clipboard_text") else "",
        "outside_ai_notice": REPORT_NOTICE if telemetry.get("outside_ai_question") else "",
    }


def record_clip(db, session, event, round_id, payload):
    direction, area, text, truncated = (payload.get(k) for k in ("direction", "area", "text", "truncated"))
    if (set(payload) != {"direction", "area", "text", "truncated"} or direction not in ("copy", "paste")
            or area not in (PASTE_AREAS if direction == "paste" else COPY_AREAS)
            or not isinstance(text, str) or not text.strip() or len(text) > CLIP_CHARACTERS
            or type(truncated) is not bool):
        raise ValueError("Invalid clipboard evidence")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Invalid clipboard text") from None
    db.execute("""INSERT OR IGNORE INTO clipboard_evidence
        (session_id,id,round_id,received_at,client_ms,page_id,direction,area,text,truncated)
        SELECT ?,?,?,?,?,?,?,?,?,? WHERE
        (SELECT COUNT(*) FROM clipboard_evidence WHERE session_id=?) < ?""",
        (session["id"], event["id"], round_id, time.time(), event["client_ms"], event["page_id"],
         direction, area, text, int(truncated), session["id"], CLIP_LIMIT))


def participant_state(db, session_id):
    row = db.execute("SELECT clipboard_consent,outside_ai_answer FROM ai_evidence_sessions WHERE session_id=?",
                     (session_id,)).fetchone()
    return {"clipboard_capture": bool(row and row["clipboard_consent"]),
            "outside_ai_answer": row["outside_ai_answer"] if row else None}


def self_report(store, session, config, answer):
    if answer not in REPORT_ANSWERS:
        raise ValueError("Choose Yes, No, or Prefer not to say.")
    if not config["telemetry"].get("outside_ai_question"):
        raise PermissionError("This study does not collect an outside AI self-report.")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM sessions WHERE id=?", (session["id"],)).fetchone()
        if not row or row["status"] != "completed":
            raise PermissionError("The question is available after the task is complete.")
        db.execute("""UPDATE ai_evidence_sessions SET outside_ai_answer=?,answered_at=?
            WHERE session_id=? AND outside_ai_answer IS NULL""", (answer, time.time(), session["id"]))
        return {"answer": participant_state(db, session["id"])["outside_ai_answer"]}


def assess(store, account, data):
    session_id, verdict, note = (data.get(k, "") for k in ("session_id", "verdict", "note"))
    if (not isinstance(session_id, str) or not 1 <= len(session_id) <= 100 or verdict not in VERDICTS
            or not isinstance(note, str) or len(note) > 1000):
        raise ValueError("Choose an assessment and enter a note of up to 1,000 characters.")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row or row["status"] == "active":
            raise ValueError("Choose a session that has ended.")
        actor = account["display_name"] or account["username"] or "Local workspace"
        db.execute("""INSERT INTO ai_evidence_reviews(session_id,actor_id,actor_name,created_at,verdict,note)
            VALUES(?,?,?,?,?,?)""", (session_id, account["id"], actor, time.time(), verdict, note.strip()))
    return {"ok": True}


def review(db, session_id, config, messages):
    telemetry = config["telemetry"]
    state = db.execute("SELECT * FROM ai_evidence_sessions WHERE session_id=?", (session_id,)).fetchone()
    clips = [dict(r) for r in db.execute("""SELECT direction,area,text,truncated,round_id,received_at
        FROM clipboard_evidence WHERE session_id=? ORDER BY received_at,rowid""", (session_id,))]
    copied = set()
    for clip in clips:
        clip["matches_study_copy"] = clip["direction"] == "paste" and not clip["truncated"] and clip["text"] in copied
        if clip["direction"] == "copy" and not clip["truncated"]:
            copied.add(clip["text"])
    reviews = [dict(r) for r in db.execute("""SELECT verdict,note,actor_name,created_at
        FROM ai_evidence_reviews WHERE session_id=? ORDER BY id DESC""", (session_id,))]
    replies = [json.loads(m["metadata_json"]) for m in messages if m["role"] == "assistant"]
    ozer_moves = [json.loads(e["payload_json"]) for e in db.execute("""SELECT payload_json FROM events
        WHERE session_id=? AND type='ozer_counterpart_decision'""", (session_id,))]
    return {
        "clipboard_offered": bool(telemetry.get("clipboard_text")),
        "clipboard_consent": bool(state and state["clipboard_consent"]),
        "clipboard": clips, "clipboard_limit": CLIP_LIMIT,
        "outside_ai_question": bool(telemetry.get("outside_ai_question")),
        "outside_ai_answer": state["outside_ai_answer"] if state else None,
        "answered_at": state["answered_at"] if state else None,
        "in_app_outputs": len(replies) + len(ozer_moves),
        "in_app_model_outputs": sum(r.get("response_source") in ("openai", "openai_tool_loop")
                                    and not r.get("safe_fallback") for r in replies)
                                + sum(r.get("provider") == "openai" for r in ozer_moves),
        "reviews": reviews,
    }
