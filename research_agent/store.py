import contextlib
import hashlib
import math
import os
import re
import json
import secrets
import sqlite3
import time
import threading
import uuid
from pathlib import Path

from .config import DEFAULT, canonical, fingerprint, public_config, validate, private_mode, normalize, number
from . import usage_evidence
from .engine import ENGINE_VERSION, rank_options, choose, public_model_input, negotiation_offer
from .privacy import check_output
from .providers import ProviderError
from .schema import backup_legacy, migrate


def now():
    return time.time()


def uid():
    return str(uuid.uuid4())


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.workspace_chat_lock = threading.Lock()
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        backup_legacy(path)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
                    config_hash TEXT NOT NULL, config_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS launches (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, participant_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL, revision_id INTEGER NOT NULL, expires_at REAL NOT NULL,
                    consumed_at REAL, preview INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, participant_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL, revision_id INTEGER NOT NULL, config_hash TEXT NOT NULL,
                    created_at REAL NOT NULL, ended_at REAL, status TEXT NOT NULL, preview INTEGER NOT NULL,
                    consent_version TEXT NOT NULL, consent_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    created_at REAL NOT NULL, option_id TEXT, metadata_json TEXT NOT NULL,
                    request_id TEXT NOT NULL, seen_at REAL, seen_client_ms REAL, page_id TEXT);
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, created_at);
                CREATE TABLE IF NOT EXISTS requests (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(session_id, request_id));
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
                    received_at REAL NOT NULL, client_ms REAL NOT NULL, client_wall_ms REAL NOT NULL,
                    page_id TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_session ON events(session_id, received_at);
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL, message_id TEXT, action TEXT NOT NULL,
                    recommended_option TEXT, final_option TEXT, created_at REAL NOT NULL,
                    client_deliberation_ms REAL, server_since_seen_ms REAL,
                    outcome_score REAL, score_regret REAL, reason TEXT NOT NULL);
            """)
            migrate(db)
            if not db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone():
                c = validate(DEFAULT)
                db.execute("INSERT INTO revisions(created_at,config_hash,config_json) VALUES(?,?,?)", (now(), fingerprint(c), canonical(c)))
            if not db.execute("SELECT 1 FROM setup_slots LIMIT 1").fetchone():
                current = self._study_revision(db)
                db.execute("INSERT INTO setup_slots(label,revision_id,updated_at) VALUES(?,?,?)",
                           ("Current setup", current["id"], now()))
                db.execute("UPDATE revisions SET source_slot_id=last_insert_rowid() WHERE id=?", (current["id"],))

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _study_revision(db):
        return db.execute("SELECT * FROM revisions WHERE kind='study' ORDER BY id DESC LIMIT 1").fetchone()

    @staticmethod
    def _revision_view(row):
        return {"config_version": row["id"], "revision": row["id"], "config_hash": row["config_hash"],
                "source_slot_id": row["source_slot_id"], "config": normalize(json.loads(row["config_json"]))}

    def current(self):
        with self.db() as db:
            return self._revision_view(self._study_revision(db))

    def record_setup_generation(self, result):
        config = result["config"]
        title = config["name"] if config else "No matching case"
        with self.db() as db:
            db.execute("""INSERT INTO setup_generations(created_at,title,has_draft,result_json)
                          VALUES(?,?,?,?)""", (now(), title, config is not None, canonical(result)))

    def setup_generations(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("""SELECT id,created_at,title,has_draft
                         FROM setup_generations ORDER BY id DESC LIMIT 50""")]

    def setup_generation(self, generation_id):
        with self.db() as db:
            row = db.execute("SELECT result_json FROM setup_generations WHERE id=?", (generation_id,)).fetchone()
            return json.loads(row["result_json"]) if row else None

    def save(self, config, expected_revision):
        config = validate(config)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._study_revision(db)["id"] != expected_revision:
                raise Conflict("Another researcher saved changes. Reload before saving.")
            cursor = db.execute("INSERT INTO revisions(created_at,config_hash,config_json) VALUES(?,?,?)",
                                (now(), fingerprint(config), canonical(config)))
            return self._revision_view(db.execute("SELECT * FROM revisions WHERE id=?", (cursor.lastrowid,)).fetchone())

    @staticmethod
    def _slot_revision(db, slot_id):
        row = db.execute("""SELECT r.*, s.id AS slot_id, s.label, s.updated_at
            FROM setup_slots s JOIN revisions r ON r.id=s.revision_id WHERE s.id=?""", (slot_id,)).fetchone()
        if row is None:
            raise ValueError("That save slot does not exist.")
        return row

    def _slot_view(self, row):
        return {**self._revision_view(row), "id": row["slot_id"], "label": row["label"],
                "updated_at": row["updated_at"]}

    def setup_slots(self):
        with self.db() as db:
            rows = db.execute("""SELECT r.*, s.id AS slot_id, s.label, s.updated_at
                FROM setup_slots s JOIN revisions r ON r.id=s.revision_id ORDER BY s.id""").fetchall()
        slots = []
        for row in rows:
            config = normalize(json.loads(row["config_json"]))
            slots.append({
                "id": row["slot_id"], "label": row["label"], "revision": row["id"],
                "config_hash": row["config_hash"], "updated_at": row["updated_at"],
                "model": config["architecture"]["model"], "provider": config["architecture"]["provider"],
                "case_study": config["case_study"], "task": config["task"],
                "study_name": config["name"], "condition_count": len(config["conditions"]), "status": config["status"],
            })
        return slots

    def setup_slot(self, slot_id):
        with self.db() as db:
            return self._slot_view(self._slot_revision(db, slot_id))

    def save_setup_slot(self, label, config, slot_id=None, expected_revision=None):
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80 or any(ord(c) < 32 for c in label):
            raise ValueError("Enter a save name between 1 and 80 characters.")
        label = label.strip()
        config = validate(config)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if slot_id is not None and self._slot_revision(db, slot_id)["id"] != expected_revision:
                raise Conflict("This save changed in another tab. Reload before saving.")
            duplicate = db.execute("SELECT id FROM setup_slots WHERE label=?", (label,)).fetchone()
            if duplicate and duplicate["id"] != slot_id:
                raise Conflict("A slot already has that name. Choose another name.")
            timestamp = now()
            if slot_id is None:
                # Keep historical references distinct after a setup is deleted.
                slot_id = db.execute("""SELECT MAX(
                    COALESCE((SELECT MAX(id) FROM setup_slots), 0),
                    COALESCE((SELECT MAX(source_slot_id) FROM revisions), 0)
                ) + 1""").fetchone()[0]
                db.execute("INSERT INTO setup_slots(id,label,revision_id,updated_at) VALUES(?,?,?,?)",
                           (slot_id, label, self._study_revision(db)["id"], timestamp))
            revision = db.execute("""INSERT INTO revisions(created_at,config_hash,config_json,kind,source_slot_id)
                VALUES(?,?,?,'slot',?)""", (timestamp, fingerprint(config), canonical(config), slot_id)).lastrowid
            db.execute("UPDATE setup_slots SET label=?,revision_id=?,updated_at=? WHERE id=?",
                       (label, revision, timestamp, slot_id))
            return self._slot_view(self._slot_revision(db, slot_id))

    def delete_setup_slot(self, slot_id, expected_revision):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            slot = self._slot_revision(db, slot_id)
            if slot["id"] != expected_revision:
                raise Conflict("This setup changed in another tab. Reload before deleting.")
            if self._study_revision(db)["source_slot_id"] == slot["slot_id"]:
                raise Conflict("Choose another setup for the study before deleting this one.")
            if db.execute("SELECT COUNT(*) FROM setup_slots").fetchone()[0] <= 1:
                raise Conflict("Save another setup before deleting the last one.")
            db.execute("DELETE FROM setup_slots WHERE id=?", (slot["slot_id"],))

    def use_setup_slot(self, slot_id, expected_revision, expected_study_revision):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._study_revision(db)["id"] != expected_study_revision:
                raise Conflict("The study setup changed in another tab. Reload before switching.")
            slot = self._slot_revision(db, slot_id)
            if slot["id"] != expected_revision:
                raise Conflict("This save changed in another tab. Reload before switching.")
            validate(json.loads(slot["config_json"]))
            revision = db.execute("""INSERT INTO revisions(created_at,config_hash,config_json,source_slot_id)
                VALUES(?,?,?,?)""", (now(), slot["config_hash"], slot["config_json"], slot_id)).lastrowid
            return self._revision_view(db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone())

    @staticmethod
    def _preview_revision(db, config, kind, source_slot_id=None):
        digest = fingerprint(config)
        row = db.execute("""SELECT * FROM revisions
            WHERE kind=? AND config_hash=? AND source_slot_id IS ? LIMIT 1""",
                         (kind, digest, source_slot_id)).fetchone()
        if row is None:
            revision = db.execute("""INSERT INTO revisions(created_at,config_hash,config_json,kind,source_slot_id)
                VALUES(?,?,?,?,?)""", (now(), digest, canonical(config), kind, source_slot_id)).lastrowid
            row = db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
        return row

    def issue_launch(self, participant_id, condition_id=None, preview=False, assignment_method=None, *,
                     slot_id=None, expected_slot_revision=None, allow_relay=True, demo_agent=None, live_preview=False):
        if not isinstance(participant_id, str) or not 1 <= len(participant_id) <= 100 or any(ord(c)<32 for c in participant_id):
            raise ValueError("A pseudonymous participant ID of 1–100 printable characters is required")
        if type(live_preview) is not bool:
            raise ValueError("Live preview must be a boolean.")
        if live_preview:
            if preview is not True or demo_agent is not None or not condition_id or assignment_method not in (None, "external"):
                raise ValueError("Live preview requires an explicitly selected group in a study preview.")
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("Configure the server's OpenAI API key before starting a live preview.")
        demo = None
        if demo_agent is not None:
            if preview is not True or slot_id is not None or condition_id is not None or assignment_method not in (None, "external"):
                raise ValueError("Demo agents can only launch a standalone preview.")
            from .packs import demo_config
            demo = demo_config(demo_agent, bool(os.environ.get("OPENAI_API_KEY")))
            condition_id = demo["conditions"][0]["id"]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if demo is not None:
                if demo["architecture"]["provider"] == "openai":
                    study = self._revision_view(self._study_revision(db))["config"]
                    cap = min(demo["architecture"]["spend_cap_usd"], study["architecture"]["spend_cap_usd"])
                    if cap <= 0:
                        raise ValueError("Set a positive study spending limit before running an OpenAI demo.")
                    demo["architecture"]["spend_cap_usd"] = cap
                    demo["architecture"]["session_spend_usd"] = min(demo["architecture"]["session_spend_usd"], cap)
                row = self._preview_revision(db, demo, "demo")
            elif slot_id is not None:
                if preview is not True:
                    raise PermissionError("Save slots can only be used directly for previews.")
                row = self._slot_revision(db, slot_id)
                if row["id"] != expected_slot_revision:
                    raise Conflict("This save changed in another tab. Reload before previewing.")
            else:
                row = self._study_revision(db)
            config = normalize(json.loads(row["config_json"]))
            ozer = (config.get("case_study") or {}).get("id") == "ozer_game"
            if live_preview:
                if ozer:
                    if config["architecture"]["provider"] != "openai":
                        raise ValueError("Save this Ozer setup with OpenAI before running a live preview.")
                    config = validate(config)
                else:
                    from .config import live_supplier_preview
                    config = live_supplier_preview(config, condition_id)
                row = self._preview_revision(db, config, "preview", row["source_slot_id"])
            if not allow_relay and config["architecture"]["provider"] == "relay":
                raise PermissionError("This-chat relay requires a local preview server")
            if config['case_study']:
                from .packs import resolve
                pack, _ = resolve(config['case_study'])
            if not preview and config["status"] != "active":
                raise ValueError("Activate the study before issuing participant links")
            if not preview and ozer:
                if config["architecture"]["provider"] != "openai" or not os.environ.get("OPENAI_API_KEY"):
                    raise ValueError("Ozer enrollment requires a configured OpenAI provider. Use a preview to play without a key.")
                from .ozer import validate_conditions
                validate_conditions(config, pack)
            if config["architecture"]["provider"] == "relay" and not preview:
                raise ValueError("This-chat relay is for local previews only")
            method = assignment_method or ("external" if condition_id else config["assignment"]["method"])
            if method not in ("external", "random", "balanced"):
                raise ValueError("Invalid assignment method")
            ids = [c["id"] for c in config["conditions"]]
            if method == "external":
                if condition_id not in ids:
                    raise ValueError("Choose an assigned condition")
            else:
                if condition_id is not None:
                    raise ValueError("Do not supply a condition with randomized assignment")
                assigned = db.execute("SELECT condition_id FROM assignments WHERE participant_id=? AND config_version=? AND preview=?", (participant_id,row["id"],int(preview))).fetchone()
                if assigned:
                    condition_id = assigned["condition_id"]
                else:
                    candidates = ids
                    if method == "balanced":
                        counts = {c:0 for c in ids}
                        for entry in db.execute("SELECT condition_id,COUNT(*) AS n FROM assignments WHERE config_version=? AND preview=? GROUP BY condition_id", (row["id"],int(preview))):
                            if entry["condition_id"] in counts: counts[entry["condition_id"]] = entry["n"]
                        candidates = [key for key,value in counts.items() if value == min(counts.values())]
                    condition_id = secrets.choice(candidates)
                    db.execute("INSERT INTO assignments VALUES(?,?,?,?,?,?)", (participant_id,row["id"],int(preview),condition_id,method,now()))
            token = secrets.token_urlsafe(32)
            demo_ozer = ozer and preview and not live_preview
            model = "deterministic-v1" if demo_ozer else config["architecture"]["model"]
            temperature = None if demo_ozer else config["architecture"]["temperature"]
            db.execute("""INSERT INTO launches (id,token_hash,participant_id,condition_id,revision_id,expires_at,preview,config_version,round_id,model_snapshot,assignment_method)
                VALUES(?,?,?,?,?,?,?,?,1,?,?)""", (uid(),token_hash(token),participant_id,condition_id,row["id"],now()+900,int(preview),row["id"],model,method))
            if config['case_study']:db.execute('UPDATE launches SET dataset_version=? WHERE token_hash=?',(config['case_study']['dataset_version'],token_hash(token)))
            db.execute('UPDATE launches SET temperature=? WHERE token_hash=?',(temperature,token_hash(token)))
        return {"launch_token":token, "expires_in_seconds":900, "revision":row["id"], "config_version":row["id"], "condition_id":condition_id, "assignment_method":method}

    def pending_launches(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("""
                SELECT id, participant_id, condition_id, expires_at, preview
                FROM launches WHERE expires_at > ? AND consumed_at IS NULL AND revoked_at IS NULL
                ORDER BY expires_at DESC, id
            """, (now(),))]

    def revoke_launch(self, launch_id):
        timestamp = now()
        with self.db() as db:
            changed = db.execute("""
                UPDATE launches SET revoked_at = ?
                WHERE id = ? AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at > ?
            """, (timestamp, launch_id, timestamp)).rowcount
            if not changed:
                raise Conflict("This link is no longer waiting to be used. Refresh Sessions for its latest status.")

    def preview(self, launch_token):
        with self.db() as db:
            row = db.execute("SELECT l.*,r.config_json,r.kind AS revision_kind FROM launches l JOIN revisions r ON r.id=l.revision_id WHERE token_hash=?", (token_hash(launch_token),)).fetchone()
            if not row or row["expires_at"] < now() or row["consumed_at"] or row["revoked_at"]:
                raise ValueError("This launch link has expired or was already used. Ask the researcher for a new link.")
            config = normalize(json.loads(row["config_json"]))
            if not row["preview"] and self.current()["config"]["status"] != "active":
                raise ValueError("This study is not accepting new sessions")
            condition = next(c for c in config["conditions"] if c["id"] == row["condition_id"])
            public = public_config(config, condition)
            if row["preview"] and row["revision_kind"] != "preview" and (config.get("case_study") or {}).get("id") == "ozer_game":
                public["provider_mode"] = "demo"
            return {"round_id": row["round_id"], "public": public, "preview": bool(row["preview"])}

    def start(self, launch_token, consent, clipboard_consent=False):
        if consent is not True:
            raise ValueError("Consent is required before a session can start")
        if type(clipboard_consent) is not bool:
            raise ValueError("Clipboard consent must be a boolean.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT l.*,r.config_json,r.config_hash,r.kind AS revision_kind FROM launches l JOIN revisions r ON r.id=l.revision_id WHERE token_hash=?", (token_hash(launch_token),)).fetchone()
            if not row or row["expires_at"] < now() or row["consumed_at"] or row["revoked_at"]:
                raise ValueError("Launch link expired or already used")
            current = json.loads(self._study_revision(db)["config_json"])
            if not row["preview"] and current["status"] != "active":
                raise ValueError("This study is not accepting new sessions")
            config = normalize(json.loads(row["config_json"]))
            condition=next(c for c in config['conditions'] if c['id']==row['condition_id'])
            consent_version = config["scenario"]["consent_text"]
            collection = usage_evidence.consent_fields(config)
            if clipboard_consent and not collection["clipboard_notice"]:
                raise ValueError("Clipboard text collection is not offered for this session.")
            if any(collection.values()):
                consent_version = {"consent_text": public_config(config, condition)["scenario"]["consent_text"],
                                   **collection, "clipboard_consent": clipboard_consent}
            session_id, token, ts = uid(), secrets.token_urlsafe(32), now()
            db.execute("""INSERT INTO sessions (id,token_hash,participant_id,condition_id,revision_id,config_hash,created_at,status,preview,consent_version,consent_at,config_version,round_id,model_snapshot)
                VALUES(?,?,?,?,?,?,?,'active',?,?,?,?,1,?)""", (session_id,token_hash(token),row["participant_id"],row["condition_id"],row["revision_id"],row["config_hash"],ts,row["preview"],fingerprint(consent_version),ts,row["revision_id"],config["architecture"]["model"]))
            if any(collection.values()):
                db.execute("INSERT INTO ai_evidence_sessions(session_id,clipboard_consent) VALUES(?,?)", (session_id, int(clipboard_consent)))
            db.execute("UPDATE launches SET consumed_at=? WHERE id=?", (ts, row["id"]))
            dataset=config['case_study']['dataset_version'] if config['case_study'] else 'legacy-config'
            db.execute('UPDATE sessions SET dataset_version=?,temperature=? WHERE id=?',(dataset,config['architecture']['temperature'],session_id))
            ozer = (config.get("case_study") or {}).get("id") == "ozer_game"
            demo_ozer = ozer and row["preview"] and row["revision_kind"] != "preview"
            if ozer:
                db.execute("INSERT INTO ozer_progress (session_id,stage) VALUES (?,'general')", (session_id,))
                if demo_ozer:
                    db.execute("UPDATE sessions SET model_snapshot='deterministic-v1',temperature=NULL WHERE id=?", (session_id,))
                elif config["architecture"]["provider"] != "openai" or not os.environ.get("OPENAI_API_KEY"):
                    raise ValueError("Live Ozer sessions require a configured OpenAI provider.")
            session=dict(db.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone())
            if config['architecture']['provider']=='openai' and condition['agent_enabled'] and not demo_ozer:
                minimum_steps = 1
                if ozer:
                    from .packs import resolve
                    pack, _ = resolve(config["case_study"])
                    minimum_steps = pack["game"]["practice_rounds"] + pack["game"]["paid_rounds"]
                self.reserve_session(db, session, config, minimum_steps=minimum_steps)
            state=db.execute('SELECT status FROM sessions WHERE id=?',(session_id,)).fetchone()[0]
        return {"session_token": token, "session_id": session_id,'status':state,'admitted':state=='active'}

    def authenticate(self, token):
        with self.db() as db:
            row = db.execute("SELECT s.*,r.config_json FROM sessions s JOIN revisions r ON r.id=s.revision_id WHERE s.token_hash=?", (token_hash(token),)).fetchone()
        if not row:
            raise PermissionError("Invalid session token")
        if now() - row["created_at"] > 24 * 3600:
            raise PermissionError("Session access expired after 24 hours. Contact the researcher.")
        session = dict(row)
        config = normalize(json.loads(session.pop("config_json")))
        condition = next(c for c in config["conditions"] if c["id"] == session["condition_id"])
        return session, config, condition

    def transcript(self, session_id):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at,id", (session_id,))]

    def session_view(self, session, config, condition):
        with self.db() as db:
            state = db.execute("SELECT status,end_reason,system_message FROM sessions WHERE id=?", (session["id"],)).fetchone()
            if not state:
                raise PermissionError("Invalid session token")
            status = state["status"]
            decision = db.execute("SELECT action,final_option,final_price,agreement,outcome_json FROM decisions WHERE session_id=? AND round_id=?", (session["id"],session["round_id"])).fetchone()
            pending = db.execute("SELECT request_id FROM requests WHERE session_id=? AND status='pending'", (session["id"],)).fetchone()
            evidence_state = usage_evidence.participant_state(db, session["id"])
        return {"session_id": session["id"], "status": status, "preview": bool(session["preview"]),
                'end_reason':state['end_reason'],'system_message':state['system_message'],'next_action':'return_to_survey' if status in ('ended_system_limit', 'revoked') else None,
                "round_id": session["round_id"], "public": public_config(config, condition), "decision": dict(decision) if decision else None,
                "usage_evidence": evidence_state,
                "pending_request": dict(pending) if pending and config["architecture"]["provider"] == "relay" else None,
                "messages": [{**{k: m[k] for k in ("id", "role", "content", "created_at", "option_id", "offer_price")},'decision_action':json.loads(m['decision_action_json']) if m['decision_action_json'] else None,'safe_fallback':bool(json.loads(m['metadata_json']).get('safe_fallback'))} for m in (self.transcript(session["id"]) if status != "revoked" else [])]}

    def begin_request(self, session_id, request_id, content, allow_pending=False, offer_price=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()[0] != "active":
                raise ValueError("Session is already finished")
            req = db.execute("SELECT status FROM requests WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if req:
                if req["status"] == "completed" or (allow_pending and req["status"] == "pending"):
                    old = db.execute("SELECT content,offer_price FROM messages WHERE session_id=? AND request_id=? AND role='user'", (session_id, request_id)).fetchone()
                    if old["content"] != content or old["offer_price"] != offer_price:
                        raise Conflict("Request ID was already used for another message")
                    return False
                raise Conflict("This request is pending or failed. Use a new request ID to retry.")
            if db.execute("SELECT 1 FROM requests WHERE session_id=? AND status='pending'", (session_id,)).fetchone():
                raise Conflict("A response is already in progress")
            count = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND role='user'", (session_id,)).fetchone()[0]
            if count >= 100:
                raise ValueError("Session prompt limit reached")
            row = db.execute("SELECT s.*,r.config_json FROM sessions s JOIN revisions r ON r.id=s.revision_id WHERE s.id=?", (session_id,)).fetchone()
            cfg = normalize(json.loads(row["config_json"]))
            cond = next(c for c in cfg["conditions"] if c["id"]==row["condition_id"])
            if not cond["agent_enabled"]:
                raise ValueError("The agent is off in this human-only condition")
            if offer_price is not None:
                n = cfg["negotiation"]
                if cfg["task"] != "negotiation" or not number(offer_price) or not n["price_min"] <= offer_price <= n["price_max"] or abs(offer_price*100-round(offer_price*100))>1e-6:
                    raise ValueError("Offer must be a price inside the public range with at most two decimals")
            ctx = (row["participant_id"],row["condition_id"],row["config_version"],row["round_id"],row["model_snapshot"])
            db.execute("INSERT INTO requests (session_id,request_id,status,offer_price,participant_id,condition_id,config_version,round_id,model_snapshot) VALUES(?,?,'pending',?,?,?,?,?,?)", (session_id,request_id,offer_price,*ctx))
            db.execute("INSERT INTO messages (id,session_id,role,content,created_at,metadata_json,request_id,offer_price,participant_id,condition_id,config_version,round_id,model_snapshot) VALUES(?,?,'user',?,?,'{}',?,?,?,?,?,?,?)", (uid(),session_id,content,now(),request_id,offer_price,*ctx))
            for table in ('messages','requests'):
                db.execute(f'UPDATE {table} SET dataset_version=?,temperature=? WHERE session_id=? AND request_id=?',(row['dataset_version'],row['temperature'],session_id,request_id))
        return True

    def _finish_request(self, db, session_id, request_id, response):
        row = db.execute("SELECT s.*,r.config_json FROM sessions s JOIN revisions r ON r.id=s.revision_id WHERE s.id=?", (session_id,)).fetchone()
        req = db.execute("SELECT status FROM requests WHERE session_id=? AND request_id=?", (session_id,request_id)).fetchone()
        if not row or row["status"] != "active" or not req or req["status"] != "pending":
            raise Conflict("The response is no longer pending in an active session")
        cfg = normalize(json.loads(row["config_json"]))
        cond = next(c for c in cfg["conditions"] if c["id"]==row["condition_id"])
        if cfg['case_study']:
            from .agent import check_agent_output
            check_agent_output(cfg,cond,response)
        else:check_output(cfg, cond, response)
        ts, mid = now(), uid()
        ctx = (row["participant_id"],row["condition_id"],row["config_version"],row["round_id"],row["model_snapshot"])
        db.execute("""INSERT INTO messages (id,session_id,role,content,created_at,option_id,metadata_json,request_id,offer_price,participant_id,condition_id,config_version,round_id,model_snapshot)
            VALUES(?,?,'assistant',?,?,?,?,?,?,?,?,?,?,?)""", (mid,session_id,response["content"],ts,response["option_id"],canonical(response["metadata"]),request_id,response.get("offer_price"),*ctx))
        db.execute("UPDATE requests SET status='completed' WHERE session_id=? AND request_id=?", (session_id,request_id))
        db.execute('UPDATE messages SET dataset_version=?,temperature=?,decision_action_json=? WHERE id=?',(row['dataset_version'],row['temperature'],canonical(response.get('decision_action')) if response.get('decision_action') is not None else None,mid))
        result={"id":mid,"role":"assistant","content":response["content"],"option_id":response["option_id"],"offer_price":response.get("offer_price"),"created_at":ts,'decision_action':response.get('decision_action'),'safe_fallback':bool(response['metadata'].get('safe_fallback'))}
        if response['metadata'].get('safe_fallback'):
            self.server_event(dict(row),'output_replaced',{'message_id':mid,'request_id':request_id,'flags':response['metadata'].get('output_flags',[]),'safe_fallback':True},db)
        if cfg['case_study'] and cond['agent']['mode']=='autonomous' and response.get('decision_action') is not None:
            result['committed_decision']=self._pack_outcome(db,dict(row),cfg,cond,'autonomous',response['decision_action'],mid,None,None,'',ts)
        return result

    def finish_request(self, session_id, request_id, response):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._finish_request(db,session_id,request_id,response)

    def reserve_session(self,db,session,config,minimum_steps=1):
        from .budget import cap_micros,step_allowance,accounting
        cap=cap_micros(config);amount=min(math.floor(config['architecture']['session_spend_usd']*1_000_000),cap)
        used=accounting(db,now())['allocated_micros']
        if amount<minimum_steps*step_allowance(config) or used+amount>cap or db.execute("SELECT 1 FROM provider_spend WHERE status='overrun'").fetchone():
            self._end_limit(db,session,'global_budget_admission','The study cannot start another AI session within its spend cap. Return to your survey to continue.')
            return False
        db.execute('INSERT INTO session_budgets VALUES(?,?,?,NULL)',(session['id'],amount,now()))
        self.server_event(session,'budget_reserved',{'allocation_micros':amount,'state':'active'},db)
        if used+amount>=.8*cap:
            self.server_event(session,'budget_warning',{'threshold':.8,'allocated_micros':used+amount,'cap_micros':cap,'message':'At least 80% of the global allowance is spent or reserved.'},db)
        return True

    def _release_session(self,db,session_id):
        db.execute('UPDATE session_budgets SET released_at=COALESCE(released_at,?) WHERE session_id=?',(now(),session_id))

    def _end_limit(self,db,session,reason,message):
        row=db.execute('SELECT status FROM sessions WHERE id=?',(session['id'],)).fetchone()
        if row and row['status']=='active':
            db.execute("UPDATE sessions SET status='ended_system_limit',end_reason=?,system_message=?,ended_at=? WHERE id=?",(reason,message,now(),session['id']))
            db.execute("UPDATE requests SET status='failed' WHERE session_id=? AND status='pending'",(session['id'],))
            self._release_session(db,session['id'])
            self.server_event(session,'session_ended',{'state':'ended_system_limit','reason':reason,'message':message,'next_action':'return_to_survey','researcher_flag':True},db)
        return {'status':'ended_system_limit','system_message':message,'end_reason':reason,'next_action':'return_to_survey','completion_code':session['id']}

    def end_system_limit(self,session,reason,message):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session["id"],)).fetchone()
            if not row:
                raise PermissionError("Invalid session token")
            if row["status"] == "revoked":
                return self.session_status(dict(row))
            return self._end_limit(db,session,reason,message+' Return to your survey to continue.')

    def server_event(self,session,event_type,payload,db=None):
        if db is None:
            with self.db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT status FROM sessions WHERE id=?", (session["id"],)).fetchone()
                if row and row["status"] != "revoked":
                    self.server_event(session, event_type, payload, connection)
                return
        timestamp=now()
        db.execute('INSERT INTO events (id,session_id,type,received_at,client_ms,client_wall_ms,page_id,payload_json,participant_id,condition_id,config_version,round_id,model_snapshot,dataset_version,temperature) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (uid(),session['id'],event_type,timestamp,0,timestamp*1000,'server',canonical(payload),session['participant_id'],session['condition_id'],session['config_version'],session['round_id'],session['model_snapshot'],session['dataset_version'],session.get('temperature')))

    def _pack_outcome(self,db,session,config,condition,action,decision,mid,elapsed,since_seen,reason,ts):
        from .packs import score_action,resolve
        from .tools import run_bounded,REGISTRY
        from .tools import MODE_SCOPES
        if action=='autonomous' and (not condition['agent_enabled'] or 'decision:commit' not in MODE_SCOPES[condition['agent']['mode']]):raise PermissionError('Condition cannot commit an agent decision')
        score=None;metric={}
        def execute(name,inputs):
            begin=time.perf_counter();output=None;error=None
            try:
                output=run_bounded(name,inputs,config['case_study'],condition,condition['agent']['tool_timeout_seconds']);return output
            except Exception:
                error='Scoring tool failed';raise
            finally:self.server_event(session,'tool_call',{'tool_name':name,'inputs':inputs,'outputs':output,'latency_ms':round((time.perf_counter()-begin)*1000,3),'status':'failed' if error else 'completed','error':error,'scope':REGISTRY[name].scope,'purpose':'outcome_scoring'},db)
        if decision is not None:score,metric=score_action(config,condition,decision,execute)
        outcome={'decision_action':decision,'metric':metric,'case_study':config['case_study']['id'],'dataset_version':session['dataset_version'],'execution':'simulation','actor':'agent' if action=='autonomous' else 'participant'}
        db.execute('INSERT INTO decisions (id,session_id,message_id,action,created_at,client_deliberation_ms,server_since_seen_ms,outcome_score,score_regret,reason,participant_id,condition_id,config_version,round_id,model_snapshot,dataset_version,task,outcome_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (uid(),session['id'],mid,action,ts,elapsed,since_seen,score,1-score if score is not None else None,reason,session['participant_id'],session['condition_id'],session['config_version'],session['round_id'],session['model_snapshot'],session['dataset_version'],config['case_study']['id'],canonical(outcome)))
        db.execute('UPDATE decisions SET temperature=? WHERE session_id=? AND round_id=?',(session.get('temperature'),session['id'],session['round_id']))
        db.execute("UPDATE sessions SET status='completed',ended_at=? WHERE id=?",(ts,session['id']))
        self._release_session(db,session['id'])
        self.server_event(session,'decision_committed',{'action':action,'outcome':outcome,'message_id':mid},db)
        return {'status':'completed','action':action,'outcome_json':canonical(outcome),'completion_code':session['id']}

    def decide_pack(self,session,config,data):
        condition=next(c for c in config['conditions'] if c['id']==session['condition_id'])
        action=data.get('action');mid=data.get('message_id');decision=data.get('decision_action')
        if data.get('round_id',1)!=session['round_id']:raise ValueError('Round does not match')
        if not number(data.get('client_ms')) or data['client_ms']<0:raise ValueError('Invalid decision clock')
        reason=data.get('reason','')
        if not isinstance(reason,str) or len(reason)>4000:raise ValueError('Invalid reason')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT status FROM sessions WHERE id=?',(session['id'],)).fetchone()[0]!='active':raise Conflict('Session is already finished')
            if db.execute("SELECT 1 FROM requests WHERE session_id=? AND status='pending'",(session['id'],)).fetchone():raise Conflict('Wait for the pending response')
            latest=db.execute("SELECT * FROM messages WHERE session_id=? AND round_id=? AND role='assistant' ORDER BY created_at DESC,rowid DESC LIMIT 1",(session['id'],session['round_id'])).fetchone()
            if (latest['id'] if latest else None)!=mid:raise Conflict('Submit against the latest response')
            proposed=json.loads(latest['decision_action_json']) if latest and latest['decision_action_json'] else None
            mode=condition['agent']['mode']
            if condition['agent_enabled'] and mode=='autonomous':raise ValueError('Autonomous decisions do not accept participant approval')
            if condition['agent_enabled'] and mode=='recommender':
                if proposed is None or action not in ('accept','reject'):raise ValueError('Explicitly accept or reject the current proposal')
                if not latest['seen_at']:raise ValueError('View the recommendation before deciding')
                if action=='accept' and decision!=proposed:raise ValueError('Acceptance must match the proposal')
                if action=='reject' and decision is not None:raise ValueError('Rejection records no action')
            elif action!='independent':raise ValueError('Record an independent outcome in this condition')
            elapsed=data['client_ms']-latest['seen_client_ms'] if latest and latest['page_id']==data.get('page_id') and latest['seen_client_ms'] is not None else None
            if elapsed is not None and elapsed<0:raise ValueError('Decision precedes exposure')
            if action!='reject' and decision is None:raise ValueError('A decision is required')
            return self._pack_outcome(db,session,config,condition,action,decision,mid,elapsed,(now()-latest['seen_at'])*1000 if latest and latest['seen_at'] else None,reason,now())

    def provider_call(self, session, request_id, config, condition, items, invoke=None):
        """Reserve a whole request's configured maximum cost atomically, across every revision."""
        from .engine import call_openai, input_bound
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError("OPENAI_API_KEY is not configured on the server")
        a = config["architecture"]
        if not re.search(r"-\d{4}-\d{2}-\d{2}$",a["model"]) or not number(a["temperature"]):
            raise ProviderError("API sessions require a dated snapshot and fixed temperature; create a new configuration version")
        if input_bound(items) > a["max_input_tokens"]:
            raise ProviderError("Conversation exceeds the configured input safety limit; contact the researcher")
        from .budget import step_allowance,remaining
        from .agent import SystemLimit
        reserve=step_allowance(config)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute('SELECT status FROM sessions WHERE id=?',(session['id'],)).fetchone()[0]!='active':raise SystemLimit('session_ended','Session is already finished')
            credit=remaining(db,session['id'])
            if credit is None:
                # Legacy active sessions acquire an allocation before their next provider request.
                if not self.reserve_session(db,session,config):
                    db.commit()
                    raise SystemLimit('global_budget_admission','Application spend cap reached; no provider call was made')
                credit=remaining(db,session['id'])
            if credit is None or credit<reserve:raise SystemLimit('session_spend_cap','The reserved session spend allowance is exhausted; no provider call was made')
            db.execute("INSERT INTO provider_spend (id,session_id,request_id,reserved_micros,charged_micros,status,created_at,participant_id,condition_id,config_version,round_id,model_snapshot,dataset_version,temperature) VALUES(?,?,?,?,NULL,'reserved',?,?,?,?,?,?,?,?)", (uid(),session["id"],request_id,reserve,now(),session["participant_id"],session["condition_id"],session["config_version"],session["round_id"],session["model_snapshot"],session.get('dataset_version','legacy-config'),a['temperature']))
        try:
            result, metadata = invoke() if invoke else call_openai(config,condition,items)
        except Exception:
            with self.db() as db:
                db.execute("UPDATE provider_spend SET status='uncertain' WHERE session_id=? AND request_id=?", (session["id"],request_id))
            raise
        usage = metadata.get("usage") or {}
        known = all(type(usage.get(k)) is int and usage[k]>=0 for k in ("input_tokens","output_tokens"))
        charged = math.ceil(usage["input_tokens"]*a["input_usd_per_million"]+usage["output_tokens"]*a["output_usd_per_million"]) if known else None
        status = "overrun" if charged is not None and charged>reserve else "settled" if known else "uncertain"
        with self.db() as db:
            db.execute("UPDATE provider_spend SET charged_micros=?,status=? WHERE session_id=? AND request_id=?", (charged,status,session["id"],request_id))
        if status == "overrun":
            raise SystemLimit("provider_usage_overrun","Provider usage exceeded the reserved allowance")
        metadata.update(cost_micros=charged, reserved_cost_micros=reserve, cost_status=status)
        return result, metadata

    def budget(self):
        from .budget import accounting,cap_micros
        config=self.current()['config']
        with self.db() as db:
            row=accounting(db,now())
            row.update(dict(db.execute("SELECT COUNT(*) AS calls,COALESCE(SUM(status IN ('reserved','uncertain')),0) AS uncertain_calls FROM provider_spend").fetchone()))
        cap=cap_micros(config)
        row.update(cap_micros=cap,warning_80_percent=row['allocated_micros']>=.8*cap if cap else True)
        return row

    def failed_request(self, session_id, request_id):
        with self.db() as db:
            db.execute("UPDATE requests SET status='failed' WHERE session_id=? AND request_id=?", (session_id, request_id))

    def recover_pending(self):
        # Manual relay requests are durable; interrupted network/provider requests cannot resume.
        with self.db() as db:
            rows = db.execute("SELECT q.session_id,q.request_id,s.condition_id,r.config_json FROM requests q JOIN sessions s ON s.id=q.session_id JOIN revisions r ON r.id=s.revision_id WHERE q.status='pending'").fetchall()
            for row in rows:
                config = normalize(json.loads(row["config_json"]))
                cond = next(c for c in config["conditions"] if c["id"] == row["condition_id"])
                if config["architecture"]["provider"] != "relay" or private_mode(cond):
                    db.execute("UPDATE requests SET status='failed' WHERE session_id=? AND request_id=?", (row["session_id"], row["request_id"]))

    def request_view(self, session_id, request_id):
        with self.db() as db:
            req = db.execute("SELECT status FROM requests WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if not req:
                raise ValueError("Unknown request in this session")
            if req["status"] == "completed":
                message = db.execute("SELECT id,role,content,option_id,offer_price,created_at FROM messages WHERE session_id=? AND request_id=? AND role='assistant'", (session_id, request_id)).fetchone()
                return {"status": "completed", "message": dict(message)}
            return {"status": req["status"], "request_id": request_id}

    def pending_relay(self):
        """Only approved public context. Private policy/configuration never enters the relay."""
        with self.db() as db:
            rows = db.execute("SELECT q.session_id,q.request_id,s.preview,s.condition_id,r.config_json,m.content,m.offer_price,m.created_at FROM requests q JOIN sessions s ON s.id=q.session_id JOIN revisions r ON r.id=s.revision_id JOIN messages m ON m.session_id=q.session_id AND m.request_id=q.request_id AND m.role='user' WHERE q.status='pending' AND s.status='active' AND s.created_at>? ORDER BY m.created_at LIMIT 100", (now()-86400,)).fetchall()
        result = []
        for row in rows:
            config = normalize(json.loads(row["config_json"]))
            condition = next(c for c in config["conditions"] if c["id"]==row["condition_id"])
            if not row["preview"] or config["architecture"]["provider"]!="relay" or private_mode(condition) or not condition["agent_enabled"]:
                continue
            history = [m for m in self.transcript(row["session_id"]) if m["request_id"]!=row["request_id"]]
            negotiation = config["task"]=="negotiation"
            chosen, scores = (None,{}) if negotiation else choose(config,condition)
            price = negotiation_offer(config,condition,history,row["offer_price"]) if negotiation and condition["authority"]!="chat" else None
            result.append({"session_id":row["session_id"],"request_id":row["request_id"],"created_at":row["created_at"],"task":config["task"],
                "user_text":row["content"],"context":public_model_input(config,condition,history,row["content"],chosen,scores,price),
                "expected_option_id":chosen["id"] if chosen and condition["selection"]=="weighted" and condition["authority"]!="chat" else None,
                "expected_offer_price":price,"selection":condition["selection"],"output":condition["output"],
                "options":[] if negotiation else [{"id":o["id"],"label":o["label"]} for o in config["scenario"]["options"]]})
        return result

    def reply_relay(self, session_id, request_id, answer, option_id, offer_price=None):
        if not isinstance(answer,str) or not answer.strip() or len(answer)>16000:
            raise ValueError("Answer must contain 1–16000 characters")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT s.*,r.config_json,q.status AS request_status,m.created_at AS queued_at,m.offer_price AS user_offer FROM sessions s JOIN revisions r ON r.id=s.revision_id JOIN requests q ON q.session_id=s.id JOIN messages m ON m.session_id=q.session_id AND m.request_id=q.request_id AND m.role='user' WHERE s.id=? AND q.request_id=?", (session_id,request_id)).fetchone()
            if not row: raise ValueError("Unknown relay request")
            config = normalize(json.loads(row["config_json"]))
            cond = next(c for c in config["conditions"] if c["id"]==row["condition_id"])
            if config["architecture"]["provider"]!="relay" or not row["preview"] or private_mode(cond) or not cond["agent_enabled"]:
                raise ValueError("Only public preview relay requests accept manual replies")
            if row["status"]!="active" or row["request_status"]!="pending" or now()-row["created_at"]>86400:
                raise Conflict("The relay request is no longer pending in an active session")
            if config["task"]=="negotiation":
                history = [dict(m) for m in db.execute("SELECT * FROM messages WHERE session_id=? AND request_id!=? ORDER BY created_at", (session_id,request_id))]
                expected = negotiation_offer(config,cond,history,row["user_offer"]) if cond["authority"]!="chat" else None
                if option_id is not None or offer_price!=expected:
                    raise ValueError("Reply price must match the approved negotiation policy")
            else:
                chosen,_ = choose(config,cond)
                expected = chosen["id"] if chosen and cond["authority"]!="chat" else None
                if cond["selection"]=="weighted" and option_id!=expected:
                    raise ValueError("Reply option must match the configured weighted policy")
                if offer_price is not None: raise ValueError("Supplier selection does not accept a price offer")
            metadata = {"provider":"relay","response_source":"manual_chat_relay","requested_model":"chat-relay-v1", "returned_model":None,
                "engine_version":ENGINE_VERSION,"private_mode":False,"latency_ms":None,"relay_wait_ms":round((now()-row["queued_at"])*1000,2)}
            try:
                return self._finish_request(db,session_id,request_id,{"content":answer,"option_id":option_id,"offer_price":offer_price,"metadata":metadata})
            except ProviderError as exc:
                raise ValueError(str(exc)) from None

    def events(self, session, config, events):
        if not isinstance(events, list) or len(events) > 100:
            raise ValueError("Send at most 100 events per batch")
        allowed = {
            "visibility": {"state"}, "focus": {"state"}, "page_exit": set(), "page_open": set(),
            "interaction_counts": {"keystrokes", "clicks", "copies", "pastes", "cut", "typing_ms", "hover_ms", "paste_characters"},
            "recommendation_visible": {"message_id"},
            "clipboard_text": {"direction", "area", "text", "truncated"},
        }
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM sessions WHERE id=?", (session["id"],)).fetchone()
            if not row or row["status"] == "revoked":
                return
            capture = (row["status"] == "active" and config["telemetry"].get("clipboard_text")
                       and usage_evidence.participant_state(db, session["id"])["clipboard_capture"])
            for event in events:
                if not isinstance(event, dict) or set(event)-{"round_id"} != {"id", "type", "client_ms", "client_wall_ms", "page_id", "payload"}:
                    raise ValueError("Invalid event envelope")
                event_round = event.get("round_id", session["round_id"])
                if (config.get("case_study") or {}).get("id") == "ozer_game":
                    if type(event_round) is not int or not 1 <= event_round <= session["round_id"]:
                        raise ValueError("Invalid event round")
                elif event_round != session["round_id"]:
                    raise ValueError("Invalid event envelope")
                typ, payload = event["type"], event["payload"]
                if typ not in allowed or not isinstance(payload, dict) or set(payload) - allowed[typ]:
                    raise ValueError("Invalid event type or fields")
                for k in ("id", "page_id"):
                    if not isinstance(event[k], str) or not 1 <= len(event[k]) <= 100:
                        raise ValueError("Invalid event identifier")
                for k in ("client_ms", "client_wall_ms"):
                    from .config import number
                    if not number(event[k]) or not 0 <= event[k] <= 1e16:
                        raise ValueError("Invalid event clock")
                if typ in ("visibility", "focus") and payload.get("state") not in ("visible", "hidden", "focused", "blurred"):
                    raise ValueError("Invalid visibility or focus state")
                if typ == "clipboard_text":
                    if capture:
                        usage_evidence.record_clip(db, session, event, event_round, payload)
                    continue
                if typ == "interaction_counts":
                    for val in payload.values():
                        if not number(val) or not 0 <= val <= 1e9:
                            raise ValueError("Invalid interaction count")
                    if not config["telemetry"]["enabled"]:
                        continue
                if typ == "recommendation_visible":
                    mid = payload.get("message_id")
                    if not isinstance(mid, str) or len(mid) > 100:
                        raise ValueError("Invalid message ID")
                ts = now()
                inserted = db.execute("INSERT OR IGNORE INTO events (id,session_id,type,received_at,client_ms,client_wall_ms,page_id,payload_json,participant_id,condition_id,config_version,round_id,model_snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (event["id"],session["id"],typ,ts,event["client_ms"],event["client_wall_ms"],event["page_id"],canonical(payload),session["participant_id"],session["condition_id"],session["config_version"],event_round,session["model_snapshot"])).rowcount
                if inserted:db.execute('UPDATE events SET dataset_version=?,temperature=? WHERE id=?',(session['dataset_version'],session.get('temperature'),event['id']))
                if inserted and typ == "recommendation_visible":
                    db.execute("UPDATE messages SET seen_at=?,seen_client_ms=?,page_id=? WHERE id=? AND session_id=? AND role='assistant' AND seen_at IS NULL", (ts, event["client_ms"], event["page_id"], mid, session["id"]))

    def decide(self, session, config, data):
        config = normalize(config)
        if config['case_study']:return self.decide_pack(session,config,data)
        action, option, mid = data.get("action"), data.get("option_id"), data.get("message_id")
        if action not in ("accept","reject","override","independent"):
            raise ValueError("Invalid decision action")
        reason = data.get("reason", "")
        if not isinstance(reason,str) or len(reason)>4000:
            raise ValueError("Decision reason is too long")
        client_ms = data.get("client_ms")
        if not number(client_ms) or not 0<=client_ms<=1e16:
            raise ValueError("Invalid decision clock")
        if data.get("round_id",session["round_id"])!=session["round_id"]:
            raise ValueError("Round does not match the session")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM sessions WHERE id=?", (session["id"],)).fetchone()[0]!="active":
                raise Conflict("A final decision was already recorded or the session was stopped")
            if db.execute("SELECT 1 FROM requests WHERE session_id=? AND round_id=? AND status='pending'", (session["id"],session["round_id"])).fetchone():
                raise Conflict("Wait for the pending response before submitting a decision")
            latest = db.execute("SELECT * FROM messages WHERE session_id=? AND round_id=? AND role='assistant' ORDER BY created_at DESC,rowid DESC LIMIT 1", (session["id"],session["round_id"])).fetchone()
            if (latest["id"] if latest else None)!=mid:
                raise Conflict("Submit against the latest assistant response")
            recommended = latest["option_id"] if latest else None
            recommended_price = latest["offer_price"] if latest else None
            final_price, agreement = None, None
            outcome = {}
            if config["task"]=="negotiation":
                n = config["negotiation"]
                final_price = data.get("final_price")
                if option is not None:
                    raise ValueError("Negotiation records prices, not supplier IDs")
                if action=="reject":
                    if final_price is not None: raise ValueError("Finishing without agreement records no price")
                elif not number(final_price) or not n["price_min"]<=final_price<=n["price_max"] or abs(final_price*100-round(final_price*100))>1e-6:
                    raise ValueError("Final price must be inside the public range with at most two decimals")
                if action=="accept" and (recommended_price is None or final_price!=recommended_price):
                    raise ValueError("Accept must match the agent's current price")
                if action=="override" and (recommended_price is None or final_price==recommended_price):
                    raise ValueError("Override requires a different final price")
                if action=="independent" and recommended_price is not None:
                    raise ValueError("Independent pricing requires no structured agent offer")
                assigned_condition = next(c for c in config["conditions"] if c["id"]==session["condition_id"])
                agreement = action=="accept" and assigned_condition["role"] in ("buyer","supplier")
                participant_role = public_config(config,next(c for c in config["conditions"] if c["id"]==session["condition_id"]))["negotiation"]["participant_role"]
                outcome = {"currency":n["currency"],"quantity":n["quantity"],"agreement":agreement,"participant_role":participant_role,
                    "recorded_unit_price":final_price,"agreed_total":round(final_price*n["quantity"],2) if agreement else None}
                score = ((n["price_max"]-final_price) if participant_role=="buyer" else (final_price-n["price_min"]))/ (n["price_max"]-n["price_min"]) if final_price is not None else None
                regret = 1-score if score is not None else None
            else:
                if option not in [None]+[o["id"] for o in config["scenario"]["options"]]:
                    raise ValueError("Invalid final option")
                if action=="accept" and (not recommended or option!=recommended): raise ValueError("Accept must match the agent's current choice")
                if action=="override" and (not recommended or not option or option==recommended): raise ValueError("Override requires a different final option")
                if action=="reject" and (not recommended or option is not None): raise ValueError("Reject records no final selection")
                if action=="independent" and (recommended is not None or option is None): raise ValueError("Independent selection requires an option and no agent choice")
                scores = rank_options(config["scenario"])
                score = scores.get(option)
                regret = max(scores.values())-score if score is not None else None
            if (recommended is not None or recommended_price is not None) and not latest["seen_at"]:
                raise ValueError("The recommendation must be displayed before a decision is submitted")
            ts, elapsed = now(), None
            if latest and latest["page_id"]==data.get("page_id") and latest["seen_client_ms"] is not None:
                elapsed = client_ms-latest["seen_client_ms"]
                if elapsed<0: raise ValueError("Decision clock precedes recommendation exposure")
            since_seen = (ts-latest["seen_at"])*1000 if latest and latest["seen_at"] else None
            db.execute("""INSERT INTO decisions (id,session_id,message_id,action,recommended_option,final_option,created_at,
                client_deliberation_ms,server_since_seen_ms,outcome_score,score_regret,reason,participant_id,condition_id,config_version,round_id,model_snapshot,
                task,final_price,recommended_price,agreement,outcome_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid(),session["id"],mid,action,recommended,option,ts,elapsed,since_seen,score,regret,reason,
                 session["participant_id"],session["condition_id"],session["config_version"],session["round_id"],session["model_snapshot"],
                 config["task"],final_price,recommended_price,agreement,canonical(outcome)))
            db.execute('UPDATE decisions SET temperature=? WHERE session_id=? AND round_id=?',(session.get('temperature'),session['id'],session['round_id']))
            db.execute("UPDATE sessions SET status='completed',ended_at=? WHERE id=?", (ts,session["id"]))
            self._release_session(db,session["id"])
        return {"status":"completed","action":action,"final_option":option,"final_price":final_price,"agreement":agreement,"completion_code":session["id"]}

    def withdraw(self, session):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session["id"],)).fetchone()
            if not row:
                raise PermissionError("Invalid session token")
            if row["status"] == "revoked":
                return self.session_status(dict(row))
            db.execute("UPDATE sessions SET status='withdrawn',ended_at=? WHERE id=? AND status='active'", (now(), session["id"]))
            db.execute("UPDATE requests SET status='failed' WHERE session_id=? AND status='pending'", (session["id"],))
            self._release_session(db,session["id"])
        return {"status": "withdrawn"}

    @staticmethod
    def session_status(session):
        result = {"status": session["status"]}
        if session["status"] == "revoked":
            result.update(system_message=session["system_message"], completion_code=session["id"],
                          next_action="return_to_survey")
        return result

    def revoke_session(self, session_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row or row["status"] != "active":
                raise Conflict("Only active sessions can be revoked.")
            message = "The researcher ended access to this session. Return to your survey to continue."
            db.execute("""
                UPDATE sessions SET status='revoked', ended_at=?, end_reason='researcher_revoked',
                                    system_message=? WHERE id=?
            """, (now(), message, session_id))
            db.execute("UPDATE requests SET status='failed' WHERE session_id=? AND status='pending'", (session_id,))
            self._release_session(db, session_id)
            self.server_event(dict(row), "session_ended",
                              {"state": "revoked", "reason": "researcher_revoked",
                               "next_action": "return_to_survey"}, db)

    def delete_session(self, session_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise Conflict("This session has already been deleted.")
            if row["status"] == "active":
                raise Conflict("Revoke this session before deleting its records.")
            for table in ("messages", "requests", "events", "decisions", "ozer_rounds",
                          "ozer_progress", "session_budgets", "session_edits", "clipboard_evidence",
                          "ai_evidence_sessions", "ai_evidence_reviews"):
                db.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
            # Keep accounting intact, including charges from provider calls still in flight.
            db.execute("UPDATE provider_spend SET participant_id='' WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def edit_session(self, session_id, account, *, label, note, restore, expected_edit_id):
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 100:
            raise ValueError("Choose a valid session.")
        if type(restore) is not bool or type(expected_edit_id) is not int or expected_edit_id < 0:
            raise ValueError("Reopen the session before editing it.")
        if restore:
            label = note = ""
        elif not isinstance(label, str) or not isinstance(note, str) or len(label) > 100 or len(note) > 2000:
            raise ValueError("Enter a label up to 100 characters and a note up to 2,000 characters.")
        label, note = " ".join(label.split()), note.strip()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise Conflict("This session no longer exists.")
            latest = db.execute("""SELECT id,label,note FROM session_edits
                                   WHERE session_id=? ORDER BY id DESC LIMIT 1""", (session_id,)).fetchone()
            if expected_edit_id != (latest["id"] if latest else 0):
                raise Conflict("Another researcher edited this session. Reopen it before saving.")
            if (label, note) == ((latest["label"], latest["note"]) if latest else ("", "")):
                return
            db.execute("""INSERT INTO session_edits(session_id,actor_id,actor_name,created_at,action,label,note)
                          VALUES(?,?,?,?,?,?,?)""", (session_id, account["id"], account["username"] or "Local workspace",
                                                        now(), "restore" if restore else "edit", label, note))

    def overview(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute("""SELECT s.id,s.participant_id,s.condition_id,s.revision_id,
                s.config_hash,s.created_at,s.ended_at,s.status,s.end_reason,s.system_message,s.preview,d.action,d.final_option,d.final_price,d.agreement,d.outcome_json,s.config_version,s.round_id,s.model_snapshot,s.dataset_version,s.temperature,
                r.config_json,COALESCE(e.id,0) AS edit_id,COALESCE(e.label,'') AS label,
                (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id AND m.role='user') AS prompt_count
                FROM sessions s JOIN revisions r ON r.id=s.revision_id
                LEFT JOIN session_edits e ON e.id=(SELECT id FROM session_edits WHERE session_id=s.id ORDER BY id DESC LIMIT 1)
                LEFT JOIN decisions d ON d.session_id=s.id AND d.round_id=s.round_id ORDER BY s.created_at DESC,s.id LIMIT 1000""")]
            totals = dict(db.execute("SELECT COUNT(*) AS sessions, SUM(status='completed') AS completed, SUM(preview=1) AS previews FROM sessions").fetchone())
        for row in rows:
            config = normalize(json.loads(row.pop("config_json")))
            condition = next(c for c in config["conditions"] if c["id"] == row["condition_id"])
            row["task"] = config["task"]
            if config['case_study']:
                row['task']=config['case_study']['id']
                outcome=json.loads(row['outcome_json'] or '{}')
                row['final_option_label']=canonical(outcome.get('decision_action')) if outcome.get('decision_action') is not None else None
            row["currency"] = config["negotiation"]["currency"] if config["task"]=="negotiation" else None
            row["researcher_flag"] = row["status"]=="ended_system_limit"
            row["condition_label"] = condition["label"]
            if not config['case_study']:row["final_option_label"] = next((o["label"] for o in config["scenario"]["options"] if o["id"] == row["final_option"]), None)
            row["duration_ms"] = round((row["ended_at"]-row["created_at"])*1000, 2) if row["ended_at"] is not None else None
        return {"sessions": rows, "totals": totals, "budget":self.budget()}

    def session_detail(self, session_id, include_all_events=False):
        """Researcher-only inspection. Explicit fields keep session capabilities out of the response."""
        with self.db() as db:
            db.execute("BEGIN")
            row = db.execute("""SELECT s.id,s.participant_id,s.condition_id,s.revision_id,s.config_hash,
                s.created_at,s.ended_at,s.status,s.end_reason,s.system_message,s.preview,s.consent_at,s.consent_version,s.config_version,s.round_id,s.model_snapshot,s.dataset_version,s.temperature,r.config_json
                FROM sessions s JOIN revisions r ON r.id=s.revision_id WHERE s.id=?""", (session_id,)).fetchone()
            if row is None:
                return None
            session = dict(row)
            edits = [dict(edit) for edit in db.execute("""SELECT id,actor_id,actor_name,created_at,action,label,note
                     FROM session_edits WHERE session_id=? ORDER BY id DESC""", (session_id,))]
            latest = edits[0] if edits else None
            session.update(edit_id=latest["id"] if latest else 0, label=latest["label"] if latest else "",
                           note=latest["note"] if latest else "")
            config = normalize(json.loads(session.pop("config_json")))
            condition = next(c for c in config["conditions"] if c["id"] == session["condition_id"])
            messages = [dict(m) for m in db.execute("""SELECT m.id,m.role,m.content,m.created_at,m.option_id,
                m.metadata_json,m.request_id,m.seen_at,m.seen_client_ms,m.page_id,m.offer_price,m.participant_id,m.condition_id,m.config_version,m.round_id,m.model_snapshot,m.dataset_version,m.temperature,m.decision_action_json,q.status AS request_status
                FROM messages m LEFT JOIN requests q ON q.session_id=m.session_id AND q.request_id=m.request_id
                WHERE m.session_id=? ORDER BY m.created_at,m.rowid""", (session_id,))]
            decision = db.execute("SELECT * FROM decisions WHERE session_id=? AND round_id=?", (session_id,session["round_id"])).fetchone()
            ozer_rounds = None
            if (config.get("case_study") or {}).get("id") == "ozer_game":
                ozer_rounds = [json.loads(r["outcome_json"]) for r in db.execute(
                    "SELECT outcome_json FROM decisions WHERE session_id=? ORDER BY round_id", (session_id,))]
            totals = {k: 0 for k in ("keystrokes", "clicks", "copies", "pastes", "cut", "typing_ms", "hover_ms", "paste_characters")}
            event_counts = {}
            last_activity = session["created_at"]
            for event in db.execute("SELECT type,received_at,payload_json FROM events WHERE session_id=?", (session_id,)):
                event_counts[event["type"]] = event_counts.get(event["type"], 0) + 1
                last_activity = max(last_activity, event["received_at"])
                if event["type"] == "interaction_counts":
                    for key, value in json.loads(event["payload_json"]).items():
                        if key in totals:
                            totals[key] += value
            events = [dict(e) for e in db.execute("""SELECT id,type,received_at,client_ms,client_wall_ms,page_id,payload_json,participant_id,session_id,condition_id,config_version,round_id,model_snapshot
                ,dataset_version,temperature FROM events WHERE session_id=? AND type!='interaction_counts'
                ORDER BY received_at DESC,rowid DESC LIMIT 80""", (session_id,))]
            raw_events = [dict(e) for e in db.execute("SELECT id,type,received_at,client_ms,client_wall_ms,page_id,payload_json,participant_id,session_id,condition_id,config_version,round_id,model_snapshot,dataset_version,temperature FROM events WHERE session_id=? ORDER BY received_at,rowid", (session_id,))] if include_all_events else None
            evidence = usage_evidence.review(db, session_id, config, messages)
        for message in messages:
            message["metadata"] = json.loads(message.pop("metadata_json"))
            last_activity = max(last_activity, message["created_at"])
        for event in events:
            event["payload"] = json.loads(event.pop("payload_json"))
        if session["ended_at"] is not None:
            last_activity = max(last_activity, session["ended_at"])
        session["duration_ms"] = round((session["ended_at"]-session["created_at"])*1000, 2) if session["ended_at"] is not None else None
        session["last_activity_at"] = last_activity
        prompts = [m for m in messages if m["role"] == "user"]
        replies = [m for m in messages if m["role"] == "assistant"]
        result = {
            "session": session, "edit_history": edits, "decision": dict(decision) if decision else None, "usage_evidence": evidence,
            "messages": messages, "activity": {"totals": totals, "event_counts": event_counts,
                "event_count": sum(event_counts.values()), "timeline": events,
                "timeline_total": sum(v for k, v in event_counts.items() if k != "interaction_counts")},
            "metrics": {"prompt_count": len(prompts), "reply_count": len(replies),
                "prompt_characters": sum(len(m["content"]) for m in prompts),
                "pending_requests": sum(m["request_status"] == "pending" for m in prompts),
                "failed_requests": sum(m["request_status"] == "failed" for m in prompts)},
            "setup": {"name": config["name"], "task":config["task"], "case_study":config['case_study'], "negotiation":public_config(config, condition)["negotiation"], "scenario": public_config(config, condition)["scenario"],
                "architecture": config["architecture"], "telemetry_enabled": config["telemetry"]["enabled"],
                "condition": {**{k: condition[k] for k in ("id", "label", "role", "objective", "authority", "reasoning", "output", "selection", "prompt", "agent_enabled", "advice_quality", "agent")},
                              "private_mode": private_mode(condition)}}}
        if ozer_rounds is not None:
            result["ozer_rounds"] = ozer_rounds
        if raw_events is not None:
            for event in raw_events:
                event["payload"] = json.loads(event.pop("payload_json"))
            result["activity"]["raw_events"] = raw_events
        return result

    def export(self, table):
        if table not in ("sessions", "messages", "events", "decisions", "revisions", "provider_spend"):
            raise ValueError("Unknown export table")
        with self.db() as db:
            if table == "sessions":
                query = """SELECT s.id,s.id AS session_id,s.participant_id,s.condition_id,s.revision_id,s.config_hash,s.created_at,
                    s.ended_at,s.status,s.end_reason,s.system_message,s.preview,s.consent_version,s.consent_at,s.config_version,s.round_id,s.model_snapshot,s.dataset_version,s.temperature,
                    (s.ended_at-s.created_at)*1000 AS total_duration_ms,
                    (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id AND m.role='user') AS prompt_count,
                    (SELECT SUM(LENGTH(content)) FROM messages m WHERE m.session_id=s.id AND m.role='user') AS prompt_characters
                    FROM sessions s"""
            elif table == "revisions":
                query = "SELECT id AS config_version,* FROM revisions"
            else:
                # Always carry participant, condition, revision and preview fields into every data export.
                query = f"SELECT t.*,s.participant_id,s.condition_id,s.revision_id,s.config_hash,s.preview FROM {table} t JOIN sessions s ON s.id=t.session_id"
            return [dict(r) for r in db.execute(query)]
