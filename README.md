# Chatbot

React (Vite) frontend, Flask backend, MySQL database.

```
chatbot/
  db.sh        start/stop/status the local MySQL (portable build in ~/mysqlportable)
  backend/     Flask API — app.py (routes), models.py (tables), bot.py (the reply logic)
  frontend/    React app — src/App.jsx, src/api.js
```

## Run it

Three terminals, from this folder:

```bash
# 1. Database (MySQL on 127.0.0.1:3306; creates the `chatbot` db + user on first run)
bash db.sh

# 2. Backend (http://127.0.0.1:5000)
cd backend
.venv/Scripts/python app.py

# 3. Frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
npm run dev
```

`bash db.sh status` and `bash db.sh stop` check or stop MySQL.

## Fresh setup on another machine

```bash
cd backend && py -3 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && cp .env.example .env
cd ../frontend && npm install
```

Tables are created automatically when the backend starts.

## API

Conversation ids are UUID strings. Every message is saved with its conversation id.

| Method | Path | |
|---|---|---|
| GET | /api/health | backend + DB check |
| GET | /api/conversations | list chats, newest activity first |
| POST | /api/conversations | new chat |
| GET | /api/conversations/:id | `{ conversation, messages }`: the chat and its full history |
| PATCH | /api/conversations/:id | `{ "title": "..." }` rename |
| DELETE | /api/conversations/:id | delete chat and its messages |
| GET | /api/conversations/:id/messages | messages in a chat, oldest first |
| POST | /api/conversations/:id/messages | `{ "content": "..." }` → saves it, returns the whole reply. 502 if Claude fails (the user message stays saved) |
| POST | /api/conversations/:id/messages/stream | same, streamed as Server-Sent Events (below) |
| POST | /api/conversations/:id/regenerate | replace the last reply; the old one stays if Claude fails |

Stream events, in order:

| event | data |
|---|---|
| `user_message` | the saved user message |
| `delta` | `{ "text": "..." }`, repeated as Claude writes |
| `done` | `{ "reply", "conversation" }` once the reply is saved |
| `error` | `{ "error": "..." }` instead of `done`; no reply is saved |

After a chat's first reply, a background job asks Claude for a short title. A title the user set in the meantime is kept.

## The bot

The model is **local Claude Code**, one fresh `claude -p` process per call, so every call is stateless.
The backend sends the conversation with each call.

- `backend/llm.py` starts Claude with tools, skills, MCP servers and the owner's settings switched off, and streams its reply. Never add `--safe-mode`: it also blocks the tool servers phase 2 needs.
- `backend/prompt.py` builds the prompt: the last `RECENT_MESSAGES` messages word for word (phase 2 adds memory).
- `backend/bot.py` is what the routes call: `stream_reply`, `generate_reply`, `make_title`.
- Settings are in `backend/.env` (see `.env.example`).
- Logs show each call's timing: `[chat] model sonnet: first words after 3.5s, finished in 6.9s ...`.

## Tests

```bash
cd backend
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

Tests use SQLite and a fake Claude (`tests/fake_claude.py`). They never start the real one.

## Database upgrades

`backend/schema.py` runs on every start and upgrades older layouts in place (e.g. numeric conversation ids → UUIDs). Each step checks first, so it is safe to rerun.
