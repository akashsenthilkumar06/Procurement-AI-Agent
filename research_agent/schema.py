"""Additive audit-context migration, with a recoverable database backup before upgrading v1."""
import sqlite3
from contextlib import closing
import time
from pathlib import Path

CONTEXT = {"participant_id":"TEXT", "condition_id":"TEXT", "config_version":"INTEGER", "round_id":"INTEGER NOT NULL DEFAULT 1", "model_snapshot":"TEXT", "dataset_version":"TEXT NOT NULL DEFAULT 'legacy-config'", "temperature":"REAL"}


def backup_legacy(path):
    path = Path(path)
    if not path.exists() or not path.stat().st_size:
        return
    with closing(sqlite3.connect(str(path))) as source:
        columns = {r[1] for r in source.execute("PRAGMA table_info(events)")}
        budget_exists=source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_budgets'").fetchone()
        if columns and ("temperature" not in columns or not budget_exists):
            version='v2' if 'round_id' not in columns else 'v3' if 'dataset_version' not in columns else 'audit' if 'temperature' not in columns else 'safety'
            backup = path.with_name(path.stem+f".pre-{version}-{time.time_ns()}.sqlite3")
            with closing(sqlite3.connect(str(backup))) as target:
                source.backup(target)
            backup.chmod(0o600)


