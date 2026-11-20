import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup.py"
spec = importlib.util.spec_from_file_location("project_setup", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LocalSetupTests(unittest.TestCase):
    def test_setup_creates_a_private_env_file_and_preserves_later_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = "# Local settings\nOPENAI_API_KEY=\n"
            (root / ".env.example").write_text(template)
            with patch.object(installer, "ROOT", root):
                self.assertTrue(installer.prepare_local_settings())
                self.assertEqual((root / ".env").read_text(), template)
                if os.name != "nt":
                    self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
                (root / ".env").write_text("OPENAI_API_KEY=local-fixture-key\nPORT=8766\n")
                self.assertFalse(installer.prepare_local_settings())
                self.assertEqual((root / ".env").read_text(), "OPENAI_API_KEY=local-fixture-key\nPORT=8766\n")

    def test_an_env_directory_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").mkdir()
            with patch.object(installer, "ROOT", root):
                with self.assertRaisesRegex(installer.SetupError, "not a file"):
                    installer.prepare_local_settings()
            self.assertTrue((root / ".env").is_dir())

    def test_an_existing_non_environment_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "important files"
            directory.mkdir()
            original = directory / "keep.txt"
            original.write_text("keep this content")
            with self.assertRaisesRegex(installer.SetupError, "not a virtual environment"):
                installer.prepare_environment(directory)
            self.assertEqual(original.read_text(), "keep this content")
            self.assertEqual(list(directory.iterdir()), [original])

    def test_environment_with_spaces_is_created_and_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "environment with spaces"
            python = installer.prepare_environment(directory)
            config = (directory / "pyvenv.cfg").read_text()
            marker = directory / "keep.txt"
            marker.write_text("existing environment")
            self.assertEqual(installer.prepare_environment(directory), python)
            self.assertEqual((directory / "pyvenv.cfg").read_text(), config)
            self.assertEqual(marker.read_text(), "existing environment")
            state = json.loads(subprocess.check_output([str(python), "-c",
                "import json,sys; print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"], text=True))
            self.assertEqual(Path(state["prefix"]).resolve(), directory.resolve())
            self.assertNotEqual(state["prefix"], state["base"])

    def test_unsupported_bootstrap_python_is_rejected_before_environment_changes(self):
        with patch.object(installer.sys, "version_info", (3, 10, 0)), patch.object(installer, "prepare_environment") as prepare:
            with self.assertRaisesRegex(installer.SetupError, "Python 3.11"):
                installer.main([])
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
