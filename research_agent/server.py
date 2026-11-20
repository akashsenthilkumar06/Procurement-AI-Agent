import argparse
import csv
import io
import json
import os
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from dotenv import load_dotenv

from . import VERSION
from .config import canonical, private_mode, normalize, validate, public_config, fingerprint
from .engine import ProviderError, respond
from .agent import SystemLimit
from .store import Conflict, Store
from . import accounts, chat_history, setup_chat, usage_evidence
from .ozer import OzerGame, expire_waiting_rounds, is_ozer
from .offer_policy import chat_offer, chat_acceptance

ROOT = Path(__file__).resolve().parent.parent


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, admin_token, integration_key="", local_mode=False, frame_origins=()):
        super().__init__(address, Handler)
        self.store, self.admin_token, self.integration_key = store, admin_token, integration_key
        self.local_mode = local_mode
        self.frame_origins = tuple(frame_origins)
        self.auth_lock = threading.Lock()
        self.login_attempts = {}

        self.last_ozer_sweep = time.monotonic()

    def service_actions(self):
        tick = time.monotonic()
        if tick - self.last_ozer_sweep >= 5:
            self.last_ozer_sweep = tick
            try:
                expire_waiting_rounds(self.store)
            except sqlite3.OperationalError:
                print("Ozer timeout sweep could not access the database.")


