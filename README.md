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

| Method | Path | |
|---|---|---|
| GET | /api/health | backend + DB check |
| GET | /api/conversations | list chats, newest first |
| POST | /api/conversations | new chat |
| DELETE | /api/conversations/:id | delete chat and its messages |
| GET | /api/conversations/:id/messages | messages in a chat |
| POST | /api/conversations/:id/messages | `{ "content": "..." }` → saves it, returns the bot reply |

## The bot

`backend/bot.py` holds `generate_reply(history)`. Right now it echoes. Replace it with a real model call; nothing else needs to change.
