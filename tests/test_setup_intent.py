import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_agent.config import PINNED_MODEL
from research_agent.providers import ProviderError
from research_agent.setup_assistant import draft_setup, requested_case
from research_agent.store import Store
from research_agent.workspace_assistant import _provider_change


class SetupIntentTests(unittest.TestCase):
    def test_provider_questions_and_negations_do_not_change_a_draft(self):
        for question in ("Does it use OpenAI API?", "How do I change it to OpenAI?", "Don't change it to OpenAI.",
                         "Can you explain how to switch to OpenAI?", "Use OpenAI or demo?", "Change the saved study to OpenAI.",
                         "Make a new AI supplier using OpenAI."):
            with self.subTest(question=question):
                self.assertIsNone(_provider_change(question))
        for command in ("Can you change it to openAI?", "Switch the draft to OpenAI.", "Please use OpenAI API instead."):
            with self.subTest(command=command):
                self.assertEqual(_provider_change(command), "openai")
        self.assertEqual(_provider_change("Switch it to demo responses."), "demo")
    def test_explicit_supplier_buyer_request_does_not_select_a_forecasting_game(self):
        for brief in (
            "Can you make me an AI supplier and human buyer agent?",
            "The AI acts as the supplier and the participant is the buyer.",
            "Human buyer negotiates with an AI supplier.",
            "I did not ask for the Ozer game setup, but rather AI supplier and human buyer.",
            "Not Ozer. I want a human buyer and an AI supplier.",
        ):
            with self.subTest(brief=brief):
                self.assertEqual(requested_case(brief), "negotiation")

    def test_role_words_do_not_override_an_explicit_game_or_supplier_advisor(self):
        for brief in (
            "Use the Ozer game with an AI supplier and human buyer.",
            "Create an AI supplier selection assistant for a human buyer.",
            "An AI supplier advisor helps a human buyer compare options.",
            "An AI advises the human buyer about supplier selection.",
            "Rename this setup.",
        ):
            with self.subTest(brief=brief):
                self.assertIsNone(requested_case(brief))

    def test_an_incompatible_provider_draft_is_never_saved(self):
        wrong = {"case":"ozer_game@v1", "name":"Wrong game", "description":"Not requested.",
                 "assignment":"random", "memory":"none", "telemetry":False,
                 "conditions":[], "review":[]}
        response = {"status":"completed", "model":PINNED_MODEL,
                    "output":[{"type":"message", "content":[{"type":"output_text", "text":json.dumps(wrong)}]}]}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"study.sqlite3")
            with patch.dict(os.environ, {"OPENAI_API_KEY":"test-only"}), patch(
                "research_agent.setup_assistant.request_openai", return_value=response
            ):
                with self.assertRaises(ProviderError):
                    draft_setup(store, {"description":"AI supplier and human buyer"})
            self.assertEqual(store.setup_generations(), [])

    def test_uploaded_protocol_can_explicitly_choose_ozer(self):
        response = {"status":"completed", "model":PINNED_MODEL,
                    "output":[{"type":"message", "content":[{"type":"output_text", "text":json.dumps({
                        "case":"unsupported", "name":"", "description":"", "assignment":"random", "memory":"none",
                        "telemetry":False, "conditions":[], "review":["Fixture for catalog inspection."]})}]}]}
        document = {"name":"protocol.txt", "data":base64.b64encode(
            b"Ozer game protocol: AI supplier and human buyer exchange forecasts and choose production.").decode()}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/"study.sqlite3")
            with patch.dict(os.environ, {"OPENAI_API_KEY":"test-only"}), patch(
                "research_agent.setup_assistant.request_openai", return_value=response
            ) as provider:
                draft_setup(store, {"description":"Create the agent described in this document.", "document":document})
                choices = provider.call_args.args[0]["text"]["format"]["schema"]["properties"]["case"]["enum"]
                self.assertIn("ozer_game@v1", choices)


if __name__ == "__main__":
    unittest.main()
