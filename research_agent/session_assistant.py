"""Translate researcher questions into read-only queries over session metadata."""

import json
import os

from .budget import reserve_workspace_call, settle_workspace_call
from .config import DEFAULT, PINNED_MODEL, canonical
from .providers import ProviderError, request_openai

SCOPES = ("study", "preview", "all")
STATUSES = ("all", "not_completed", "active", "completed", "withdrawn", "revoked", "ended_system_limit")
GROUP_COLUMNS = {"by_status": "status", "by_condition": "condition_label", "by_study": "study_name"}
VIEWS = ("list", "count", *GROUP_COLUMNS, "unsupported")
PAGE_SIZE = 20
QUERY_FIELDS = {
    "view": {"type": "string", "enum": VIEWS},
    "scope": {"type": "string", "enum": SCOPES},
    "status": {"type": "string", "enum": STATUSES},
    "condition": {"type": "string"},
    "study": {"type": "string"},
    "participant": {"type": "string"},
}


def validate_query(query):
    if not isinstance(query, dict) or query.keys() != QUERY_FIELDS.keys():
        raise ValueError("Invalid session filters.")
    for field, value in query.items():
        choices = QUERY_FIELDS[field].get("enum")
        if not isinstance(value, str) or len(value) > 100 or (choices and value not in choices):
            raise ValueError("Invalid session filters.")
    return {field: value.strip() for field, value in query.items()}


def interpret_question(store, question, scope, previous):
    if not isinstance(question, str) or not question.strip() or len(question.encode("utf-8")) > 4000:
        raise ValueError("Enter a question of up to 1,000 characters or 4 KB.")
    if len(question) > 1000 or scope not in SCOPES:
        raise ValueError("Enter a question of up to 1,000 characters and choose a session scope.")
    if previous is not None:
        previous = validate_query(previous)
    if not os.environ.get("OPENAI_API_KEY"):
        raise ProviderError("Add an OpenAI key to ask a question, or use a suggested query.")
    instructions = (
        "Translate the researcher question into filters for a read-only lookup of recorded study sessions. "
        "You never see database records and must not answer with guesses, produce SQL, or request actions. "
        "Use list for which sessions, count for how many, and by_status for a completion or status breakdown. "
        "Use by_condition or by_study when asked to compare groups or studies. "
        "Map in progress, still open, and unfinished to active. Map not completed to not_completed, which "
        "includes active, withdrawn, revoked and sessions ended by a system limit. "
        "Use the default scope unless the question explicitly requests previews or all sessions including previews. "
        "Use a prior query only when the new question refers back to it. "
        "For a named condition, study, or participant code, copy the exact words from the question or prior filter. "
        "Use empty strings for unused condition, study, and participant filters. "
        "Use unsupported for actions, transcript analysis, unused links, and questions beyond session status, "
        "participant codes, conditions, and study names."
    )
    max_input, max_output = 8192, 400
    body = {
        "model": PINNED_MODEL, "store": False, "temperature": 0, "truncation": "disabled",
        "max_output_tokens": max_output,
        "input": [{"role": "system", "content": instructions},
                  {"role": "user", "content": canonical({
                      "question": question, "default_scope": scope, "previous_query": previous,
                  })}],
        "text": {"format": {"type": "json_schema", "name": "session_lookup", "strict": True,
                            "schema": {"type": "object", "properties": QUERY_FIELDS,
                                       "required": list(QUERY_FIELDS), "additionalProperties": False}}},
    }
    from .engine import input_bound
    if input_bound(body["input"] + [body["text"]]) > max_input:
        raise ValueError("Use a shorter session question.")
    cost_config = {"architecture": {**DEFAULT["architecture"], "max_input_tokens": max_input,
                                    "max_output_tokens": max_output}}
    charge_id = reserve_workspace_call(store, cost_config)
    response = request_openai(body, timeout=30)
    if not isinstance(response, dict):
        raise ProviderError("The session assistant returned an invalid response.")
    settle_workspace_call(store, charge_id, cost_config, response)
    if response.get("status") != "completed" or response.get("model") != PINNED_MODEL:
        raise ProviderError("The session assistant could not interpret the question.")
    try:
        text = "".join(part["text"] for item in response.get("output", []) if item.get("type") == "message"
                       for part in item.get("content", []) if part.get("type") == "output_text")
        return validate_query(json.loads(text))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ProviderError("Try rephrasing your question about the session records.") from None


def answer_session_question(store, data):
    query = data.get("query")
    if query is None:
        query = interpret_question(store, data.get("question"), data.get("scope", "study"), data.get("previous"))
    else:
        query = validate_query(query)
    offset = data.get("offset", 0)
    if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
        raise ValueError("Invalid session results page.")
    view = query["view"]
    result = {"query": query, "total": 0, "rows": [], "groups": [], "next_offset": None}
    if view == "unsupported":
        return result

    filters, args = [], []
    if query["scope"] != "all":
        filters.append("preview = ?")
        args.append(int(query["scope"] == "preview"))
    if query["status"] == "not_completed":
        filters.append("status != 'completed'")
    elif query["status"] != "all":
        filters.append("status = ?")
        args.append(query["status"])
    for key, columns in (("participant", ("participant_id",)), ("study", ("study_name",)),
                         ("condition", ("condition_id", "condition_label"))):
        if query[key]:
            filters.append("(" + " OR ".join(f"instr(lower({column}), lower(?)) > 0" for column in columns) + ")")
            args.extend([query[key]] * len(columns))
    where = " WHERE " + " AND ".join(filters) if filters else ""
    base = """WITH session_rows AS (
        SELECT s.id, s.participant_id, s.condition_id, s.status, s.preview, s.created_at,
            COALESCE(json_extract(r.config_json, '$.name'), 'Unnamed study') AS study_name,
            COALESCE((SELECT json_extract(c.value, '$.label')
                FROM json_each(r.config_json, '$.conditions') AS c
                WHERE json_extract(c.value, '$.id') = s.condition_id), s.condition_id) AS condition_label
        FROM sessions AS s JOIN revisions AS r ON r.id = s.revision_id
    ), matching AS (SELECT * FROM session_rows""" + where + ") "
    with store.db() as db:
        db.execute("BEGIN")
        result["total"] = db.execute(base + "SELECT COUNT(*) FROM matching", args).fetchone()[0]
        page = [*args, PAGE_SIZE + 1, offset]
        if view == "list":
            rows = db.execute(base + """SELECT * FROM matching ORDER BY created_at DESC, id
                                      LIMIT ? OFFSET ?""", page).fetchall()
            result["rows"] = [dict(row) for row in rows[:PAGE_SIZE]]
        elif view in GROUP_COLUMNS:
            column = GROUP_COLUMNS[view]
            rows = db.execute(base + f"""SELECT {column} AS label, COUNT(*) AS count,
                                      SUM(status = 'completed') AS completed
                                      FROM matching GROUP BY {column}
                                      ORDER BY count DESC, label COLLATE NOCASE, label
                                      LIMIT ? OFFSET ?""", page).fetchall()
            result["groups"] = [dict(row) for row in rows[:PAGE_SIZE]]
        else:
            rows = []
    if len(rows) > PAGE_SIZE:
        result["next_offset"] = offset + PAGE_SIZE
    return result
