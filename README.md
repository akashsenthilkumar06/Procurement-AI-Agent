# Procurement AI Agent

Create AI agents for procurement studies, run participant sessions, and review their conversations and decisions.

## 1. Set up

Install **Python 3.11+**, open this project folder in VS Code, and run this in its terminal:

```sh
python3 scripts/setup.py
```

This installs the dependencies in `.venv` and creates `.env` if it doesn't already exist.

## 2. Add your API key

Open `.env` in the project folder and add your key from the [OpenAI API keys page](https://platform.openai.com/api-keys):

```dotenv
OPENAI_API_KEY=your-key-here
```

Save the file. The app loads it automatically each time it starts. Skip the key to use built-in demo responses. `.env` stays out of Git.

## 3. Start the app

```sh
.venv/bin/python -m research_agent.server
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Use this same command next time. Stop with **Ctrl+C**; restart after changing `.env`.

## Create and run a study

1. In **Session chat**, describe your agent—for example, “Create an AI supplier and human buyer agent using OpenAI.” Use **+** to attach a protocol. You can also build one in **Agent setup**.
2. Choose **Save draft & edit**, review the instructions and response engine, then **Test this setup**. Ask chat “Change it to OpenAI” to update a demo draft.
3. When ready, set the study to **Active**, save, and choose **Use for study → Share a session**.
4. Review conversations and decisions in **Sessions**, or ask **Session chat** about the recorded sessions. Study data is saved in `data/`.

[Setup, Windows, and troubleshooting](docs/SETUP.md) · [User guide](docs/USER_GUIDE.md) · [Deployment](docs/DEPLOYMENT.md)
