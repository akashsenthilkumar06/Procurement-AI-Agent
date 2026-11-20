# Setup reference

[Quick start](../README.md)

## First setup

With **Python 3.11+** installed, open the project folder in your terminal and run:

```sh
python3 scripts/setup.py
```

This creates or reuses `.venv`, installs the dependencies, checks PDF support and SQLite, and creates `.env` from `.env.example` if needed. It preserves an existing `.env`. The app does not need Node.js or npm.

On Windows, use `py -3 scripts/setup.py`. Internet access is needed to download dependencies on the first installation.

## Add your OpenAI key

Create a key on the [OpenAI API keys page](https://platform.openai.com/api-keys). Open `.env` in the project folder, add the key, and save:

```dotenv
OPENAI_API_KEY=your-key-here
```

The server loads this file automatically at startup, including when started from another folder. Existing process environment variables take priority. `.env` is excluded from Git and Docker builds.

Live agent responses, AI drafting, and typed Session chat questions need a key. Demo responses and suggested session queries work without one. Select OpenAI in the agent's Study controls, or ask Session chat to change a draft to OpenAI, then save the setup.

## Start the app

On macOS or Linux:

```sh
.venv/bin/python -m research_agent.server
```

On Windows (PowerShell):

```powershell
.\.venv\Scripts\python.exe -m research_agent.server
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Stop with **Ctrl+C**. Restart after editing `.env` or backend code. Run setup again when dependencies change. Study data is saved in `data/`.

You can also install dependencies and start the app in one command: `python3 scripts/setup.py --run` (Windows: `py -3 scripts/setup.py --run`).

## Optional settings

The comments in [`.env.example`](../.env.example) list settings such as `PORT`, `DATA_DIR`, and `SERVER_SPEND_CAP_USD`. To use another port, add `PORT=8766` to `.env` and restart. Command-line flags take priority over these defaults.

For a separate environment directory, use `python3 scripts/setup.py --venv /path/to/environment`; start the app with that environment's Python.

In VS Code, install Microsoft's Python extension and choose **Python: Select Interpreter → .venv**. New integrated terminals activate it automatically. The commands above also work without activation.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Python is older than 3.11 | Run setup with a newer interpreter, such as `python3.13 scripts/setup.py`. |
| `.venv` has an old or broken interpreter | Use `--venv` with a fresh directory. Setup preserves existing environments. |
| Missing `pypdf` or `dotenv` | Run `python3 scripts/setup.py`, then restart with the project's `.venv` Python. |
| Dependency download fails | Check the connection or package-index configuration and rerun setup. |
| Python cannot create the environment | Install the `venv`/`ensurepip` support for your Python distribution. |
| API key is not detected | Check that the file is named `.env`, contains `OPENAI_API_KEY=...`, and sits beside `pyproject.toml`. Restart the server. |
| Address/port already in use | Stop the earlier server or set another `PORT` in `.env`. |
| PDF has no readable text | Use a text-based PDF, DOCX, TXT, or Markdown file. Scanned PDFs need OCR first. |

Attachments are limited to 4 MB. PDFs must be unlocked and at most 30 pages; extracted text is also bounded. Short excerpts work best.