def migrate(db):
    db.execute("BEGIN IMMEDIATE")
    for table in ("sessions", "launches", "messages", "requests", "events", "decisions"):
        columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in CONTEXT.items():
            if name not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    for table, fields in {
        "messages": {"offer_price":"REAL", "decision_action_json":"TEXT"}, "requests": {"offer_price":"REAL"},
        "launches": {"assignment_method":"TEXT NOT NULL DEFAULT 'external'"},
        "decisions": {"task":"TEXT NOT NULL DEFAULT 'supplier_selection'", "final_price":"REAL", "recommended_price":"REAL", "agreement":"INTEGER", "outcome_json":"TEXT NOT NULL DEFAULT '{}'"},
    }.items():
        columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in fields.items():
            if name not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    import json
    for row in db.execute("SELECT s.id,s.revision_id,r.config_json FROM sessions s JOIN revisions r ON r.id=s.revision_id WHERE s.config_version IS NULL OR s.model_snapshot IS NULL").fetchall():
        model = json.loads(row["config_json"])["architecture"]["model"]
        db.execute("UPDATE sessions SET config_version=?,model_snapshot=? WHERE id=?", (row["revision_id"], model, row["id"]))
    for table in ("messages", "requests", "events", "decisions"):
        db.execute(f"""UPDATE {table} SET participant_id=(SELECT participant_id FROM sessions WHERE id={table}.session_id),
            condition_id=(SELECT condition_id FROM sessions WHERE id={table}.session_id),
            config_version=(SELECT config_version FROM sessions WHERE id={table}.session_id),
            model_snapshot=(SELECT model_snapshot FROM sessions WHERE id={table}.session_id)
            WHERE config_version IS NULL OR model_snapshot IS NULL""")
    for row in db.execute("SELECT l.id,l.revision_id,r.config_json FROM launches l JOIN revisions r ON r.id=l.revision_id WHERE l.config_version IS NULL OR l.model_snapshot IS NULL").fetchall():
        db.execute("UPDATE launches SET config_version=?,model_snapshot=? WHERE id=?", (row["revision_id"], json.loads(row["config_json"])["architecture"]["model"], row["id"]))
    # v1's UNIQUE(session_id) would prevent a second round even with a round_id column.
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone()[0]
    if "session_id TEXT UNIQUE" in sql:
        info = db.execute("PRAGMA table_info(decisions)").fetchall()
        declarations = []
        for col in info:
            declaration = col["name"]+" "+col["type"]
            if col["pk"]: declaration += " PRIMARY KEY"
            if col["notnull"]: declaration += " NOT NULL"
            if col["dflt_value"] is not None: declaration += " DEFAULT "+str(col["dflt_value"])
            declarations.append(declaration)
        db.execute("CREATE TABLE decisions_v2 ("+",".join(declarations)+",UNIQUE(session_id,round_id))")
        names = ",".join(c["name"] for c in info)
        db.execute(f"INSERT INTO decisions_v2 ({names}) SELECT {names} FROM decisions")
        db.execute("DROP TABLE decisions")
        db.execute("ALTER TABLE decisions_v2 RENAME TO decisions")
    db.execute("""CREATE TABLE IF NOT EXISTS assignments (participant_id TEXT NOT NULL, config_version INTEGER NOT NULL,
        preview INTEGER NOT NULL, condition_id TEXT NOT NULL, method TEXT NOT NULL, created_at REAL NOT NULL,
        PRIMARY KEY(participant_id,config_version,preview))""")
    db.execute("""CREATE TABLE IF NOT EXISTS provider_spend (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
        request_id TEXT NOT NULL, reserved_micros INTEGER NOT NULL, charged_micros INTEGER, status TEXT NOT NULL,
        created_at REAL NOT NULL, participant_id TEXT NOT NULL, condition_id TEXT NOT NULL,
        config_version INTEGER NOT NULL, round_id INTEGER NOT NULL DEFAULT 1, model_snapshot TEXT NOT NULL,
        UNIQUE(session_id,request_id))""")
    columns={r['name'] for r in db.execute('PRAGMA table_info(provider_spend)')}
    if 'dataset_version' not in columns:db.execute("ALTER TABLE provider_spend ADD COLUMN dataset_version TEXT NOT NULL DEFAULT 'legacy-config'")
    if 'temperature' not in columns:db.execute('ALTER TABLE provider_spend ADD COLUMN temperature REAL')
    # Configured temperature is audit context, including on legacy rows. Null means no API setting.
    for table in ('sessions','launches'):
        rows=db.execute(f"SELECT t.id,r.config_json FROM {table} t JOIN revisions r ON r.id=t.revision_id WHERE t.temperature IS NULL AND t.model_snapshot!='deterministic-v1'").fetchall()
        for row in rows:db.execute(f'UPDATE {table} SET temperature=? WHERE id=?',(json.loads(row['config_json'])['architecture'].get('temperature'),row['id']))
    for table in ('messages','requests','events','decisions','provider_spend'):
        db.execute(f'UPDATE {table} SET temperature=(SELECT temperature FROM sessions WHERE id={table}.session_id) WHERE temperature IS NULL')
    if "revoked_at" not in {r["name"] for r in db.execute("PRAGMA table_info(launches)")}:
        db.execute("ALTER TABLE launches ADD COLUMN revoked_at REAL")
    fields={r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
    for name in ('end_reason','system_message'):
        if name not in fields:db.execute(f'ALTER TABLE sessions ADD COLUMN {name} TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS session_budgets (session_id TEXT PRIMARY KEY,allocation_micros INTEGER NOT NULL,created_at REAL NOT NULL,released_at REAL)')
    db.execute("""CREATE TABLE IF NOT EXISTS ozer_progress (
        session_id TEXT PRIMARY KEY, stage TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ozer_rounds (
        session_id TEXT NOT NULL, round_id INTEGER NOT NULL, signal INTEGER NOT NULL,
        forecast INTEGER, production INTEGER, decision_opened_at REAL, deadline_at REAL,
        agent_started_at REAL, human_submitted_at REAL,
        PRIMARY KEY(session_id,round_id))""")
    revision_fields = {r["name"] for r in db.execute("PRAGMA table_info(revisions)")}
    if "kind" not in revision_fields:
        db.execute("ALTER TABLE revisions ADD COLUMN kind TEXT NOT NULL DEFAULT 'study'")
        db.execute("ALTER TABLE revisions ADD COLUMN source_slot_id INTEGER")
    db.execute("CREATE INDEX IF NOT EXISTS revisions_kind_id ON revisions(kind, id)")
    db.execute("""CREATE TABLE IF NOT EXISTS setup_slots (
        id INTEGER PRIMARY KEY, label TEXT NOT NULL COLLATE NOCASE UNIQUE,
        revision_id INTEGER NOT NULL, updated_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS setup_spend (
        id INTEGER PRIMARY KEY, created_at REAL NOT NULL, reserved_micros INTEGER NOT NULL,
        charged_micros INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS setup_generations (
        id INTEGER PRIMARY KEY, created_at REAL NOT NULL, title TEXT NOT NULL,
        has_draft INTEGER NOT NULL, result_json TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS researcher_accounts (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, password_hash TEXT,
        role TEXT NOT NULL, theme TEXT NOT NULL DEFAULT 'system', disabled INTEGER NOT NULL DEFAULT 0,
        display_name TEXT NOT NULL DEFAULT '', start_page TEXT NOT NULL DEFAULT 'overview',
        sidebar TEXT NOT NULL DEFAULT 'open', chat_scope TEXT NOT NULL DEFAULT 'study')""")
    account_fields = {row["name"] for row in db.execute("PRAGMA table_info(researcher_accounts)")}
    for name, default in (("display_name", ""), ("start_page", "overview"), ("sidebar", "open"), ("chat_scope", "study")):
        if name not in account_fields:
            db.execute(f"ALTER TABLE researcher_accounts ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
    db.execute("INSERT OR IGNORE INTO researcher_accounts(id,role) VALUES(1,'owner')")
    db.execute("""CREATE TABLE IF NOT EXISTS researcher_logins (
        token_hash TEXT PRIMARY KEY, researcher_id INTEGER NOT NULL, expires_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS researcher_chats (
        id TEXT PRIMARY KEY, researcher_id INTEGER NOT NULL, title TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS researcher_chats_owner ON researcher_chats(researcher_id,updated_at DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS researcher_chat_turns (
        id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, request_id TEXT NOT NULL,
        question TEXT NOT NULL, scope TEXT NOT NULL, query_json TEXT NOT NULL,
        UNIQUE(chat_id,request_id))""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS researcher_chat_requests ON researcher_chat_turns(request_id)")
    chat_fields = {row["name"] for row in db.execute("PRAGMA table_info(researcher_chat_turns)")}
    for name, declaration in {"kind":"TEXT NOT NULL DEFAULT 'lookup'", "result_json":"TEXT",
                              "attachment_name":"TEXT NOT NULL DEFAULT ''", "input_hash":"TEXT NOT NULL DEFAULT ''"}.items():
        if name not in chat_fields:
            db.execute(f"ALTER TABLE researcher_chat_turns ADD COLUMN {name} {declaration}")
    db.execute("""CREATE TABLE IF NOT EXISTS session_edits (
        id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, actor_id INTEGER NOT NULL,
        actor_name TEXT NOT NULL, created_at REAL NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('edit','restore')),
        label TEXT NOT NULL, note TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS session_edits_session ON session_edits(session_id,id DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS setup_chat_turns (
        id INTEGER PRIMARY KEY, researcher_id INTEGER NOT NULL, chat_id TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, description TEXT NOT NULL,
        attachment_name TEXT NOT NULL, result_json TEXT NOT NULL, input_hash TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS setup_chat_owner ON setup_chat_turns(researcher_id,chat_id,id)")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_evidence_sessions (
        session_id TEXT PRIMARY KEY, clipboard_consent INTEGER NOT NULL,
        outside_ai_answer TEXT, answered_at REAL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS clipboard_evidence (
        session_id TEXT NOT NULL, id TEXT NOT NULL, round_id INTEGER NOT NULL,
        received_at REAL NOT NULL, client_ms REAL NOT NULL, page_id TEXT NOT NULL,
        direction TEXT NOT NULL, area TEXT NOT NULL, text TEXT NOT NULL, truncated INTEGER NOT NULL,
        PRIMARY KEY(session_id,id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_evidence_reviews (
        id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL,
        created_at REAL NOT NULL, verdict TEXT NOT NULL, note TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS ai_evidence_reviews_session ON ai_evidence_reviews(session_id,id DESC)")
    db.execute("PRAGMA user_version=14")
