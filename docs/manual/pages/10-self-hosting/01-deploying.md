---
title: Deploying CakeCRM
summary: Run your own CakeCRM, either with the one-click Railway template or on your own machine.
shots: []
---

CakeCRM is free and open source (AGPL-3.0), and you run your own copy. It needs a **PostgreSQL** database and nothing else: no Redis, no object store, no mail server. AI keys are optional and are entered in the app later, never as environment variables.

## Deploy to Railway

The quickest route is the **Deploy on Railway** button in the project README.

1. Click the button and set `AUTH_PASSWORD`, the first admin's starting password. It is the only input you provide.
2. Railway creates the PostgreSQL database and deploys CakeCRM.
3. Open your CakeCRM address and sign in. Optionally, add an AI provider in the app. See [AI setup](../07-baker/06-ai-setup.md).

The template supplies `DATABASE_URL`. `JWT_SECRET` (which signs sign-ins) and `ENCRYPTION_KEY` (which encrypts stored keys and tokens) are generated once and kept on the template's persistent volume, so a redeploy does not sign everyone out and stored credentials stay readable. Setting `JWT_SECRET` yourself is still recommended for production, because a secret you control survives the volume being recreated.

## Run it on your own machine

You need Python and Node.js. Then:

```
git clone https://github.com/wwilson1017/CakeCRM.git
cd CakeCRM
python run.py
```

`run.py` asks for an admin email and password, writes a `.env` file, installs dependencies, builds the app and starts it at `http://localhost:8000`. If PostgreSQL is not reachable and Docker is installed, it starts the bundled database with Docker Compose. Without Docker, point `DATABASE_URL` in `.env` at a PostgreSQL server you already have.

## Settings in the environment

| Variable | What it does |
|---|---|
| `AUTH_PASSWORD` | Starting password for the first admin. Required. |
| `ADMIN_EMAIL`, `ADMIN_NAME` | The first admin's sign-in email and name. Default email `admin@cakecrm.local`. |
| `DATABASE_URL` | The PostgreSQL connection string. |
| `JWT_SECRET` | Signs sign-in sessions. Generated and kept if left unset. |
| `ENCRYPTION_KEY` | Encrypts AI keys, the Gmail secret and other stored credentials. Keep it stable. |
| `AUTH_PASSWORD_RESET` | Emergency admin password reset. See [First admin and passwords](02-first-admin-and-passwords.md). |
| `HEARTBEAT_ENABLED` | Baker's scheduled background work. On by default on Railway, off for a local run. |
| `BACKEND_URL` | Your public address, if you run behind your own domain or proxy. Gmail's redirect URI is built from it, and ChatGPT and Claude sign in to it when people connect their own AI tool (it must be https for that). |

The full list, with comments, is in `.env.example` in the repository.

## Good to know

- Only the `backend/data/` folder survives a Railway redeploy; everything else lives in PostgreSQL.
- The Ollama provider is for your own hardware and is hidden on Railway.
- To contribute changes back, read `CONTRIBUTING.md`. Contributions are signed off under the DCO; there is no CLA.