class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchAgent/" + VERSION

    def log_message(self, fmt, *args):
        # No URL, query string, bearer token, launch token, or message body in access logs.
        print(f"{self.command} {urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}")

    def headers_common(self, content_type):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        ancestors = "'self' " + " ".join(self.server.frame_origins)
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors {ancestors}; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not self.server.frame_origins:
            self.send_header("X-Frame-Options", "SAMEORIGIN")

    def send(self, value, status=200, content_type="application/json; charset=utf-8", extra_headers=None):
        body = canonical(value).encode() if content_type.startswith("application/json") else value.encode() if isinstance(value, str) else value
        self.send_response(status)
        self.headers_common(content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, max_bytes=250000):
        if self.headers.get("X-Research-Request") != "1":
            raise PermissionError("Missing request protection header")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("JSON content type is required")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= max_bytes:
            raise ValueError(f"Request size must be 1–{max_bytes} bytes")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Request must be a JSON object")
        return value

    def researcher_cookie(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            raise PermissionError("Researcher sign-in is required") from None
        token = cookie.get("research_admin")
        return token.value if token and len(token.value) <= 100 else None

    def admin(self):
        account = accounts.get_account(self.server.store, self.researcher_cookie())
        if account is not None:
            return account
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer ") and len(header) > 7 and secrets.compare_digest(header[7:].encode(), self.server.admin_token.encode()):
            return None
        raise PermissionError("Researcher sign-in is required")

    def session(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) > 200:
            raise PermissionError("Participant session is required")
        return self.server.store.authenticate(header[7:])

    def login_cookie(self, token, age=accounts.LOGIN_SECONDS):
        secure = "" if self.server.local_mode else "; Secure"
        return {"Set-Cookie": f"research_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}{secure}"}

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        try:
            self.route(method)
        except PermissionError as exc:
            self.send({"error": str(exc)}, 403)
        except Conflict as exc:
            self.send({"error": str(exc)}, 409)
        except chat_history.ChatNotFound as exc:
            self.send({"error": str(exc)}, 404)
        except ProviderError as exc:
            self.send({"error": str(exc)}, 502)
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            self.send({"error": str(exc) or "Invalid request"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print(f"Internal request failure: {type(exc).__name__}")
            self.send({"error": "Unexpected server error. Contact the researcher."}, 500)

    def route(self, method):
        store = self.server.store
        parsed = urlsplit(self.path)
        path = parsed.path
        if method == "GET" and path == "/api/health":
            return self.send({"ok": True, "version": VERSION, "local_mode": self.server.local_mode,
                              "accounts_configured": accounts.configured(store),
                              "openai_configured": bool(os.environ.get("OPENAI_API_KEY"))})
        if method == "GET" and path in ("/", "/participate", "/style.css", "/theme.js", "/admin.js", "/participant.js", "/ozer.js", "/research-mark.svg"):
            filename = {"/": "index.html", "/participate": "participant.html"}.get(path, path[1:])
            extension = Path(filename).suffix
            mime = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".html": "text/html"}[extension]
            return self.send((ROOT / "static" / filename).read_bytes(), content_type=mime + "; charset=utf-8")
        if method == "POST" and path == "/api/login":
            data = self.read_json(max_bytes=8192)
            with self.server.auth_lock:
                ts = time.time()
                # Bound memory without retaining old IP histories.
                self.server.login_attempts = {k: v for k, v in self.server.login_attempts.items() if v[0] > ts - 60}
                start, count = self.server.login_attempts.get(self.client_address[0], (ts, 0))
                self.server.login_attempts[self.client_address[0]] = (start, count + 1)
            if count >= 10:
                return self.send({"error": "Too many sign-in attempts; wait one minute"}, 429)
            if "username" in data:
                token = accounts.sign_in(store, data.get("username"), data.get("password"))
            else:
                key = data.get("token")
                if not isinstance(key, str) or not secrets.compare_digest(key.encode(), self.server.admin_token.encode()):
                    raise PermissionError("Invalid researcher access key")
                token = accounts.legacy_login(store)
            accounts.sign_out(store, self.researcher_cookie())
            return self.send({"ok": True}, extra_headers=self.login_cookie(token))
        if method == "POST" and path == "/api/logout":
            self.read_json()
            accounts.sign_out(store, self.researcher_cookie())
            return self.send({"ok": True}, extra_headers=self.login_cookie("", 0))
        if method == "POST" and path == "/api/local-login":
            self.read_json()
            host = self.headers.get("Host", "").split(":")[0]
            if not self.server.local_mode or self.client_address[0] != "127.0.0.1" or host not in ("localhost", "127.0.0.1"):
                raise PermissionError("Local workspace access is unavailable")
            return self.send({"ok": True}, extra_headers=self.login_cookie(accounts.legacy_login(store)))
        if path.startswith("/api/admin/"):
            account = self.admin()
            if path in ("/api/admin/account", "/api/admin/chats", "/api/admin/setup-chats",
                        "/api/admin/setup-chats/ask", "/api/admin/session/edit", "/api/admin/session/ai-assessment") or path.startswith(("/api/admin/account/", "/api/admin/chats/")):
                if account is None:
                    raise PermissionError("Sign in through the researcher workspace.")
                return self.researcher_route(method, path, parsed.query, account)
            if method=='GET' and path=='/api/admin/case-studies':
                from .packs import catalog
                return self.send([{'id':p['id'],'version':p['version'],'title':p['title'],'instructions':p['scenario']['instructions'],'allowed_tools':p['allowed_tools']} for p in catalog()])
            if method=='POST' and path=='/api/admin/case-study-config':
                from .packs import study_config
                data=self.read_json()
                return self.send({'config':study_config(data['id'],data['version'])})
            if path == "/api/admin/relay":
                if not self.server.local_mode:
                    raise PermissionError("This-chat relay is available only on a local preview server")
                if method == "GET":
                    return self.send({"requests": store.pending_relay()})
                if method == "POST":
                    data = self.read_json()
                    return self.send(store.reply_relay(data["session_id"], data["request_id"], data["answer"], data.get("option_id"), data.get("offer_price")))
            if method == "POST" and path == "/api/admin/setup-assistant":
                from .setup_assistant import draft_setup
                return self.send(draft_setup(store, self.read_json(max_bytes=6_000_000)))
            if method == "GET" and path == "/api/admin/setup-generations":
                generation_id = parse_qs(parsed.query).get("id", [None])[0]
                if generation_id is None:
                    return self.send(store.setup_generations())
                if not generation_id.isascii() or not generation_id.isdecimal() or len(generation_id) > 18:
                    raise ValueError("A valid generation ID is required.")
                result = store.setup_generation(int(generation_id))
                return self.send(result) if result is not None else self.send({"error": "Generation not found."}, 404)
            if method == "POST" and path == "/api/admin/session-assistant":
                from .session_assistant import answer_session_question
                return self.send(answer_session_question(store, self.read_json(max_bytes=12000)))
            if method == "POST" and path == "/api/admin/validate-config":
                config=validate(self.read_json()["config"])
                return self.send({"config":config,"config_hash":fingerprint(config)})
            if method == "POST" and path == "/api/admin/builder-preview":
                data = self.read_json()
                config = validate(data["config"])
                condition = next((c for c in config["conditions"] if c["id"] == data["condition_id"]), None)
                if condition is None:
                    raise ValueError("Choose a condition in this setup")
                return self.send({"public": public_config(config, condition)})
            if method == "POST" and path == "/api/admin/condition-preview":
                data = self.read_json()
                config = validate(data["config"])
                condition = next(c for c in config["conditions"] if c["id"]==data["condition_id"])
                mock = normalize(config)
                mock["architecture"].update(provider="demo",model="deterministic-v1",temperature=None,reasoning_effort=None)
                if is_ozer(config):
                    sample = {"content": "Ozer previews use a deterministic counterpart. Open the participant preview to play either role."}
                else:
                    sample = respond(mock,condition,[],"Show a public example response") if condition["agent_enabled"] else None
                return self.send({"public":public_config(config,condition),"condition":{k:condition[k] for k in ("id","label","role","authority","output","agent_enabled")},
                    "config_hash":fingerprint(config),"sample":sample,"preview_kind":"deterministic_mock"})
            if method == "GET" and path == "/api/admin/config":
                return self.send(store.current())
            if path == "/api/admin/setup-slots":
                if method == "GET":
                    slot_id = parse_qs(parsed.query).get("id", [None])[0]
                    return self.send(store.setup_slots() if slot_id is None else store.setup_slot(slot_id))
                if method == "POST":
                    data = self.read_json()
                    return self.send(store.save_setup_slot(data["label"], data["config"], data.get("id"),
                                                           data.get("expected_revision")))
            if method == "POST" and path == "/api/admin/delete-setup-slot":
                data = self.read_json()
                store.delete_setup_slot(data["id"], data["expected_revision"])
                return self.send({"ok": True})
            if method == "POST" and path == "/api/admin/use-setup-slot":
                data = self.read_json()
                return self.send(store.use_setup_slot(data["id"], data["expected_revision"],
                                                      data["expected_study_revision"]))
            if method == "GET" and path == "/api/admin/session":
                params = parse_qs(parsed.query)
                session_id = params.get("session_id", [""])[0]
                if not session_id or len(session_id) > 100:
                    raise ValueError("A valid session ID is required")
                download = params.get("download") == ["1"]
                detail = store.session_detail(session_id, include_all_events=download)
                if detail is None:
                    return self.send({"error": "Session not found"}, 404)
                headers = {"Content-Disposition": f'attachment; filename="session-{detail["session"]["id"]}.json"'} if download else None
                return self.send(detail, extra_headers=headers)
            if method == "GET" and path == "/api/admin/pending-launches":
                return self.send(store.pending_launches())
            session_actions = {
                "/api/admin/revoke-session": ("session_id", store.revoke_session),
                "/api/admin/delete-session": ("session_id", store.delete_session),
                "/api/admin/revoke-launch": ("launch_id", store.revoke_launch),
            }
            if method == "POST" and path in session_actions:
                field, action = session_actions[path]
                identifier = self.read_json().get(field)
                if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
                    raise ValueError("A valid ID is required")
                action(identifier)
                return self.send({"ok": True})
            if method == "GET" and path == "/api/admin/overview":
                return self.send(store.overview())
            if method == "POST" and path == "/api/admin/config":
                data = self.read_json()
                return self.send(store.save(data["config"], data["expected_revision"]))
            if method == "POST" and path == "/api/admin/launch":
                data = self.read_json()
                if type(data.get("preview", False)) is not bool:
                    raise ValueError("Preview must be a boolean")
                return self.send(store.issue_launch(
                    data["participant_id"], data.get("condition_id"), data.get("preview", False),
                    data.get("assignment_method"), slot_id=data.get("slot_id"),
                    expected_slot_revision=data.get("expected_slot_revision"), allow_relay=self.server.local_mode,
                    demo_agent=data.get("demo_agent"), live_preview=data.get("live_preview", False),
                    expected_study_revision=data.get("expected_study_revision"),
                ))
            if method == "GET" and path == "/api/admin/export":
                params = parse_qs(parsed.query)
                table = params.get("table", ["sessions"])[0]
                rows = store.export(table)
                if params.get("format", ["csv"])[0] == "json":
                    return self.send(rows, extra_headers={"Content-Disposition": f'attachment; filename="{table}.json"'})
                buffer = io.StringIO(newline="")
                if rows:
                    fields = list(rows[0])
                else:
                    with store.db() as db:
                        fields = [r["name"] for r in db.execute(f"PRAGMA table_info({table})")]
                    if table == "sessions":
                        fields.remove("token_hash")
                        fields += ["session_id","total_duration_ms", "prompt_count", "prompt_characters"]
                    elif table == "revisions":
                        fields.insert(0,"config_version")
                    else:
                        fields += [k for k in ("participant_id","condition_id","revision_id","config_hash","preview") if k not in fields]
                writer = csv.DictWriter(buffer, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    # Prevent formula execution on opening an export in Excel.
                    writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v for k, v in row.items()})
                return self.send("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8", extra_headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})
        if method == "POST" and path == "/api/launch":
            data = self.read_json()
            key = self.headers.get("X-Integration-Key", "")
            if not self.server.integration_key or not secrets.compare_digest(key, self.server.integration_key):
                raise PermissionError("Server-side integration key is required")
            return self.send(store.issue_launch(data["participant_id"], data.get("condition_id"), False, data.get("assignment_method")))
        if method == "POST" and path == "/api/participant/preview":
            return self.send(store.preview(self.read_json()["launch_token"]))
        if method == "POST" and path == "/api/participant/start":
            data = self.read_json()
            return self.send(store.start(data["launch_token"], data.get("consent"), data.get("clipboard_consent", False)))
        if path.startswith("/api/participant/"):
            session, config, condition = self.session()
            if method == "POST" and path == "/api/participant/ai-report":
                return self.send(usage_evidence.self_report(store, session, config, self.read_json(2000).get("answer")))
            if method == "GET" and path == "/api/participant/status":
                return self.send(store.session_status(session))
            if is_ozer(config):
                if method == "GET" and path == "/api/participant/session":
                    return self.send(OzerGame(store, session, config, condition).view())
                if method == "POST" and path == "/api/participant/ozer":
                    return self.send(OzerGame(store, session, config, condition).advance(self.read_json()))
                if method == "POST" and path == "/api/participant/withdraw":
                    self.read_json()
                    store.withdraw(session)
                    return self.send(OzerGame(store, session, config, condition).view())
                if path != "/api/participant/events":
                    return self.send({"error": "Use the Ozer round controls for this session."}, 404)
            if method == "GET" and path == "/api/participant/session":
                return self.send(store.session_view(session, config, condition))
            if session["status"] == "revoked":
                if method == "POST":
                    self.read_json()
                return self.send(store.session_status(session))
            if method == "GET" and path == "/api/participant/request":
                rid = parse_qs(parsed.query).get("request_id", [""])[0]
                if not rid or len(rid) > 100:
                    raise ValueError("A valid request ID is required")
                return self.send(store.request_view(session["id"],rid))
            if method == "POST" and path == "/api/participant/message":
                data = self.read_json()
                content, rid = data.get("content"), data.get("request_id")
                if not isinstance(content, str) or not content.strip() or len(content) > 12000:
                    raise ValueError("Message must contain 1–12000 characters")
                if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
                    raise ValueError("A request ID is required")
                if data.get("round_id",session["round_id"]) != session["round_id"]:
                    raise ValueError("Round does not match the session")
                if ((config.get("case_study") or {}).get("id") == "negotiation"
                        and condition.get("agent_enabled") and condition["agent"]["mode"] == "recommender"):
                    acceptance = chat_acceptance(content, config["negotiation"]["currency"])
                    if acceptance is not None:
                        return self.send(store.decide_pack(session, config, data, chat_acceptance=acceptance))
                offer_price = data.get("offer_price")
                if config["task"] == "negotiation" and offer_price is None:
                    offer_price = chat_offer(content, config["negotiation"]["currency"])
                relay = config["architecture"]["provider"] == "relay" and not private_mode(condition) and not config['case_study']
                if relay and (not self.server.local_mode or not session["preview"]):
                    raise PermissionError("This-chat relay is for local previews only")
                if not store.begin_request(session["id"], rid, content, allow_pending=relay, offer_price=offer_price):
                    if relay:
                        result = store.request_view(session["id"],rid)
                        if result["status"] == "pending":
                            return self.send({**result, "participant_offer_price":offer_price},202)
                        result["message"]["participant_offer_price"] = offer_price
                        return self.send(result["message"])
                    old = next(m for m in store.transcript(session["id"]) if m["request_id"] == rid and m["role"] == "assistant")
                    return self.send({**{k: old[k] for k in ("id", "role", "content", "option_id", "offer_price", "created_at")},'decision_action':json.loads(old['decision_action_json']) if old['decision_action_json'] else None,
                        'participant_offer_price':offer_price, 'safe_fallback':bool(json.loads(old['metadata_json']).get('safe_fallback')),
                        'committed_decision':dict(store.session_view(*store.authenticate(self.headers['Authorization'][7:]))['decision'] or {}) if config['case_study'] and condition['agent']['mode']=='autonomous' else None})
                if relay:
                    return self.send({"status":"pending","request_id":rid,"participant_offer_price":offer_price},202)
                try:
                    history = [m for m in store.transcript(session["id"]) if m["request_id"] != rid]
                    if config['case_study']:
                        from .agent import run_agent
                        from .providers import agent_step
                        def model_call(items,definitions,schema,remaining,step):
                            return store.provider_call(session,rid+':'+str(step),config,condition,items+[{'tool_definitions':definitions,'schema':schema}],
                                invoke=lambda:agent_step(config,condition,items,definitions,schema,remaining))
                        result=run_agent(config,condition,history,content,tool_log=lambda payload:store.server_event(session,'tool_call',{'request_id':rid,'purpose':'agent',**payload}),model_call=model_call,offer_price=offer_price)
                    else:
                        result = respond(config,condition,history,content,offer_price=offer_price,
                                         provider_call=lambda c,cond,items:store.provider_call(session,rid,c,cond,items))
                    reply = store.finish_request(session["id"], rid, result)
                    reply["participant_offer_price"] = offer_price
                    return self.send(reply)
                except SystemLimit as exc:
                    return self.send(store.end_system_limit(session,exc.reason,str(exc)))
                except Exception:
                    store.failed_request(session["id"], rid)
                    if config['case_study']:store.server_event(session,'agent_turn_failed',{'request_id':rid,'decision_committed':False})
                    raise
            if method == "POST" and path == "/api/participant/events":
                store.events(session, config, self.read_json().get("events"))
                return self.send({"ok": True})
            if method == "POST" and path == "/api/participant/decision":
                return self.send(store.decide(session, config, self.read_json()))
            if method == "POST" and path == "/api/participant/withdraw":
                self.read_json()
                return self.send(store.withdraw(session))
        self.send({"error": "Not found"}, 404)

    def researcher_route(self, method, path, query, account):
        store = self.server.store
        if method == "POST" and path == "/api/admin/session/ai-assessment":
            return self.send(usage_evidence.assess(store, account, self.read_json(12000)))
        if method == "POST" and path == "/api/admin/session/edit":
            data = self.read_json(16000)
            store.edit_session(data.get("session_id"), account, label=data.get("label", ""), note=data.get("note", ""),
                               restore=data.get("restore", False), expected_edit_id=data.get("expected_edit_id"))
            return self.send({"ok": True})
        if method == "GET" and path == "/api/admin/setup-chats":
            chat_id = parse_qs(query, keep_blank_values=True).get("id", [None])[0]
            return self.send(setup_chat.list_chats(store, account["id"]) if chat_id is None
                             else setup_chat.get_chat(store, account["id"], chat_id))
        if method == "POST" and path == "/api/admin/setup-chats/ask":
            return self.send(setup_chat.ask(store, account["id"], self.read_json(6_000_000)))
        if method == "GET" and path == "/api/admin/account":
            return self.send(account)
        if method == "GET" and path == "/api/admin/account/settings":
            config = store.current()["config"]
            return self.send({**accounts.activity_counts(store, account["id"]),
                              "workspace": {"study": config["name"], "status": config["status"],
                                            "provider": config["architecture"]["provider"],
                                            "model": config["architecture"]["model"],
                                            "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
                                            "budget": store.budget()}})
        if method == "POST" and path == "/api/admin/account/preferences":
            return self.send(accounts.update_preferences(store, account, self.read_json(8192)))
        if method == "POST" and path == "/api/admin/account/sign-out-others":
            self.read_json(8192)
            if account["username"] is None:
                raise PermissionError("Create an owner account before managing sign-ins.")
            count = accounts.revoke_other_logins(store, account, self.researcher_cookie())
            return self.send({"revoked": count})
        if method == "POST" and path == "/api/admin/account/appearance":
            return self.send({"theme": accounts.set_theme(store, account, self.read_json(8192).get("theme"))})
        if method == "POST" and path == "/api/admin/account/claim":
            if account["id"] != 1 or account["username"] is not None:
                raise PermissionError("Only the workspace owner can set up the owner account.")
            data = self.read_json(8192)
            token = accounts.claim_owner(store, data.get("username"), data.get("password"))
            return self.send({"ok": True}, extra_headers=self.login_cookie(token))
        if method == "POST" and path == "/api/admin/account/password":
            if account["username"] is None:
                raise PermissionError("Create an owner account before changing the password.")
            data = self.read_json(8192)
            token = accounts.change_password(store, account, data.get("current_password"), data.get("password"))
            return self.send({"ok": True}, extra_headers=self.login_cookie(token))
        if path in ("/api/admin/account/researchers", "/api/admin/account/access"):
            if account["role"] != "owner" or account["username"] is None:
                raise PermissionError("Only the owner can manage researcher accounts.")
            if method == "GET" and path.endswith("/researchers"):
                return self.send(accounts.list_researchers(store))
            if method == "POST":
                data = self.read_json(8192)
                if path.endswith("/researchers"):
                    return self.send(accounts.add_researcher(store, data.get("username"), data.get("password")), 201)
                accounts.set_disabled(store, data.get("id"), data.get("disabled"))
                return self.send({"ok": True})
        if method == "GET" and path == "/api/admin/chats":
            chat_id = parse_qs(query, keep_blank_values=True).get("id", [None])[0]
            if chat_id is None:
                return self.send(chat_history.list_chats(store, account["id"]))
            return self.send(chat_history.get_chat(store, account["id"], chat_id))
        if method == "GET" and path == "/api/admin/chats/export":
            return self.send(chat_history.export_chats(store, account["id"]),
                             extra_headers={"Content-Disposition": 'attachment; filename="session-chat-history.json"'})
        if method == "POST" and path.startswith("/api/admin/chats/"):
            data = self.read_json(6_000_000 if path == "/api/admin/chats/ask" else 12000)
            chat_id = data.get("chat_id")
            if path == "/api/admin/chats/ask":
                return self.send(chat_history.ask(store, account["id"], data))
            if path == "/api/admin/chats/page":
                return self.send(chat_history.page(store, account["id"], chat_id, data.get("request_id"), data.get("offset", 0)))
            if path == "/api/admin/chats/rename":
                chat_history.rename(store, account["id"], chat_id, data.get("title"))
                return self.send({"ok": True})
            if path == "/api/admin/chats/delete":
                chat_history.delete(store, account["id"], chat_id)
                return self.send({"ok": True})
        return self.send({"error": "Not found"}, 404)


def main():
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="Research Agent pilot server")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--data", default=os.environ.get("DATA_DIR", str(ROOT / "data")))
    args = parser.parse_args()
    local = args.host == "127.0.0.1"
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not local and (not admin_token or len(admin_token) < 32):
        parser.error("Remote deployment requires ADMIN_TOKEN with at least 32 characters")
    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not admin_token:
        key_file = data_dir / "admin.key"
        if not key_file.exists():
            fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        admin_token = key_file.read_text().strip()
    origins = os.environ.get("FRAME_ORIGINS", "").split()
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or "*" in origin or parsed.username:
            parser.error("FRAME_ORIGINS must be exact HTTPS origins separated by spaces")
    store = Store(data_dir / "study.sqlite3")
    store.recover_pending()
    server = AppServer((args.host, args.port), store, admin_token, os.environ.get("INTEGRATION_KEY", ""), local, origins)
    print(f"Research Agent ready at http://{args.host}:{server.server_port}", flush=True)
    print("Local workspace access enabled." if local else "Use HTTPS through a reverse proxy before enrolling participants.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
