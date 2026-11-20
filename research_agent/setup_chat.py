"""Private, saved study drafting conversations."""
import hashlib
import json

from .chat_history import ChatNotFound, _identifier
from .config import canonical
from .setup_assistant import draft_setup
from .store import Conflict, now


def _turn(row):
    return {"description": row["description"], "attachment_name": row["attachment_name"],
            "result": json.loads(row["result_json"])}


def list_chats(store, researcher_id):
    with store.db() as db:
        return [dict(row) for row in db.execute("""
            SELECT t.chat_id AS id,COALESCE(NULLIF(substr(t.description,1,90),''),t.attachment_name) AS title,
                   c.updated_at
            FROM (SELECT MIN(id) AS first_id,MAX(created_at) AS updated_at FROM setup_chat_turns
                  WHERE researcher_id=? GROUP BY chat_id ORDER BY updated_at DESC,first_id DESC LIMIT 50) c
            JOIN setup_chat_turns t ON t.id=c.first_id ORDER BY c.updated_at DESC,c.first_id DESC""", (researcher_id,))]


def get_chat(store, researcher_id, chat_id):
    _identifier(chat_id)
    with store.db() as db:
        turns = db.execute("""SELECT * FROM setup_chat_turns WHERE researcher_id=? AND chat_id=?
                              ORDER BY id""", (researcher_id, chat_id)).fetchall()
    if not turns:
        raise ChatNotFound("Setup conversation not found.")
    return {"id": chat_id, "turns": [_turn(row) for row in turns]}


def _context(rows):
    latest = json.loads(rows[0]["result_json"])
    config = latest["config"]
    return {"earlier_messages": [row["description"][:2000] for row in reversed(rows) if row["description"]],
            "latest_review": latest["review"],
            "latest_draft": None if config is None else {
                "case": config["case_study"], "name": config["name"], "description": config["description"],
                "assignment": config["assignment"]["method"], "memory": config["architecture"]["memory"],
                "provider":config["architecture"]["provider"], "model":config["architecture"]["model"],
                "detailed_interactions": config["telemetry"]["enabled"],
                "conditions": [{key: c[key] for key in ("id", "label", "objective", "prompt")}
                               for c in config["conditions"]]}}


def ask(store, researcher_id, data):
    # Keep the idempotency check, provider call and saved turn in one in-process
    # operation. The spend ledger separately guards dispatch across processes.
    if not store.workspace_chat_lock.acquire(blocking=False):
        raise Conflict("A workspace chat request is still running. Try again shortly.")
    try:
        return _ask(store, researcher_id, data)
    finally:
        store.workspace_chat_lock.release()


def _ask(store, researcher_id, data):
    request_id = _identifier(data.get("request_id"))
    chat_id = data.get("chat_id")
    if chat_id is not None:
        _identifier(chat_id)
    thread = chat_id or request_id
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ValueError("Enter a study description.")
    description = description.strip()
    document = data.get("document")
    signature = hashlib.sha256(canonical([description, document]).encode()).hexdigest()
    with store.db() as db:
        prior = db.execute("SELECT * FROM setup_chat_turns WHERE request_id=?", (request_id,)).fetchone()
        if prior:
            if (prior["researcher_id"], prior["chat_id"], prior["input_hash"]) != (researcher_id, thread, signature):
                raise Conflict("This request was already used for another draft.")
            return {"id": thread, "turn": _turn(prior)}
        previous = db.execute("""SELECT * FROM setup_chat_turns WHERE researcher_id=? AND chat_id=?
                                 ORDER BY id DESC LIMIT 3""", (researcher_id, chat_id)).fetchall()
        if chat_id and not previous:
            raise ChatNotFound("Setup conversation not found.")
    result = draft_setup(store, {"description": description, "document": document},
                         previous=_context(previous) if previous else None, save_history=False)
    name = document["name"][:160] if document else ""
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM researcher_accounts WHERE id=? AND disabled=0", (researcher_id,)).fetchone():
            raise PermissionError("Researcher sign-in is required.")
        prior = db.execute("SELECT * FROM setup_chat_turns WHERE request_id=?", (request_id,)).fetchone()
        if prior:
            if (prior["researcher_id"], prior["chat_id"], prior["input_hash"]) != (researcher_id, thread, signature):
                raise Conflict("This request was already used for another draft.")
            return {"id": thread, "turn": _turn(prior)}
        latest_id = db.execute("SELECT MAX(id) FROM setup_chat_turns WHERE researcher_id=? AND chat_id=?",
                               (researcher_id, thread)).fetchone()[0]
        if latest_id != (previous[0]["id"] if previous else None):
            raise Conflict("This conversation received another message. Reopen it before continuing.")
        db.execute("""INSERT INTO setup_chat_turns(researcher_id,chat_id,request_id,created_at,description,
                      attachment_name,result_json,input_hash) VALUES(?,?,?,?,?,?,?,?)""",
                   (researcher_id, thread, request_id, now(), description, name, canonical(result), signature))
    return {"id": thread, "turn": {"description": description, "attachment_name": name, "result": result}}
