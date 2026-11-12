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
    return answer_session_question(store, {"query": json.loads(row["query_json"]), "offset": offset})


def _turn(store, row):
    return {"request_id": row["request_id"], "question": row["question"], "scope": row["scope"],
            "result": _result(store, row)}


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
        for row in db.execute("""SELECT t.chat_id,t.question,t.scope,t.query_json FROM researcher_chat_turns t
                                 JOIN researcher_chats c ON c.id=t.chat_id WHERE c.researcher_id=? ORDER BY t.id""",
                              (account_id,)):
            chats[row["chat_id"]]["turns"].append({"question": row["question"], "scope": row["scope"],
                                                  "query": json.loads(row["query_json"])})
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
    question = data.get("question")
    scope = data.get("scope", "study")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000 or scope not in SCOPES:
        raise ValueError("Enter a question of up to 1,000 characters and choose a session scope.")
    question = question.strip()
    with store.db() as db:
        db.execute("BEGIN")
        if chat_id is not None:
            _owned_chat(db, account_id, chat_id)
        prior = db.execute("""SELECT t.* FROM researcher_chat_turns t JOIN researcher_chats c ON c.id=t.chat_id
                             WHERE t.request_id=? AND c.researcher_id=?""", (request_id, account_id)).fetchone()
        if prior is not None:
            if prior["chat_id"] != (chat_id or request_id) or prior["question"] != question or prior["scope"] != scope:
                raise Conflict("This request was already used for another question.")
            return {"chat": _owned_chat(db, account_id, prior["chat_id"]), "turn": _turn(store, prior)}
        latest = db.execute("""SELECT id,scope,query_json FROM researcher_chat_turns
                              WHERE chat_id=? ORDER BY id DESC LIMIT 1""", (chat_id,)).fetchone()
        previous = None
        if latest and latest["scope"] == scope:
            for row in db.execute("SELECT scope,query_json FROM researcher_chat_turns WHERE chat_id=? ORDER BY id DESC", (chat_id,)):
                if row["scope"] != scope:
                    break
                candidate = json.loads(row["query_json"])
                if candidate["view"] != "unsupported":
                    previous = candidate
                    break

    query = data.get("query")
    if query is not None and (not isinstance(query, dict) or query.get("scope") != scope):
        raise ValueError("The suggested query must use the selected session scope.")
    result = answer_session_question(store, {"question": question, "scope": scope, "previous": previous, "query": query})
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
            if existing["question"] != question or existing["scope"] != scope:
                raise Conflict("This request was already used for another question.")
            return {"chat": chat, "turn": _turn(store, existing)}
        last_id = db.execute("SELECT MAX(id) FROM researcher_chat_turns WHERE chat_id=?", (chat_id,)).fetchone()[0]
        if last_id != (latest["id"] if latest else None):
            raise Conflict("This chat received another message. Reopen it before continuing.")
        if db.execute("SELECT 1 FROM researcher_chat_turns WHERE request_id=?", (request_id,)).fetchone():
            raise Conflict("This request was already used for another question.")
        db.execute("""INSERT INTO researcher_chat_turns(chat_id,request_id,question,scope,query_json) VALUES(?,?,?,?,?)""",
                   (chat_id, request_id, question, scope, canonical(result["query"])))
        db.execute("UPDATE researcher_chats SET updated_at=? WHERE id=?", (timestamp, chat_id))
        chat["updated_at"] = timestamp
    return {"chat": chat, "turn": {"request_id": request_id, "question": question, "scope": scope, "result": result}}


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
