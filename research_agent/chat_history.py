import hashlib
import json
import re
import time

from .config import canonical
from .session_assistant import SCOPES, answer_session_question
from .store import Conflict


class ChatNotFound(LookupError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        raise ValueError("Choose a valid chat or message.")
    return value


def _owned_chat(db, account_id, chat_id):
    row = db.execute("SELECT * FROM researcher_chats WHERE id=? AND researcher_id=?", (chat_id, account_id)).fetchone()
    if row is None:
        raise ChatNotFound("Chat not found.")
    return dict(row)


def _result(store, row, offset=0):
    if row["kind"] != "lookup":
        return json.loads(row["result_json"])
    return {"kind":"lookup", **answer_session_question(store, {"query": json.loads(row["query_json"]), "offset": offset})}


def _turn(store, row):
    return {"request_id": row["request_id"], "question": row["question"], "scope": row["scope"],
            "attachment_name":row["attachment_name"], "result": _result(store, row)}


def list_chats(store, account_id):
    with store.db() as db:
        return [dict(row) for row in db.execute("""SELECT id,title,created_at,updated_at FROM researcher_chats
                         WHERE researcher_id=? ORDER BY updated_at DESC,id""", (account_id,))]


def export_chats(store, account_id):
    with store.db() as db:
        db.execute("BEGIN")
        chats = {row["id"]: {**dict(row), "turns": []} for row in db.execute("""
            SELECT id,title,created_at,updated_at FROM researcher_chats WHERE researcher_id=?
            ORDER BY updated_at DESC,id""", (account_id,))}
        for row in db.execute("""SELECT t.chat_id,t.question,t.scope,t.query_json,t.kind,t.result_json,t.attachment_name FROM researcher_chat_turns t
                                 JOIN researcher_chats c ON c.id=t.chat_id WHERE c.researcher_id=? ORDER BY t.id""",
                              (account_id,)):
            chats[row["chat_id"]]["turns"].append({"question": row["question"], "scope": row["scope"],
                                                  "query": json.loads(row["query_json"]), "kind":row["kind"],
                                                  "attachment_name":row["attachment_name"],
                                                  **({"result":json.loads(row["result_json"])} if row["result_json"] else {})})
    return list(chats.values())


def get_chat(store, account_id, chat_id):
    _identifier(chat_id)
    with store.db() as db:
        db.execute("BEGIN")
        chat = _owned_chat(db, account_id, chat_id)
        rows = db.execute("SELECT * FROM researcher_chat_turns WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()
    return {"chat": chat, "turns": [_turn(store, row) for row in rows]}


def ask(store, account_id, data):
    if not store.workspace_chat_lock.acquire(blocking=False):
        raise Conflict("A workspace chat request is still running. Try again shortly.")
    try:
        return _ask(store, account_id, data)
    finally:
        store.workspace_chat_lock.release()


def _ask(store, account_id, data):
    chat_id = data.get("chat_id")
    if chat_id is not None:
        _identifier(chat_id)
    request_id = _identifier(data.get("request_id"))
    question = data.get("question", "")
    scope = data.get("scope", "study")
    document, query = data.get("document"), data.get("query")
    if not isinstance(question, str) or scope not in SCOPES or len(question.encode("utf-8")) > 16000:
        raise ValueError("Enter a message of up to 16 KB and choose a session scope.")
    question = question.strip()
    if not question:
        if document is None:
            raise ValueError("Enter a message or choose a document.")
        question = "Create an agent setup from this document."
    if query is not None and (document is not None or not isinstance(query, dict) or query.get("scope") != scope):
        raise ValueError("The suggested query must use the selected session scope and no document.")
    signature = hashlib.sha256(canonical([question, scope, document, query]).encode()).hexdigest()
    with store.db() as db:
        db.execute("BEGIN")
        if chat_id is not None:
            _owned_chat(db, account_id, chat_id)
        prior = db.execute("""SELECT t.*,c.researcher_id FROM researcher_chat_turns t JOIN researcher_chats c ON c.id=t.chat_id
                             WHERE t.request_id=?""", (request_id,)).fetchone()
        if prior is not None:
            if (prior["researcher_id"] != account_id or prior["chat_id"] != (chat_id or request_id)
                    or prior["question"] != question or prior["scope"] != scope
                    or (prior["input_hash"] and prior["input_hash"] != signature)
                    or (not prior["input_hash"] and document is not None)):
                raise Conflict("This request was already used for another question.")
            return {"chat": _owned_chat(db, account_id, prior["chat_id"]), "turn": _turn(store, prior)}
        recent = [dict(row) for row in db.execute("SELECT * FROM researcher_chat_turns WHERE chat_id=? ORDER BY id DESC LIMIT 8", (chat_id,))]
        latest = recent[0] if recent else None
        history = []
        for row in recent:
            if row["scope"] != scope:
                break
            history.append(row)
        history.reverse()
    if query is not None:
        result = {"kind":"lookup", **answer_session_question(store, {"query":query})}
    else:
        from .workspace_assistant import answer_workspace_question
        result = answer_workspace_question(store, question, scope, history, document)
    kind = result.get("kind", "lookup")
    attachment_name = document["name"][:160] if document else ""
    timestamp = time.time()
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM researcher_accounts WHERE id=? AND disabled=0", (account_id,)).fetchone():
            raise PermissionError("Researcher sign-in is required.")
        if chat_id is None:
            chat_id = request_id
            db.execute("""INSERT OR IGNORE INTO researcher_chats(id,researcher_id,title,created_at,updated_at)
                          VALUES(?,?,?,?,?)""", (chat_id, account_id, " ".join(question.split())[:100], timestamp, timestamp))
        chat = _owned_chat(db, account_id, chat_id)
        existing = db.execute("SELECT * FROM researcher_chat_turns WHERE chat_id=? AND request_id=?",
                              (chat_id, request_id)).fetchone()
        if existing:
            if existing["question"] != question or existing["scope"] != scope or existing["input_hash"] != signature:
                raise Conflict("This request was already used for another question.")
            return {"chat": chat, "turn": _turn(store, existing)}
        last_id = db.execute("SELECT MAX(id) FROM researcher_chat_turns WHERE chat_id=?", (chat_id,)).fetchone()[0]
        if last_id != (latest["id"] if latest else None):
            raise Conflict("This chat received another message. Reopen it before continuing.")
        if db.execute("SELECT 1 FROM researcher_chat_turns WHERE request_id=?", (request_id,)).fetchone():
            raise Conflict("This request was already used for another question.")
        db.execute("""INSERT INTO researcher_chat_turns(chat_id,request_id,question,scope,query_json,kind,result_json,attachment_name,input_hash)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   (chat_id, request_id, question, scope, canonical(result["query"]), kind,
                    canonical(result) if kind != "lookup" else None, attachment_name, signature))
        db.execute("UPDATE researcher_chats SET updated_at=? WHERE id=?", (timestamp, chat_id))
        chat["updated_at"] = timestamp
    return {"chat": chat, "turn": {"request_id": request_id, "question": question, "scope": scope,
                                    "attachment_name":attachment_name, "result": result}}


def page(store, account_id, chat_id, request_id, offset):
    _identifier(chat_id)
    _identifier(request_id)
    with store.db() as db:
        db.execute("BEGIN")
        _owned_chat(db, account_id, chat_id)
        turn = db.execute("SELECT * FROM researcher_chat_turns WHERE chat_id=? AND request_id=?",
                          (chat_id, request_id)).fetchone()
    if turn is None:
        raise ChatNotFound("Message not found.")
    if turn["kind"] != "lookup":
        raise ValueError("Only session result lists have additional pages.")
    return _result(store, turn, offset)


def rename(store, account_id, chat_id, title):
    _identifier(chat_id)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ValueError("Enter a chat title of up to 100 characters.")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        _owned_chat(db, account_id, chat_id)
        db.execute("UPDATE researcher_chats SET title=? WHERE id=?", (" ".join(title.split()), chat_id))


def delete(store, account_id, chat_id):
    _identifier(chat_id)
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        _owned_chat(db, account_id, chat_id)
        db.execute("DELETE FROM researcher_chat_turns WHERE chat_id=?", (chat_id,))
        db.execute("DELETE FROM researcher_chats WHERE id=?", (chat_id,))
