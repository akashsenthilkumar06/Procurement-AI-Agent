"""Prepare a local development environment without changing the global Python install."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PYTHON = (3, 11)


class SetupError(Exception):
    pass


def environment_python(directory):
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare_environment(directory):
    if directory.exists():
        if not directory.is_dir() or not (directory / "pyvenv.cfg").is_file():
            raise SetupError(
                f"{directory} exists but is not a virtual environment. "
                "Choose another directory with --venv PATH."
            )
        print(f"Using existing environment: {directory}", flush=True)
    else:
        print(f"Creating environment: {directory}", flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(directory)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SetupError(
                f"Could not create {directory}: {exc}. "
                "Check that this Python installation includes venv and ensurepip."
            ) from None
    python = environment_python(directory)
    if not python.is_file():
        raise SetupError(
            f"The interpreter in {directory} is missing. "
            "Use --venv PATH to create a fresh environment."
        )
    try:
        version = json.loads(subprocess.check_output(
            [str(python), "-c", "import json,sys; print(json.dumps(list(sys.version_info[:2])))"],
            cwd=ROOT, text=True,
        ))
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise SetupError(f"Could not check the interpreter in {directory}: {exc}") from None
    if tuple(version) < MINIMUM_PYTHON:
        raise SetupError(
            f"{directory} uses Python {'.'.join(map(str, version))}; Python 3.11+ is required. "
            "Run this script with Python 3.11+ and choose a fresh directory with --venv PATH."
        )
    if subprocess.run([str(python), "-m", "pip", "--version"], cwd=ROOT,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        checked_run([python, "-m", "ensurepip", "--upgrade"], "Preparing pip in the environment…")
    return python


def checked_run(command, description):
    print(description, flush=True)
    try:
        subprocess.run([str(part) for part in command], cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise SetupError(f"{description} failed (exit {exc.returncode}). Fix the error above and rerun setup.") from None


def prepare_local_settings():
    path = ROOT / ".env"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if not path.is_file():
            raise SetupError("The project's .env path is not a file. Move it before running setup.") from None
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
        destination.write((ROOT / ".env.example").read_text(encoding="utf-8"))
    print("Created .env. Add your OpenAI API key there to enable AI responses.", flush=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create/reuse a venv, install this project, and check its dependencies.")
    parser.add_argument("--run", action="store_true", help="start the application after setup")
    parser.add_argument("--venv", default=".venv", metavar="PATH", help="environment directory (default: .venv in the project)")
    args = parser.parse_args(argv)
    if sys.version_info[:2] < MINIMUM_PYTHON:
        raise SetupError("Python 3.11+ is required. Run setup with a Python 3.11 or newer interpreter.")
    directory = Path(args.venv).expanduser()
    if not directory.is_absolute():
        directory = ROOT / directory
    python = prepare_environment(directory)
    checked_run([python, "-m", "pip", "install", "--disable-pip-version-check", "--editable", ROOT],
                "Installing the project and dependencies from pyproject.toml…")
    checked_run([python, "-m", "pip", "check"], "Checking installed dependencies…")
    checked_run([python, "-c", "import sqlite3; from pypdf import PdfReader; from dotenv import load_dotenv; from research_agent.server import AppServer; print('PDF reader, .env support, SQLite, and application imports are ready.')"],
                "Checking application imports…")
    prepare_local_settings()
    command = [str(python), "-m", "research_agent.server"]
    print("\nSetup complete.", flush=True)
    if args.run:
        return subprocess.call(command, cwd=ROOT)
    display = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    print(f"Start the app with:\n  {display}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SetupError, OSError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
