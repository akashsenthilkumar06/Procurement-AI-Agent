"""HTTP checks for both Ozer roles. The enrolled-model tests intercept every provider call."""

import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from research_agent.accounts import legacy_login
from research_agent.config import PINNED_MODEL
from research_agent.ozer import expire_waiting_rounds
from research_agent.packs import study_config
from research_agent.providers import _agent_step_http
from research_agent.server import AppServer
from research_agent.store import Store, uid


def protocol_pdf(text):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })}),
    })
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 40 740 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = content
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class OzerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "test.sqlite3")
        self.server = AppServer(("127.0.0.1", 0), self.store, "test-admin", local_mode=True)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, body=None, token="test-admin", *, cookie=None):
        request = urllib.request.Request(
            self.origin + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Research-Request": "1",
                     "Authorization": "Bearer " + token},
        )
        if cookie:
            request.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def save(self, *, outside_ai_question=False, **architecture):
        config = study_config("ozer_game", "v1")
        config["architecture"].update(architecture)
        config["status"] = "active"
        if outside_ai_question:
            config["telemetry"]["outside_ai_question"] = True
        self.store.save(config, self.store.current()["revision"])

    def start(self, role, preview=True):
        launch = self.store.issue_launch("participant-" + role, "human_" + role, preview)
        return self.start_launch(launch)

    def start_launch(self, launch):
        code, result = self.request("/api/participant/start", {"launch_token": launch["launch_token"], "consent": True})
        self.assertEqual(code, 200, result)
        self.token = result["session_token"]
        self.session_id = result["session_id"]
        return self.view()

    def view(self):
        code, result = self.request("/api/participant/session", token=self.token)
        self.assertEqual(code, 200, result)
        return result

    def act(self, state, action, expected=200, **payload):
        code, result = self.request("/api/participant/ozer",
            {"action": action, "stage": state["ozer"]["stage"], "round_id": state["round_id"], **payload},
            self.token)
        self.assertEqual(code, expected, result)
        return result

    def reach_quiz(self, state):
        for stage in ("general", "instructions", "role"):
            self.assertEqual(state["ozer"]["stage"], stage)
            state = self.act(state, "continue")
        self.assertEqual(state["ozer"]["stage"], "quiz")
        return state

    def begin(self, state, role):
        state = self.reach_quiz(state)
        self.assertTrue(self.act(state, "quiz", answers={})["ozer"]["quiz_retry"])
        return self.act(state, "quiz", answers={"signal": "retailer", "paid": "paid_only", "decision": role})

    def test_only_test_sessions_can_skip_comprehension(self):
        self.save(provider="openai", model=PINNED_MODEL, temperature=0)
        quiz = self.reach_quiz(self.start("supplier"))
        decision = self.act(quiz, "skip_quiz")
        self.assertEqual(decision["round_id"], 1)
        self.assertEqual(decision["ozer"]["stage"], "decision")
        self.assertIn("retailer_report", decision["ozer"])
        self.assertEqual(self.act(quiz, "skip_quiz")["ozer"]["stage"], "decision")
        events = [e["type"] for e in self.store.export("events") if e["session_id"] == self.session_id]
        self.assertEqual(events.count("ozer_comprehension_skipped"), 1)
        self.assertNotIn("ozer_comprehension", events)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}):
            enrolled = self.reach_quiz(self.start("retailer", preview=False))
            self.act(enrolled, "skip_quiz", expected=403, preview=True)
            self.assertTrue(self.act(enrolled, "quiz", answers={})["ozer"]["quiz_retry"])
            self.assertEqual(self.view()["ozer"]["stage"], "quiz")
            answers = {"signal": "retailer", "paid": "paid_only", "decision": "retailer"}
            self.assertEqual(self.act(enrolled, "quiz", answers=answers)["ozer"]["stage"], "decision")

    def test_both_roles_complete_all_rounds_and_export_payoffs(self):
        # Previews stay keyless even when the saved config is set to the enrolled provider.
        self.save(provider="openai", model=PINNED_MODEL, temperature=0, outside_ai_question=True)
        for role in ("supplier", "retailer"):
            with self.subTest(role=role), patch("research_agent.ozer.draw", side_effect=lambda lo, hi: 224 if lo == 100 else -52) as draw:
                initial = self.start(role)
                self.assertEqual(initial["public"]["provider_mode"], "demo")
                self.assertNotIn("private_signal", initial["ozer"])
                state = self.begin(initial, role)
                profits = []
                for number in range(1, 24):
                    self.assertEqual(state["round_id"], number)
                    opened = state["ozer"]
                    self.assertEqual(opened["stage"], "decision")
                    self.assertGreater(opened["deadline_at"], opened["server_now"])
                    if role == "supplier":
                        self.assertEqual(opened["retailer_report"], 224)
                        self.assertNotIn("private_signal", opened)
                    else:
                        self.assertEqual(opened["private_signal"], 224)
                        self.assertNotIn("retailer_report", opened)
                    self.assertEqual(draw.call_count, number * 2 - 1)
                    if number == 1:
                        for invalid in (True, -1, 2.5, 476, "224"):
                            self.act(state, "submit", expected=400, value=invalid)
                        self.assertEqual(self.view()["ozer"]["deadline_at"], opened["deadline_at"])
                        self.assertEqual(self.request("/api/participant/decision", {}, self.token)[0], 404)
                        self.assertEqual(self.request("/api/participant/message", {}, self.token)[0], 404)
                    submitted = self.act(state, "submit", value=224)
                    outcome = submitted["ozer"]["feedback"]
                    self.assertEqual(outcome["demand"], 172)
                    self.assertEqual(outcome["sales"], 172)
                    self.assertEqual(outcome["profit"], 6000 if role == "supplier" else 8600)
                    self.assertEqual("private_signal" in outcome, role == "retailer")
                    self.assertNotIn("shock", outcome)
                    self.assertEqual(outcome["practice"], number <= 3)
                    if number == 4:
                        self.assertEqual(outcome["cumulative_profit"], outcome["profit"])
                    if number > 3:
                        profits.append(outcome["profit"])
                    if number == 1:
                        duplicate = self.act(state, "submit", value=1)
                        self.assertEqual(duplicate["ozer"]["feedback"], outcome)
                        self.assertEqual(draw.call_count, 2)
                    if number == 23:
                        self.assertEqual(submitted["status"], "completed")
                        self.assertEqual(self.view()["ozer"]["feedback"], outcome)
                        self.assertEqual(self.store.session_detail(self.session_id)["session"]["status"], "completed")
                        self.assertEqual(self.act(state, "submit", value=1)["ozer"]["feedback"], outcome)
                    state = self.act(submitted, "continue")
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["ozer"]["results"]["paid_rounds"], 20)
                code, report = self.request("/api/participant/ai-report", {"answer": "prefer_not"}, self.token)
                self.assertEqual((code, report["answer"]), (200, "prefer_not"))
                self.assertEqual(self.view()["usage_evidence"]["outside_ai_answer"], "prefer_not")
                self.assertEqual(state["ozer"]["results"]["paid_profit"], sum(profits))
                self.assertEqual(state["ozer"]["results"]["total_usd"], "7.4" if role == "supplier" else "8.44")
                admin = self.store.session_detail(self.session_id)
                self.assertEqual(len(admin["ozer_rounds"]), 23)
                self.assertEqual(admin["ozer_rounds"][0]["signal"], 224)
                self.assertEqual(admin["ozer_rounds"][0]["shock"], -52)
                events = [e for e in self.store.export("events") if e["session_id"] == self.session_id]
                self.assertTrue(all(e["dataset_version"] == "ozer_generated@v1" for e in events))
                self.assertEqual({e["round_id"] for e in events if e["type"] == "ozer_round_settled"}, set(range(1, 24)))
                self.assertEqual({e["model_snapshot"] for e in events}, {"deterministic-v1"})
                self.assertEqual(sum(e["type"] == "ozer_completed" for e in events), 1)
        self.assertEqual(self.store.export("provider_spend"), [])

    def test_enrolled_counterpart_gets_only_its_current_observation(self):
        self.save(provider="openai", model=PINNED_MODEL, temperature=0)
        records = []

        real_urlopen = urllib.request.urlopen

        def mock_open(request, *args, **kwargs):
            if request.full_url != "https://api.openai.com/v1/responses":
                return real_urlopen(request, *args, **kwargs)
            records.append(json.loads(request.data))
            return io.BytesIO(json.dumps({
                "status": "completed", "model": PINNED_MODEL,
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps({"answer": "242", "action": None})}
                ]}],
                "usage": {"input_tokens": 50, "output_tokens": 4},
            }).encode())

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}),
            patch("research_agent.ozer.agent_step", _agent_step_http),
            patch("research_agent.providers.urllib.request.urlopen", side_effect=mock_open),
            patch("research_agent.ozer.draw", side_effect=lambda lo, hi: 367 if lo == 100 else -3),
        ):
            for role in ("supplier", "retailer"):
                initial = self.start(role, preview=False)
                state = self.begin(initial, role)
                if role == "supplier":
                    self.assertEqual(state["ozer"]["retailer_report"], 242)
                    self.assertNotIn("367", json.dumps(state))
                self.act(state, "submit", value=241)
        self.assertEqual(len(records), 2)
        for body in records:
            self.assertEqual(body["model"], PINNED_MODEL)
            self.assertFalse(body["store"])
            self.assertEqual(len(body["input"]), 2)
            self.assertEqual(body["tools"], [])
        retailer_request, supplier_request = records
        self.assertEqual(json.loads(retailer_request["input"][1]["content"]), {"private_signal": 367})
        self.assertEqual(json.loads(supplier_request["input"][1]["content"]), {"retailer_report": 241})
        self.assertNotIn("367", json.dumps(supplier_request))
        self.assertEqual(len(self.store.export("provider_spend")), 2)

    def test_pdf_setup_chat_to_saved_preview_and_enrolled_game(self):
        protocol = "Ozer Game: 3 practice and 20 paid periods. Retailer reports F and supplier chooses Q."
        plan = {
            "case": "ozer_game@v1", "name": "Forecast protocol pilot", "description": "Compare the two roles.",
            "assignment": "random", "memory": "none", "telemetry": False,
            "conditions": [
                {"id": "human_supplier", "label": "Supplier participant", "objective": "Keep retailer decisions consistent.",
                 "prompt": "Report an integer forecast from the current signal."},
                {"id": "human_retailer", "label": "Retailer participant", "objective": "Balance excess stock and lost sales.",
                 "prompt": "Use the current report to choose production."},
            ],
            "review": [],
        }
        calls = []

        def fake_openai(body, timeout):
            drafting = body["text"]["format"]["name"] == "study_setup"
            calls.append(body)
            return {"status": "completed", "model": PINNED_MODEL,
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(
                        plan if drafting else {"answer": "242", "action": None})}]}]}

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}),
            patch("research_agent.setup_assistant.request_openai", side_effect=fake_openai),
            patch("research_agent.providers.request_openai", side_effect=fake_openai),
            patch("research_agent.ozer.agent_step", _agent_step_http),
            patch("research_agent.ozer.draw", side_effect=lambda lo, hi: 367 if lo == 100 else -3),
        ):
            current = self.store.current()
            cookie = "research_admin=" + legacy_login(self.store)
            code, reply = self.request("/api/admin/setup-chats/ask", {
                "request_id": uid(), "document": {"name": "Ozer protocol.pdf",
                                                    "data": base64.b64encode(protocol_pdf(protocol)).decode()},
            }, cookie=cookie)
            self.assertEqual(code, 200, reply)
            self.assertEqual(reply["turn"]["attachment_name"], "Ozer protocol.pdf")
            self.assertEqual(calls[0]["input"][-1]["content"], protocol)
            config = reply["turn"]["result"]["config"]
            self.assertEqual(config["status"], "draft")
            self.assertEqual(config["case_study"]["id"], "ozer_game")
            self.assertEqual(self.store.current(), current)

            slot_path = "/api/admin/setup-slots"
            code, slot = self.request(slot_path, {"label": config["name"], "config": config})
            self.assertEqual(code, 200, slot)
            self.assertEqual(self.request(f"{slot_path}?id={slot['id']}"), (200, slot))
            launch_path = "/api/admin/launch"
            request = {"slot_id": slot["id"], "expected_slot_revision": slot["revision"], "preview": True,
                       "condition_id": "human_supplier", "participant_id": "ozer-preview"}
            self.assertEqual(self.request(launch_path, {**request, "live_preview": True})[0], 400)
            code, launch = self.request(launch_path, request)
            self.assertEqual(code, 200, launch)
            demo = self.begin(self.start_launch(launch), "supplier")
            self.assertEqual(demo["ozer"]["retailer_report"], 367)
            self.assertEqual(len(calls), 1)  # File drafting was the only provider call.

            config["architecture"].update(provider="openai", model=PINNED_MODEL, temperature=0)
            too_short = {**config, "architecture": {**config["architecture"], "max_input_tokens": 1000}}
            code, rejected = self.request("/api/admin/builder-preview", {"config": too_short, "condition_id": "human_supplier"})
            self.assertEqual(code, 400)
            self.assertIn("input allowance", rejected["error"])
            code, slot = self.request(slot_path, {"id": slot["id"], "expected_revision": slot["revision"],
                                                  "label": slot["label"], "config": config})
            self.assertEqual(code, 200, slot)
            code, launch = self.request(launch_path, {**request, "expected_slot_revision": slot["revision"], "live_preview": True})
            self.assertEqual(code, 200, launch)
            code, consent = self.request("/api/participant/preview", {"launch_token": launch["launch_token"]})
            self.assertEqual(code, 200, consent)
            self.assertTrue(consent["preview"])
            self.assertEqual(consent["public"]["provider_mode"], "openai")
            preview = self.begin(self.start_launch(launch), "supplier")
            self.assertEqual(preview["ozer"]["retailer_report"], 242)
            self.assertNotIn("private_signal", preview["ozer"])
            self.assertEqual(json.loads(calls[1]["input"][-1]["content"]), {"private_signal": 367})
            self.assertIn(plan["conditions"][0]["objective"], calls[1]["input"][0]["content"])
            self.assertIn(plan["conditions"][0]["prompt"], calls[1]["input"][0]["content"])
            self.assertEqual(self.act(preview, "submit", value=250)["ozer"]["feedback"]["production"], 250)
            self.assertEqual(self.store.current(), current)

            config["status"] = "active"
            code, slot = self.request(slot_path, {"id": slot["id"], "expected_revision": slot["revision"],
                                                  "label": slot["label"], "config": config})
            self.assertEqual(code, 200, slot)
            code, activated = self.request("/api/admin/use-setup-slot", {
                "id": slot["id"], "expected_revision": slot["revision"], "expected_study_revision": current["revision"],
            })
            self.assertEqual(code, 200, activated)
            code, launch = self.request(launch_path, {"condition_id": "human_retailer", "participant_id": "ozer-enrolled"})
            self.assertEqual(code, 200, launch)
            state = self.begin(self.start_launch(launch), "retailer")
            self.assertFalse(state["preview"])
            for number in range(1, 24):
                self.assertEqual(state["round_id"], number)
                state = self.act(state, "submit", value=250)
                self.assertEqual(state["ozer"]["feedback"]["production"], 242)
                state = self.act(state, "continue")
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["ozer"]["results"]["paid_rounds"], 20)
            self.assertEqual(state["ozer"]["results"]["total_usd"], "9.84")
            for body in calls[2:]:
                self.assertEqual(json.loads(body["input"][-1]["content"]), {"retailer_report": 250})
                self.assertIn(plan["conditions"][1]["objective"], body["input"][0]["content"])
                self.assertIn(plan["conditions"][1]["prompt"], body["input"][0]["content"])
            self.assertEqual(len(calls), 25)
            self.assertEqual(len(self.store.session_detail(self.session_id)["ozer_rounds"]), 23)

    def test_expiration_keeps_incomplete_round_unpaid_and_hides_signal(self):
        self.save()
        state = self.begin(self.start("retailer"), "retailer")
        with patch("research_agent.ozer.now", return_value=state["ozer"]["deadline_at"]):
            expire_waiting_rounds(self.store)
            terminal = self.act(state, "submit", value=224)
        self.assertEqual(terminal["status"], "ended_system_limit")
        self.assertEqual(terminal["end_reason"], "ozer_decision_timeout")
        self.assertNotIn("private_signal", terminal["ozer"])
        self.assertEqual(self.store.export("decisions"), [])
        events = self.store.export("events")
        self.assertEqual(sum(e["type"] == "ozer_decision_timeout" for e in events), 1)
        self.assertFalse(any(e["type"] == "ozer_human_decision" for e in events))

    def test_concurrent_retry_does_not_duplicate_paid_model_call(self):
        self.save(provider="openai", model=PINNED_MODEL, temperature=0)
        ready = threading.Event()
        release = threading.Event()

        def mock(*args):
            ready.set()
            if not release.wait(5):
                raise AssertionError("Timed out waiting for duplicate submission")
            return {"calls": [], "final": {"answer": "200", "action": None}}, {
                "usage": {"input_tokens": 50, "output_tokens": 4}}

        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}), patch("research_agent.ozer.agent_step", side_effect=mock) as call:
            state = self.begin(self.start("retailer", preview=False), "retailer")
            results = []
            worker = threading.Thread(target=lambda: results.append(self.act(state, "submit", value=210)))
            worker.start()
            try:
                self.assertTrue(ready.wait(5))
                pending = self.act(state, "submit", value=333)
                self.assertEqual(pending["ozer"]["stage"], "counterpart")
                self.assertNotIn("private_signal", pending["ozer"])
            finally:
                release.set()
                worker.join()
            self.assertEqual(call.call_count, 1)
            self.assertEqual(results[0]["ozer"]["feedback"]["forecast"], 210)

    def test_withdrawal_wins_over_a_pending_counterpart_decision(self):
        self.save(provider="openai", model=PINNED_MODEL, temperature=0)
        ready = threading.Event()
        release = threading.Event()

        def mock(*args):
            ready.set()
            if not release.wait(5):
                raise AssertionError("Timed out waiting for withdrawal")
            return {"calls": [], "final": {"answer": "200", "action": None}}, {
                "usage": {"input_tokens": 50, "output_tokens": 4}}

        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}), patch(
            "research_agent.ozer.agent_step", side_effect=mock
        ):
            state = self.begin(self.start("retailer", preview=False), "retailer")
            results = []
            worker = threading.Thread(target=lambda: results.append(self.act(state, "submit", value=210)))
            worker.start()
            try:
                self.assertTrue(ready.wait(5))
                code, withdrawn = self.request("/api/participant/withdraw", {}, self.token)
                self.assertEqual(code, 200, withdrawn)
                self.assertEqual(withdrawn["status"], "withdrawn")
                self.assertNotIn("private_signal", withdrawn["ozer"])
            finally:
                release.set()
                worker.join()
            self.assertEqual(results[0]["status"], "withdrawn")
        self.assertEqual(self.store.export("decisions"), [])
        self.assertFalse(any(e["type"] == "ozer_counterpart_decision" for e in self.store.export("events")))

    def test_provider_timeout_has_a_distinct_terminal_reason(self):
        self.save(provider="openai", model=PINNED_MODEL, temperature=0)
        real_urlopen = urllib.request.urlopen

        def mock_open(request, *args, **kwargs):
            if request.full_url == "https://api.openai.com/v1/responses":
                raise urllib.error.URLError(TimeoutError("Timed out"))
            return real_urlopen(request, *args, **kwargs)

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "mock-only"}),
            patch("research_agent.ozer.agent_step", _agent_step_http),
            patch("research_agent.providers.urllib.request.urlopen", side_effect=mock_open),
        ):
            state = self.begin(self.start("retailer", preview=False), "retailer")
            result = self.act(state, "submit", value=210)
        self.assertEqual(result["status"], "ended_system_limit")
        self.assertEqual(result["end_reason"], "ozer_counterpart_timeout")
        self.assertEqual(self.store.export("decisions"), [])
        self.assertEqual(self.store.export("provider_spend")[0]["status"], "uncertain")
