from contextlib import chdir, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_agent import server


class EnvironmentStartupTests(unittest.TestCase):
    def start(self, root, *, environment=None, arguments=()):
        output = io.StringIO()
        with (patch.object(server, "ROOT", root),
              patch.dict(os.environ, environment or {}, clear=True),
              patch("sys.argv", ["research-agent", *arguments]),
              patch.object(server, "AppServer") as factory,
              redirect_stdout(output)):
            server.main()
            loaded = dict(os.environ)
            factory.return_value.serve_forever.assert_called_once()
            factory.return_value.server_close.assert_called_once()
            return factory.call_args, loaded, output.getvalue()

    def test_project_env_is_loaded_before_server_settings_and_never_prints_the_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                'OPENAI_API_KEY="dotenv-fixture-key"\nPORT=9234\n'
                'INTEGRATION_KEY=integration-fixture\nFRAME_ORIGINS=https://survey.example.com\n')
            call, loaded, output = self.start(root)
            self.assertEqual(loaded["OPENAI_API_KEY"], "dotenv-fixture-key")
            self.assertEqual(call.args[0], ("127.0.0.1", 9234))
            self.assertEqual(call.args[3], "integration-fixture")
            self.assertEqual(call.args[5], ["https://survey.example.com"])
            self.assertTrue((root / "data" / "study.sqlite3").is_file())
            self.assertNotIn("dotenv-fixture-key", output)
            self.assertNotIn("integration-fixture", output)

    def test_process_environment_and_command_line_take_priority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text('OPENAI_API_KEY=file-fixture\nHOST=0.0.0.0\nPORT=9234\n')
            call, loaded, _ = self.start(root, environment={"OPENAI_API_KEY":"process-fixture", "PORT":"9345"},
                                         arguments=("--host", "127.0.0.1", "--port", "9456"))
            self.assertEqual(loaded["OPENAI_API_KEY"], "process-fixture")
            self.assertEqual(loaded["PORT"], "9345")
            self.assertEqual(call.args[0], ("127.0.0.1", 9456))

    def test_only_the_project_file_is_read_and_shell_expressions_are_not_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            other = Path(temporary) / "other folder"
            root.mkdir(); other.mkdir()
            marker = root / "should-not-exist"
            (root / ".env").write_text(f'OPENAI_API_KEY=project-fixture\nEXAMPLE=$(touch "{marker}")\n')
            (other / ".env").write_text('OPENAI_API_KEY=unrelated-fixture\n')
            with chdir(other):
                _, loaded, _ = self.start(root)
            self.assertEqual(loaded["OPENAI_API_KEY"], "project-fixture")
            self.assertIn("$(touch", loaded["EXAMPLE"])
            self.assertFalse(marker.exists())

    def test_missing_env_still_starts_without_an_api_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            call, loaded, _ = self.start(root)
            self.assertNotIn("OPENAI_API_KEY", loaded)
            self.assertEqual(call.args[0], ("127.0.0.1", 8765))


if __name__ == "__main__":
    unittest.main()
