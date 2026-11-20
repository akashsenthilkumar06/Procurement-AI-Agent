import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from research_agent import chat_history
from research_agent.config import DEFAULT, PINNED_MODEL
from research_agent.engine import respond
from research_agent.packs import study_config
from research_agent.server import AppServer
from research_agent.store import Store, uid


def query(scope="study", **changes):
    return {"view":"list", "scope":scope, "status":"all", "study":"", "condition":"", "participant":"", **changes}


def plan(intent, *, scope="study", subject=None, sessions=False, session_ids=(), setup_ids=(), **filters):
    subject = subject or ("draft" if intent == "draft" else "saved_agent" if setup_ids else "app" if intent == "help" else "sessions")
    return {"intent":intent, "subject":subject, "query":query(scope, **filters), "include_sessions":sessions,
            "session_ids":list(session_ids), "setup_ids":list(setup_ids)}


def provider_response(value):
    return {"status":"completed", "model":PINNED_MODEL, "usage":{"input_tokens":100, "output_tokens":50},
            "output":[{"type":"message", "content":[{"type":"output_text", "text":json.dumps(value)}]}]}


def draft_plan(name="Workspace supplier advisor"):
    return {"case":"supplier_selection@v1", "name":name, "description":"Study decisions with AI advice.",
            "assignment":"balanced", "memory":"session", "provider":"demo", "telemetry":False,
            "conditions":[{"id":"recommender", "label":"AI advisor", "objective":"Compare public supplier terms.",
                           "prompt":"Discuss cost and reliability using the assigned data."}], "review":["Review the group before launch."]}


def negotiation_plan():
    return {"case":"negotiation@v2", "name":"AI supplier · Human buyer",
            "description":"A human buyer negotiates unit prices with an AI supplier.",
            "assignment":"balanced", "memory":"session", "provider":"demo", "telemetry":False,
            "conditions":[{"id":"recommender", "label":"AI supplier", "objective":"Negotiate a supply agreement with the human buyer.",
                           "prompt":"Act as the supplier. Discuss the buyer's needs and exchange unit-price offers using the study's offer policy. The buyer makes the final decision."}],
            "review":["Uses the installed negotiation terms; review the order before testing."]}


def ozer_plan():
    config = study_config("ozer_game", "v1")
    return {"case":"ozer_game@v1", "name":"Ozer forecasting study", "description":"Ozer forecast-sharing game.",
            "assignment":"random", "memory":"none", "telemetry":False,
            "conditions":[{key:c[key] for key in ("id", "label", "objective", "prompt")} for c in config["conditions"]],
            "review":[]}


class WorkspaceChatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)/"study.sqlite3"
        self.store = Store(self.path)
        self.server = AppServer(("127.0.0.1",0), self.store, "workspace-admin", local_mode=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:"+str(self.server.server_port)
        self.cookie = ""
        self.cookie = self.request("/api/login", {"token":"workspace-admin"}, signed=False)[2]["Set-Cookie"].split(";",1)[0]
        self.environment = patch.dict(os.environ, {"OPENAI_API_KEY":"workspace-fixture"})
        self.environment.start()
        self.router_patch = patch("research_agent.workspace_assistant.request_openai")
        self.router = self.router_patch.start()
        self.draft_patch = patch("research_agent.setup_assistant.request_openai")
        self.drafter = self.draft_patch.start()

    def tearDown(self):
        self.router_patch.stop(); self.draft_patch.stop(); self.environment.stop()
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temporary.cleanup()

    def request(self, path, data=None, signed=True, cookie=None, token=None):
        headers = {"Content-Type":"application/json", "X-Research-Request":"1"}
        if signed:
            headers["Cookie"] = self.cookie if cookie is None else cookie
        if token:
            headers["Authorization"] = "Bearer "+token
        request = urllib.request.Request(self.origin+path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response), response.headers
        except urllib.error.HTTPError as response:
            return response.code, json.load(response), response.headers

    def ask(self, question, **changes):
        data = {"question":question, "request_id":uid(), "scope":"study", **changes}
        return data, self.request("/api/admin/chats/ask", data)

    def session(self, code, *, preview=False, completed=False):
        current = self.store.current()
        cfg = copy.deepcopy(DEFAULT)
        cfg.update(status="active", name="Reliability pilot")
        cfg["conditions"][0]["private_information"] = {"memo":"PRIVATE_CONFIGURATION_CANARY"}
        self.store.save(cfg, current["revision"])
        launch = self.store.issue_launch(code, "recommend", preview, "external")
        credentials = self.store.start(launch["launch_token"], True)
        session, config, condition = self.store.authenticate(credentials["session_token"])
        rid = uid()
        text = "PREVIEW_MESSAGE_CANARY" if preview else "RELIABILITY_MESSAGE_CANARY: reliable delivery matters more than the lowest price."
        self.store.begin_request(session["id"], rid, text)
        answer = self.store.finish_request(session["id"], rid, respond(config, condition, [], text))
        if completed:
            self.store.events(session, config, [{"id":uid(), "type":"recommendation_visible", "client_ms":10,
                "client_wall_ms":100, "page_id":"test", "payload":{"message_id":answer["id"]}}])
            self.store.decide(session, config, {"action":"accept", "option_id":answer["option_id"],
                "message_id":answer["id"], "client_ms":100, "page_id":"test"})
        return credentials

    def test_session_chat_drafts_saves_and_runs_an_agent_without_generate_button(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(draft_plan())
        before = len(self.store.setup_slots())
        body, (code, result, _) = self.ask("Make an AI agent setup that advises a buyer about suppliers.")
        self.assertEqual(code, 200, result)
        self.assertEqual(result["turn"]["result"]["kind"], "draft")
        config = result["turn"]["result"]["draft"]["config"]
        self.assertEqual(config["name"], "Workspace supplier advisor")
        self.assertEqual(len(self.store.setup_slots()), before)
        replay = self.request("/api/admin/chats/ask", body)
        self.assertEqual(replay[1], result)
        self.assertEqual(self.router.call_count, 1)
        self.assertEqual(self.drafter.call_count, 1)
        self.assertEqual(self.request("/api/admin/chats/ask", {**body,"question":"Different request"})[0], 409)
        code, saved, _ = self.request("/api/admin/setup-slots", {"label":config["name"], "config":config})
        self.assertEqual(code, 200, saved)
        code, launch, _ = self.request("/api/admin/launch", {"participant_id":"draft-preview", "preview":True,
            "condition_id":"recommender", "assignment_method":"external", "slot_id":saved["id"], "expected_slot_revision":saved["revision"]})
        self.assertEqual(code, 200, launch)
        code, credentials, _ = self.request("/api/participant/start", {"launch_token":launch["launch_token"],"consent":True}, signed=False)
        self.assertEqual(code, 200, credentials)
        code, reply, _ = self.request("/api/participant/message", {"request_id":uid(),"content":"Compare the suppliers."},
                                      signed=False, token=credentials["session_token"])
        self.assertEqual(code, 200, reply)
        self.assertTrue(reply["decision_action"])

    def test_follow_up_refines_the_same_draft_and_chat_history_reopens_it(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.side_effect = [provider_response(draft_plan()), provider_response(draft_plan("Revised advisor"))]
        _, (_, first, _) = self.ask("Create a supplier agent.")
        _, (code, second, _) = self.ask("Rename it Revised advisor.", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, second)
        context = self.drafter.call_args.args[0]["input"][-2]["content"]
        self.assertIn("Workspace supplier advisor", context)
        history = self.request("/api/admin/chats?id="+first["chat"]["id"])[1]
        self.assertEqual([t["result"]["draft"]["config"]["name"] for t in history["turns"]],
                         ["Workspace supplier advisor", "Revised advisor"])
        self.assertEqual(self.drafter.call_count, 2)
        exported = self.request("/api/admin/chats/export")[1]
        self.assertEqual(exported[0]["turns"][-1]["result"]["kind"], "draft")

    def test_provider_follow_up_reads_the_draft_instead_of_the_applied_study(self):
        current = copy.deepcopy(DEFAULT)
        current["architecture"].update(provider="openai", model=PINNED_MODEL, temperature=0)
        self.store.save(current, self.store.current()["revision"])
        self.router.return_value = provider_response(plan("draft", setup_ids=[1]))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (_, first, _) = self.ask("Can you make me an AI supplier and human buyer agent?")
        draft_id = first["turn"]["request_id"]
        self.assertEqual(first["turn"]["result"]["sources"], [])
        self.router.side_effect = [provider_response(plan("lookup", study="Supplier selection pilot")),
            provider_response({"answer":"This draft uses demo responses. OpenAI created the draft, but the participant agent is not using the OpenAI API. Select OpenAI in Study controls after saving it.",
                               "sources":["draft:"+draft_id]})]
        _, (code, followup, _) = self.ask("Does it use OpenAI API?", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, followup)
        result = followup["turn"]["result"]
        self.assertEqual((result["kind"], result["subject"]), ("answer", "draft"))
        self.assertEqual(result["query"], query())
        self.assertEqual(result["included_sessions"], 0)
        self.assertEqual(result["sources"][0]["kind"], "draft")
        self.assertEqual(result["sources"][0]["id"], draft_id)
        context = json.loads(self.router.call_args.args[0]["input"][-1]["content"])
        self.assertEqual(context["subject"], "draft")
        self.assertEqual(context["evidence"]["latest_draft"]["setup"]["provider"], "demo")
        self.assertEqual(context["evidence"]["latest_draft"]["setup"]["memory"], "session")
        self.assertEqual(context["workspace"]["provider"], "openai")
        self.assertEqual(context["evidence"]["saved_agents"], [])
        self.assertNotIn("workspace-fixture", json.dumps(context))
        self.assertEqual(self.drafter.call_count, 1)
        reopened = self.request("/api/admin/chats?id="+first["chat"]["id"])[1]
        self.assertEqual(reopened["turns"][-1]["result"], result)

    def test_provider_change_updates_the_draft_and_saves_a_real_api_configuration(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (_, first, _) = self.ask("Create an AI supplier and human buyer agent.")
        original = first["turn"]["result"]["draft"]["config"]
        ledger_before = self.store.budget()
        _, (code, changed, _) = self.ask("Can you change it to openAI?", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, changed)
        self.assertEqual(changed["turn"]["result"]["kind"], "draft")
        config = changed["turn"]["result"]["draft"]["config"]
        expected = copy.deepcopy(original)
        expected["architecture"].update(provider="openai", model=PINNED_MODEL, temperature=0, reasoning_effort=None)
        self.assertEqual(config, expected)
        self.assertEqual(self.router.call_count, 1)
        self.assertEqual(self.drafter.call_count, 1)
        self.assertEqual(self.store.budget(), ledger_before)
        self.assertEqual(self.store.current()["config"]["architecture"]["provider"], "demo")
        code, saved, _ = self.request("/api/admin/setup-slots", {"label":config["name"], "config":config})
        self.assertEqual(code, 200, saved)
        self.assertEqual(self.store.setup_slot(saved["id"])["config"]["architecture"]["provider"], "openai")
        code, launch, _ = self.request("/api/admin/launch", {"participant_id":"api-negotiation", "preview":True,
            "condition_id":"recommender", "assignment_method":"external", "slot_id":saved["id"], "expected_slot_revision":saved["revision"]})
        self.assertEqual(code, 200, launch)
        code, credentials, _ = self.request("/api/participant/start", {"launch_token":launch["launch_token"], "consent":True}, signed=False)
        self.assertEqual(code, 200, credentials)
        session, runtime_config, _ = self.store.authenticate(credentials["session_token"])
        self.assertEqual(runtime_config["architecture"]["provider"], "openai")
        self.assertEqual(session["model_snapshot"], PINNED_MODEL)
        _, (code, demo, _) = self.ask("Switch it to demo responses.", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, demo)
        self.assertEqual(demo["turn"]["result"]["draft"]["config"], original)
        reopened = self.request("/api/admin/chats?id="+first["chat"]["id"])[1]
        self.assertEqual([t["result"]["draft"]["config"]["architecture"]["provider"] for t in reopened["turns"]],
                         ["demo", "openai", "demo"])

    def test_a_broader_revision_can_preserve_the_openai_provider(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (_, first, _) = self.ask("Create an AI supplier and human buyer agent.")
        self.ask("Switch it to OpenAI.", chat_id=first["chat"]["id"])
        revised = negotiation_plan()
        revised["provider"] = "openai"
        revised["conditions"][0]["prompt"] = "Be patient and discuss the buyer's needs."
        self.drafter.return_value = provider_response(revised)
        _, (code, result, _) = self.ask("Make the supplier more patient.", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, result)
        self.assertEqual(result["turn"]["result"]["draft"]["config"]["architecture"]["provider"], "openai")
        context = json.loads(self.drafter.call_args.args[0]["input"][-2]["content"].removeprefix("Previous study context: "))
        self.assertEqual(context["latest_draft"]["provider"], "openai")

    def test_an_initial_openai_request_builds_an_api_agent(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response({**negotiation_plan(), "provider":"openai"})
        _, (code, result, _) = self.ask("Create an AI supplier and human buyer agent using OpenAI.")
        self.assertEqual(code, 200, result)
        architecture = result["turn"]["result"]["draft"]["config"]["architecture"]
        self.assertEqual((architecture["provider"], architecture["model"], architecture["temperature"]),
                         ("openai", PINNED_MODEL, 0))

    def test_draft_follow_up_recovers_after_an_earlier_lookup(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (_, first, _) = self.ask("Create an AI supplier and human buyer agent.")
        self.router.return_value = provider_response(plan("lookup"))
        _, (_, lookup, _) = self.ask("Who hasn't completed?", chat_id=first["chat"]["id"])
        self.assertEqual(lookup["turn"]["result"]["kind"], "lookup")
        self.router.side_effect = [provider_response(plan("lookup")),
            provider_response({"answer":"The draft uses demo responses and session memory.",
                               "sources":["draft:"+first["turn"]["request_id"]]})]
        _, (code, result, _) = self.ask("Does it use OpenAI API?", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, result)
        self.assertEqual(result["turn"]["result"]["subject"], "draft")
        self.assertEqual(result["turn"]["result"]["kind"], "answer")

    def test_explicit_session_search_still_works_after_drafting(self):
        self.session("P-unfinished")
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (_, first, _) = self.ask("Create an AI supplier and human buyer agent.")
        self.router.return_value = provider_response(plan("lookup", status="active"))
        _, (code, result, _) = self.ask("Which sessions have not completed?", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, result)
        self.assertEqual(result["turn"]["result"]["kind"], "lookup")
        self.assertEqual(result["turn"]["result"]["total"], 1)
        self.assertEqual(result["turn"]["result"]["rows"][0]["participant_id"], "P-unfinished")
        self.assertEqual(self.router.call_count, 2)

    def test_a_named_saved_agent_is_distinct_from_the_conversation_draft(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (_, first, _) = self.ask("Create an AI supplier and human buyer agent.")
        slot = self.store.setup_slots()[0]
        self.router.side_effect = [provider_response(plan("analyze", setup_ids=[slot["id"]])),
            provider_response({"answer":"The saved current setup is a supplier selection agent.", "sources":["setup:"+str(slot["id"])]})]
        _, (code, result, _) = self.ask("What is the saved Current setup for?", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, result)
        self.assertEqual(result["turn"]["result"]["subject"], "saved_agent")
        context = json.loads(self.router.call_args.args[0]["input"][-1]["content"])
        self.assertIsNone(context["evidence"]["latest_draft"])
        self.assertEqual(context["evidence"]["saved_agents"][0]["id"], slot["id"])

    def test_ai_supplier_human_buyer_draft_runs_as_negotiation(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(negotiation_plan())
        _, (code, result, _) = self.ask("Can you make me an AI supplier and human buyer agent?")
        self.assertEqual(code, 200, result)
        config = result["turn"]["result"]["draft"]["config"]
        self.assertEqual(config["case_study"]["id"], "negotiation")
        self.assertEqual(config["negotiation"]["participant_role"], "buyer")
        self.assertEqual([(c["role"], c["agent"]["mode"]) for c in config["conditions"]], [("supplier", "recommender")])
        self.assertEqual(config["architecture"]["memory"], "session")
        schema = self.drafter.call_args.args[0]["text"]["format"]["schema"]
        self.assertEqual(schema["properties"]["case"]["enum"], ["negotiation@v2", "unsupported"])
        code, saved, _ = self.request("/api/admin/setup-slots", {"label":config["name"], "config":config})
        self.assertEqual(code, 200, saved)
        code, launch, _ = self.request("/api/admin/launch", {"participant_id":"negotiation-preview", "preview":True,
            "condition_id":"recommender", "assignment_method":"external", "slot_id":saved["id"], "expected_slot_revision":saved["revision"]})
        self.assertEqual(code, 200, launch)
        code, credentials, _ = self.request("/api/participant/start", {"launch_token":launch["launch_token"], "consent":True}, signed=False)
        self.assertEqual(code, 200, credentials)
        auth = self.store.authenticate(credentials["session_token"])
        view = self.store.session_view(*auth)
        self.assertEqual(view["public"]["negotiation"]["participant_role"], "buyer")
        code, reply, _ = self.request("/api/participant/message", {"request_id":uid(), "content":"What is your unit-price offer?"},
            signed=False, token=credentials["session_token"])
        self.assertEqual(code, 200, reply)
        self.assertEqual(reply["decision_action"], {"price":120})

    def test_rejecting_ozer_replaces_it_in_both_chat_entry_points(self):
        self.router.return_value = provider_response(plan("draft"))
        for setup_chat in (False, True):
            with self.subTest(setup_chat=setup_chat):
                self.drafter.side_effect = [provider_response(ozer_plan()), provider_response(negotiation_plan())]
                if setup_chat:
                    path = "/api/admin/setup-chats/ask"
                    code, first, _ = self.request(path, {"request_id":uid(), "description":"Create the Ozer game."})
                    self.assertEqual(code, 200, first)
                    code, second, _ = self.request(path, {"request_id":uid(), "chat_id":first["id"],
                        "description":"I did not ask for the Ozer game setup, but rather AI supplier and human buyer."})
                    result = second["turn"]["result"] if code == 200 else second
                else:
                    _, (code, first, _) = self.ask("Create the Ozer game.")
                    self.assertEqual(code, 200, first)
                    _, (code, second, _) = self.ask("I did not ask for the Ozer game setup, but rather AI supplier and human buyer.", chat_id=first["chat"]["id"])
                    result = second["turn"]["result"]["draft"] if code == 200 else second
                self.assertEqual(code, 200, result)
                self.assertEqual(result["config"]["case_study"]["id"], "negotiation")
                request = self.drafter.call_args.args[0]
                self.assertEqual(request["text"]["format"]["schema"]["properties"]["case"]["enum"], ["negotiation@v2", "unsupported"])
                context = json.loads(request["input"][-2]["content"].removeprefix("Previous study context: "))
                self.assertNotIn("latest_draft", context)
                self.assertNotIn("Ozer forecasting study", json.dumps(context))

    def test_document_upload_in_session_chat_is_used_but_only_filename_is_retained(self):
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(draft_plan())
        text = "UPLOAD_PROTOCOL_CANARY: procurement conditions.\n"*230
        document = {"name":"protocol.md", "data":base64.b64encode(text.encode()).decode()}
        body, (code, result, _) = self.ask("", document=document)
        self.assertGreater(len(json.dumps(body)), 12000)
        self.assertEqual(code, 200, result)
        self.assertIn("UPLOAD_PROTOCOL_CANARY", self.drafter.call_args.args[0]["input"][-1]["content"])
        self.assertEqual(result["turn"]["attachment_name"], "protocol.md")
        history = self.request("/api/admin/chats?id="+result["chat"]["id"])[1]
        self.assertNotIn("UPLOAD_PROTOCOL_CANARY", json.dumps(history))
        changed = {**body, "document":{**document,"data":base64.b64encode(b"different").decode()}}
        self.assertEqual(self.request("/api/admin/chats/ask", changed)[0], 409)
        self.assertEqual(self.drafter.call_count, 1)

    def test_analysis_reads_scoped_transcripts_and_outcomes_with_real_source_links(self):
        actual = self.session("recorded-buyer", completed=True)
        self.session("preview-buyer", preview=True)
        self.router.side_effect = [provider_response(plan("analyze", scope="preview", sessions=True)),
            provider_response({"answer":"The recorded buyer emphasized reliable delivery and accepted the recommendation.",
                               "sources":["session:"+actual["session_id"]]})]
        body, (code, result, _) = self.ask("What mattered to participants when they accepted the AI advice?")
        self.assertEqual(code, 200, result)
        answer = result["turn"]["result"]
        self.assertEqual(answer["query"]["scope"], "study")
        self.assertEqual(answer["included_sessions"], 1)
        self.assertEqual(answer["sources"][0]["id"], actual["session_id"])
        evidence = self.router.call_args.args[0]["input"][-1]["content"]
        self.assertIn("RELIABILITY_MESSAGE_CANARY", evidence)
        self.assertIn('"action":"accept"', evidence)
        for protected in ("PREVIEW_MESSAGE_CANARY", "preview-buyer", "PRIVATE_CONFIGURATION_CANARY", actual["session_token"], "token_hash", "clipboard_evidence"):
            self.assertNotIn(protected, evidence)
        self.request("/api/admin/chats/ask", body)
        self.assertEqual(self.router.call_count, 2)
        self.assertEqual(self.request("/api/admin/chats?id="+result["chat"]["id"])[1]["turns"][0]["result"], answer)

    def test_saved_agent_details_are_available_without_private_fields(self):
        config = copy.deepcopy(DEFAULT)
        config["conditions"][0]["objective"] = "AGENT_GOAL_CANARY: emphasize delivery reliability."
        config["conditions"][0]["private_information"] = {"memo":"DO_NOT_SEND_PRIVATE_AGENT_CANARY"}
        saved = self.store.save_setup_slot("Reliability advisor", config)
        self.router.side_effect = [provider_response(plan("analyze", setup_ids=[saved["id"]])),
            provider_response({"answer":"Reliability advisor emphasizes delivery reliability.","sources":["setup:"+str(saved["id"])]})]
        _, (code, result, _) = self.ask("What is the Reliability advisor configured to do?")
        self.assertEqual(code, 200, result)
        context = self.router.call_args.args[0]["input"][-1]["content"]
        self.assertIn("AGENT_GOAL_CANARY", context)
        self.assertNotIn("DO_NOT_SEND_PRIVATE_AGENT_CANARY", context)
        self.assertNotIn("private_information", context)
        self.assertEqual(result["turn"]["result"]["sources"][0]["kind"], "setup")

    def test_unknown_sources_are_rejected_and_requests_need_researcher_auth(self):
        self.router.side_effect = [provider_response(plan("help")),
            provider_response({"answer":"An invented source.","sources":["session:does-not-exist"]})]
        _, (code, result, _) = self.ask("Show me help.")
        self.assertEqual(code, 502, result)
        self.assertEqual(chat_history.list_chats(self.store, 1), [])
        self.assertEqual(self.request("/api/admin/chats/ask", {"request_id":uid(),"question":"Create an agent"}, signed=False)[0], 403)
        self.assertEqual(self.drafter.call_count, 0)

    def test_old_chat_rows_migrate_and_keep_live_lookup_results(self):
        self.session("recorded")
        chat_id, rid = uid(), uid()
        with self.store.db() as db:
            db.execute("DROP TABLE researcher_chat_turns")
            db.execute("CREATE TABLE researcher_chat_turns (id INTEGER PRIMARY KEY,chat_id TEXT NOT NULL,request_id TEXT NOT NULL,question TEXT NOT NULL,scope TEXT NOT NULL,query_json TEXT NOT NULL,UNIQUE(chat_id,request_id))")
            db.execute("INSERT INTO researcher_chats VALUES(?,1,'Older chat',0,0)", (chat_id,))
            db.execute("INSERT INTO researcher_chat_turns(chat_id,request_id,question,scope,query_json) VALUES(?,?,?,?,?)",
                       (chat_id,rid,"Who is still in progress?","study",json.dumps(query(status="active"))))
        reopened = Store(self.path)
        result = chat_history.get_chat(reopened, 1, chat_id)
        self.assertEqual(result["turns"][0]["result"]["total"], 1)
        self.assertEqual(result["turns"][0]["attachment_name"], "")
        self.router.assert_not_called()

    def test_scope_change_does_not_forward_previous_preview_conversation(self):
        self.router.return_value = provider_response(plan("lookup", scope="preview"))
        _, (_, first, _) = self.ask("PREVIEW_QUESTION_CANARY", scope="preview")
        self.router.return_value = provider_response(plan("lookup"))
        self.ask("Now show study sessions.", chat_id=first["chat"]["id"], scope="study")
        self.assertNotIn("PREVIEW_QUESTION_CANARY", self.router.call_args.args[0]["input"][-1]["content"])

    def test_analysis_reports_when_only_a_bounded_sample_was_read(self):
        for index in range(7):
            self.session("buyer-"+str(index))
        self.router.side_effect = [provider_response(plan("analyze", sessions=True)),
            provider_response({"answer":"The reviewed excerpts discuss reliability. This is a sample.", "sources":[]})]
        _, (code, result, _) = self.ask("What do the saved conversations discuss?")
        self.assertEqual(code, 200, result)
        answer = result["turn"]["result"]
        self.assertEqual(answer["matching_sessions"], 7)
        self.assertEqual(answer["included_sessions"], 6)
        self.assertTrue(answer["sample_limited"])
        self.assertEqual(len(answer["sources"]), 6)

    def test_concurrent_session_and_setup_requests_do_not_duplicate_model_work(self):
        entered, release = threading.Event(), threading.Event()
        def slow_model(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release the provider")
            return provider_response(plan("lookup"))
        self.router.side_effect = slow_model
        body = {"request_id":uid(), "question":"List my sessions.", "scope":"study"}
        replies = []
        worker = threading.Thread(target=lambda:replies.append(self.request("/api/admin/chats/ask", body)))
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.request("/api/admin/chats/ask", body)[0], 409)
            self.assertEqual(self.request("/api/admin/setup-chats/ask", {"request_id":uid(),"description":"Create an agent"})[0], 409)
        finally:
            release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(replies[0][0], 200, replies)
        self.assertEqual(self.request("/api/admin/chats/ask", body)[0], 200)
        self.assertEqual(self.router.call_count, 1)
        self.drafter.assert_not_called()

    def test_follow_up_can_read_the_records_returned_by_an_earlier_lookup(self):
        saved = self.session("followup-buyer", completed=True)
        body = {"request_id":uid(),"question":"Who completed?","scope":"study", "query":query(status="completed")}
        code, first, _ = self.request("/api/admin/chats/ask", body)
        self.assertEqual(code, 200, first)
        self.router.side_effect = [provider_response(plan("analyze", sessions=True, session_ids=[saved["session_id"]])),
            provider_response({"answer":"That buyer emphasized reliable delivery.","sources":["session:"+saved["session_id"]]})]
        _, (code, result, _) = self.ask("What did the first participant say?", chat_id=first["chat"]["id"])
        self.assertEqual(code, 200, result)
        route_context = json.loads(self.router.call_args_list[0].args[0]["input"][-1]["content"])
        self.assertEqual(route_context["history"][0]["lookup_result"]["rows"][0]["id"], saved["session_id"])

    def test_new_draft_and_analysis_history_stays_private_to_its_researcher(self):
        code, _, headers = self.request("/api/admin/account/claim", {"username":"owner","password":"owner-fixture-password"})
        self.assertEqual(code, 200)
        self.cookie = headers["Set-Cookie"].split(";",1)[0]
        self.request("/api/admin/account/researchers", {"username":"colleague","password":"colleague-fixture-password"})
        _, _, headers = self.request("/api/login", {"username":"colleague","password":"colleague-fixture-password"}, signed=False)
        other = headers["Set-Cookie"].split(";",1)[0]
        self.router.return_value = provider_response(plan("draft"))
        self.drafter.return_value = provider_response(draft_plan())
        body, (code, first, _) = self.ask("Create a supplier agent.")
        self.assertEqual(code, 200, first)
        chat_id = first["chat"]["id"]
        self.assertEqual(self.request("/api/admin/chats?id="+chat_id, cookie=other)[0], 404)
        self.assertEqual(self.request("/api/admin/chats/ask", {**body,"chat_id":chat_id,"request_id":uid()}, cookie=other)[0], 404)
        self.assertEqual(self.request("/api/admin/chats/ask", body, cookie=other)[0], 409)
        self.assertEqual(self.request("/api/admin/chats/export", cookie=other)[1], [])
        self.assertEqual(self.router.call_count, 1)
        self.assertEqual(self.drafter.call_count, 1)


if __name__ == "__main__":
    unittest.main()
