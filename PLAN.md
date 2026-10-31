# Personal chatbot: final plan

A local, personal chatbot. Normal conversation like a web LLM, PDF upload with
answers and summaries from the PDFs, and memory that does not degrade as a chat
gets long. Runs entirely on this machine. The LLM is local Claude Code in
stateless print mode.

## Decisions

| Area | Choice |
|---|---|
| App | Flask backend (`backend/`) + React (Vite) frontend (`frontend/`), run locally |
| Database | Local portable MySQL on 127.0.0.1:3306 (`db.sh`), database `chatbot` |
| Vector search | No vector extension. Embeddings stored as packed float32 blobs; cosine similarity computed in Python (numpy), with an in-memory cache |
| Embeddings | Local model (`bge-small-en-v1.5`, 384 dims, via `fastembed`/ONNX). Downloaded once, then runs offline |
| PDF text | `pypdf`, text extracted per page |
| File storage | Local disk: `chatbot/storage/uploads/<document-id>.pdf` (git-ignored) |
| LLM | `claude -p`, one fresh process per call, no saved session |
| Tool calling | Our own MCP server, served by Flask at `/api/mcp`, passed to each call with `--mcp-config` |

## Status

- **Phase 1 (chat and saving): done, backend only.** UUID conversation ids, every
  message saved, local Claude replies (plain and streamed), history fetch, rename,
  background titles. 34 tests. Live-tested with real Claude on 2026-10-31.
- **Phase 2 (memory): done, backend only.** Facts with confidence scores, promotion of dug-up details,
  rolling summary, local embeddings, the tool server (search_history, search_memory, remember_fact,
  forget_fact), memory API. 75 tests. Live-tested with real Claude on 2026-10-31.
- Phase 3 (PDFs): next.

## The LLM adapter (`backend/llm.py`)

Every model call goes through one function. It spawns:

```
claude -p
  --setting-sources local          # no user/project settings, hooks or CLAUDE.md
  --disable-slash-commands         # no skills
  --tools ""                       # no built-in tools (no shell, files, web)
  --strict-mcp-config              # ignore every other MCP server
  --mcp-config <per-call JSON>     # only our tools, scoped to this conversation
  --allowedTools mcp__rag__*       # our tools run without permission prompts
  --no-session-persistence         # nothing saved; every call is stateless
  --system-prompt <ours>
  --output-format stream-json --include-partial-messages
  --model sonnet                   # haiku for background jobs
```

The prompt goes in on stdin. Tested on 2026-10-31: calls are stateless, Claude
calls our tools and cites pages, and it does not see the user's CLAUDE.md.

Note: `--safe-mode` must NOT be used. It also blocks `--mcp-config` servers.

Known side effect: Claude Code still adds the account email, Windows paths and
OS version to each call. Accepted, because this bot is only for the owner.

Swapping to the Anthropic API later means rewriting only this file.

## Conversations and history

- Every conversation gets a **conversation id** (a UUID) when it is created.
  Every API call about a chat uses that id.
- **Every message is saved** to MySQL in full, with its conversation id,
  role (user/assistant), and time. Nothing is ever deleted by the memory system.
- The full history of any conversation can be fetched by its id, in order.
- Conversations can be listed (newest first), renamed and deleted by the user.
- The title is generated from the first message (background job).

## Memory: what each turn sends

Rebuilt from the database on every call. The size stays flat however long the chat gets.

1. System prompt
2. **Pinned facts** about the user (all of them; small)
3. **Rolling summary** of this chat's older messages
4. **List of this chat's PDFs**: name, page count, one-line summary
5. **Recent messages** word for word (last ~12)
6. The new message

Everything else is fetched by Claude through tools, only when needed.

## How facts are chosen and scored

### What becomes a fact

Test: "will this still matter in a future conversation?" Only things the user
said or confirmed, never the model's guesses about them.

- Yes: identity, preferences, ongoing goals/projects, decisions, constraints, corrections.
- No: small talk, one-off questions, the model's own explanations (unless agreed
  as a decision), PDF content (searchable anyway).

### Confidence score (0 to 1)

Every fact has a score. It decides whether the fact is sent every turn.

| Score | Tier | Behaviour |
|---|---|---|
| ≥ 0.6 | **Pinned** | Sent in every prompt |
| 0.3 – 0.6 | **Known** | Not sent; found by `search_memory` when relevant |
| < 0.3 | **Archived** | Kept in the database, still searchable, hidden in the panel by default |

Nothing is ever deleted by scoring. Only the user deletes.

Starting score:
- Stated clearly ("I'm a backend developer") → 0.7
- Mentioned in passing → 0.45
- "Remember this" → 1.0 and **locked** (never decays; user can unlock)

Goes up (the user keeps asking about it):
- Each later exchange that refers to the fact or relies on it → +0.15, capped at 1.0

