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
| GET | /api/conversations/:id | `{ conversation, messages, summary }`: the chat, its full history and its rolling summary |
| PATCH | /api/conversations/:id | `{ "title": "..." }` rename |
| DELETE | /api/conversations/:id | delete chat and its messages |
| GET | /api/conversations/:id/messages | messages in a chat, oldest first |
| POST | /api/conversations/:id/messages | `{ "content": "..." }` → saves it, returns the whole reply. 502 if Claude fails (the user message stays saved) |
| POST | /api/conversations/:id/messages/stream | same, streamed as Server-Sent Events (below) |
| POST | /api/conversations/:id/regenerate | replace the last reply; the old one stays if Claude fails |
| POST | /api/conversations/:id/regenerate/stream | same, streamed (events below, without `user_message`). If the chat ends in a message whose reply failed, it answers that message |

Stream events, in order:

| event | data |
|---|---|
| `user_message` | the saved user message |
| `tool` | `{ "name", "label" }` when Claude starts looking something up, e.g. "Searching your PDFs…" |
| `delta` | `{ "text": "..." }`, repeated as Claude writes |
| `done` | `{ "reply", "conversation" }` once the reply is saved |
| `error` | `{ "error": "..." }` instead of `done`; no reply is saved |

After a chat's first reply, a background job asks Claude for a short title. A title the user set in the meantime is kept.

Memory (facts about the user, shared by every chat):

| Method | Path | |
|---|---|---|
| GET | /api/memory/facts | every fact with live `score`, `tier` (pinned/known/archived), `locked`, `in_prompt`; `?tier=` filters |
| POST | /api/memory/facts | `{ "content": "..." }` add by hand: pinned at 1.0 and locked |
| GET | /api/memory/facts/:id | one fact plus `history` (why its score changed) |
| PATCH | /api/memory/facts/:id | `{ "content" }` reword, `{ "locked": true/false }` lock or unlock |
| DELETE | /api/memory/facts/:id | forget it |

PDFs (each belongs to one chat):

| Method | Path | |
|---|---|---|
| POST | /api/conversations/:id/documents | multipart upload, field `file`. Answers at once with the document in `processing`; 400 if it isn't a PDF, 413 if over `UPLOAD_MAX_MB` |
| GET | /api/conversations/:id/documents | the chat's PDFs with `status`, `stage`, `pages`, `one_line`, `error` |
| GET | /api/documents/:id | one PDF, plus its `summary` |
| GET | /api/documents/:id/file | the PDF itself |
| POST | /api/documents/:id/retry | try a failed PDF again (409 if it didn't fail) |
| DELETE | /api/documents/:id | delete the PDF, its file and its search index |

`status` goes `processing` → `ready`, or `failed` with a readable `error` (scanned or password-protected PDFs, broken files).
While processing, `stage` is `reading`, `indexing` (searchable page by page) or `summarizing` (fully searchable).
Poll the list every second or two to update a chip. Deleting a chat deletes its PDFs too.

`POST /api/mcp` is the tool server Claude itself calls during a reply. It only answers this machine, with a per-start secret.

## The web app

- Replies stream in as they're written. While Claude looks something up, a pill says what it's doing
  ("Searching your PDFs…", "Searching earlier messages…").
- **PDFs**: the + in the message box, or drag a PDF onto the chat. Each PDF gets a chip above the message box
  that goes Uploading → Reading → Indexing → Summarizing → "15 pages". Click a chip for its summary, to open
  the file, retry a failed one or remove it. Page citations in replies, like "attention.pdf, p. 8", are links
  that open the PDF at that page.
- **Memory** (sidebar, bottom): every fact grouped Pinned / Known / Archived with a confidence bar. Click a
  fact to see its history, edit it, lock it or forget it. Add facts by hand at the top.
- If a reply fails after your message was saved, Retry (or "Get a reply" when you come back later) answers
  it without sending the message twice.

## The bot

The model is **local Claude Code**, one fresh `claude -p` process per call, so every call is stateless.
The backend sends what's needed with each call: pinned facts, the chat's rolling summary, a list of the
chat's PDFs, and the recent messages word for word. Claude fetches anything else with tools.

- `backend/llm.py` starts Claude with built-in tools, skills, other MCP servers and the owner's settings switched off, and streams its reply. Never add `--safe-mode`: it also blocks our tool server.
- `backend/prompt.py` builds every prompt (chat, fact extraction, summary, title).
- `backend/mcp.py` is the tool server: `search_history`, `search_memory`, `remember_fact`, `forget_fact`,
  `search_pdfs`, `get_document_summary`, `read_pdf_pages`.
- `backend/documents.py` handles PDFs: saves the file to `storage/uploads/`, reads each page (pypdf), cuts the
  text into ~1,500-character overlapping chunks with page numbers, embeds them, and summarizes the whole PDF
  (sections in parallel, then one summary). All in the background; the upload answers at once.
- `backend/memory.py` holds facts, confidence scores and search. `backend/embeddings.py` makes search vectors locally (bge-small, ~65 MB, downloaded once into `backend/.models`).
- `backend/jobs.py` runs after each reply in the background: embeds the new messages, asks Claude (haiku) what to remember and applies it, and folds old messages into the summary.
- `backend/bot.py` is what routes and jobs call.
- Settings are in `backend/.env` (see `.env.example`).
- Logs show each call: `[chat] model sonnet: first words after 2.7s, finished in 5.4s ..., tools used: search_history`, and each memory change: `Fact 3 used: 0.70 -> 0.85`.

### Confidence scores

| Score | Tier | |
|---|---|---|
| ≥ 0.6 | pinned | sent with every message (about 500 tokens max; best first) |
| 0.3–0.6 | known | Claude finds it with `search_memory` |
| < 0.3 | archived | still kept and searchable |

New facts start at 0.7 (said clearly) or 0.45 (in passing). "Remember this" is 1.0 and locked.
Each use adds 0.15. Something Claude had to dig out of old messages becomes a fact at 0.65 (pinned);
a dug-up existing fact rises to at least 0.65. Facts lose 0.005 per active day (a day you chat) that
they go unused, so a clearly stated fact stays pinned about 3 weeks of use. Days off don't count. The
fade is worked out when read, so nothing runs nightly. Locked facts never fade. Nothing is deleted by
scoring.

## Tests

```bash
cd backend
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
```

Tests use SQLite, a fake Claude (`tests/fake_claude.py`) and fake embeddings. They never start the real Claude or download the model.

## Database upgrades

`backend/schema.py` runs on every start and upgrades older layouts in place (e.g. numeric conversation ids → UUIDs). Each step checks first, so it is safe to rerun.