Goes down (the user stops asking about it):
- Each active day (a day the user chats at all) where the fact never comes up → −0.005 (corrected from −0.03, which would
  have unpinned a clear fact after only 4 active days)
- Calendar days off do not count, so a week away does not erase memory.
- Locked facts do not decay.

Rough feel: a clearly stated fact stays pinned for about 3 weeks of active use
without coming up, then drops to Known. One mention puts it back.

Pinned facts have a size budget (~500 tokens). If more than fit, the highest
scores win and the rest behave as Known.

### Promotion: something unimportant that the user asks about

If Claude has to dig something out with `search_history` and it was not a fact
yet, the background job turns it into a fact at **0.65**, so it is pinned
straight away. From then on normal scoring applies: ask again and it rises,
never ask again and it fades back to Known.

If the dug-up thing was already a Known or Archived fact, it jumps to at least
0.65 the same way.

### How "used" is detected

The background fact job gets:
- the latest exchange,
- the current facts with their ids,
- the tool calls made this turn (which history messages or facts were fetched).

It returns `add`, `update`, `delete`, `used: [fact ids]` and
`promote: [message ids]`. The app applies the score changes. Decay is worked out when a score is read (stored score minus 0.005 per idle active day since it was last touched), so no job runs at
the start of each active day.

Every score change is written to `fact_events`, so the memory panel can show
why a score moved ("used 4 times, last today").

## Tools Claude can call (`/api/mcp`)

| Tool | What it does |
|---|---|
| `search_pdfs(query, document?)` | Top matching passages from this chat's PDFs, with file name and page numbers |
| `get_document_summary(document)` | The stored summary of a whole PDF |
| `read_pdf_pages(document, from, to)` | Exact text of specific pages (capped length) |
| `search_history(query)` | Older messages from this chat that match, word for word |
| `search_memory(query)` | Known and Archived facts that match (pinned ones are already in the prompt) |
| `remember_fact(fact)` | Lets Claude pin a fact when the user says "remember this" |

Each call's tool URL carries the conversation id plus a random per-process
secret, and the endpoint only listens on 127.0.0.1.

## Background jobs (after the reply, user never waits)

- **Embed** the new user and assistant messages.
- **Extract and score facts** (haiku, `--json-schema`): add new facts, replace
  contradicted ones, raise the score of facts that were used, promote things
  dug out of history. See "How facts are chosen and scored".
- **Roll the summary**: when messages older than the recent window pass a size
  threshold, fold them into the chat summary (old summary + those messages → new
  summary).

## PDF pipeline

1. Upload → save to `storage/uploads/`, create a `documents` row (status `processing`).
2. Extract text page by page.
3. Chunk to ~800 tokens with overlap, keeping page ranges.
4. Embed each chunk, store in `chunks`.
5. Summarize the whole document (map-reduce: section summaries → one summary).
6. Status `ready`. The UI chip turns from "processing" to "ready".

Scanned PDFs (images, no text layer) are out of scope for now. They would need OCR.

## Database (`chatbot` on MySQL, 127.0.0.1:3306)

```
conversations(id char(36) uuid, title, summary, summarized_upto_id, created_at, updated_at)
messages(id, conversation_id, role, content, embedding blob, created_at)
facts(id, content, category, score real, locked bool, origin,             -- global, about the user
      use_count, last_used_at, source_message_id, embedding blob,       -- origin: extracted | remembered | promoted
      created_at, updated_at)
fact_events(id, fact_id, kind, delta, score_after, message_id, created_at) -- kind: created | used | promoted | edited | locked | unlocked
activity_days(day date primary key)                                        -- days the user chatted; drives decay
documents(id, conversation_id, filename, stored_path, pages, status, summary, created_at)
chunks(id, document_id, page_from, page_to, content, embedding blob)
```

## UI

- Sidebar: list of chats, new chat button.
- Chat: streaming replies rendered as markdown, "Searching your PDFs…" while a tool runs.
- Upload: button and drag-and-drop; document chips with processing/ready status.
- Memory panel: every fact with a score bar, grouped Pinned / Known / Archived.
  Edit, delete, lock or unlock. Click a fact to see its history ("used 4 times, last today").

## Build order

1. **Chat and saving.** App, database, adapter, streaming, chat list.
   *Check:* chat with it, refresh the page, the conversation is still there.
2. **Memory.** Facts, rolling summary, `search_history`, memory panel.
   *Check:* mention something early, chat a lot, ask about it later. Watch its
   score bar rise when you bring it up. Ask about a throwaway detail from
   earlier and see it appear as a new pinned fact.
3. **PDFs.** Upload, local storage, chunk/embed, PDF tools, document summaries.
   *Check:* upload a PDF, ask a question, see a page citation; ask for a summary.

## Known limits

- Each reply has a few seconds of startup before text begins, because every call
  starts a fresh Claude Code process. Streaming hides most of it.
- Uses the owner's Claude subscription usage.
- Embedding model is English-focused. A multilingual one can be swapped in later.
