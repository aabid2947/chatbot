"""The tools Claude can call while it writes a reply, served as a tiny MCP
server (JSON-RPC over HTTP) by this same Flask app.

Each chat call hands Claude a URL with the chat id, the user message being
answered, and a secret made fresh each time the backend starts. The endpoint
also only answers this machine. Every call is logged in `tool_calls` so the
background job can see what had to be dug up.
"""

import hmac
import logging
import secrets
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, jsonify, request

import documents
import memory
from models import Conversation, Document, Fact, db

log = logging.getLogger("chatbot.mcp")

bp = Blueprint("mcp", __name__)

SERVER = "memory"

TOOLS = [
    {
        "name": "search_history",
        "description": (
            "Search earlier messages of this conversation (or of all chats) by meaning and words. "
            "Returns the best matching messages word for word, with ids and dates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in a few words."},
                "all_chats": {"type": "boolean", "description": "Search every chat, not just this one."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_memory",
        "description": "Search facts remembered about the user that are not already in <memory>.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look for, in a few words."}},
            "required": ["query"],
        },
    },
    {
        "name": "remember_fact",
        "description": "Save something the user explicitly asked you to remember. It is kept permanently.",
        "inputSchema": {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "One self-contained sentence."}},
            "required": ["fact"],
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete a remembered fact the user asked you to forget.",
        "inputSchema": {
            "type": "object",
            "properties": {"fact_id": {"type": "integer", "description": "The number from an id like [f12]."}},
            "required": ["fact_id"],
        },
    },
]

_DOCUMENT_ARG = {
    "type": "string",
    "description": "Which PDF: its id like d3, or its file name.",
}

TOOLS += [
    {
        "name": "search_pdfs",
        "description": (
            "Search the PDFs uploaded to this conversation by meaning and words. Returns the best matching "
            "passages word for word, with the file name and page numbers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for: a question or a few key words."},
                "document": {**_DOCUMENT_ARG, "description": "Optional: search only this PDF (id like d3, or file name)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document_summary",
        "description": "The summary of a whole uploaded PDF.",
        "inputSchema": {
            "type": "object",
            "properties": {"document": _DOCUMENT_ARG},
            "required": ["document"],
        },
    },
    {
        "name": "read_pdf_pages",
        "description": "The exact text of a range of pages of an uploaded PDF (about 30,000 characters at most).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document": _DOCUMENT_ARG,
                "first_page": {"type": "integer", "description": "First page to read, counting from 1."},
                "last_page": {"type": "integer", "description": "Last page to read. Defaults to first_page."},
            },
            "required": ["document", "first_page"],
        },
    },
]

ALLOWED_TOOLS = [f"mcp__{SERVER}__{t['name']}" for t in TOOLS]

LABELS = {
    "search_history": "Searching earlier messages…",
    "search_memory": "Checking what I remember…",
    "remember_fact": "Saving to memory…",
    "forget_fact": "Removing from memory…",
    "search_pdfs": "Searching your PDFs…",
    "get_document_summary": "Reading the PDF summary…",
    "read_pdf_pages": "Reading PDF pages…",
}


def new_secret():
    return secrets.token_urlsafe(24)


def tools_for_turn(conversation_id, turn_message_id, window_start_id):
    """What bot.stream_reply needs to give Claude our tools for one reply."""
    app = current_app
    query = urlencode({
        "key": app.config["MCP_SECRET"],
        "conv": conversation_id,
        "turn": turn_message_id,
        "before": window_start_id,
    })
    url = f"{app.config['SELF_URL']}/api/mcp?{query}"
    return {"mcp_config": {"mcpServers": {SERVER: {"type": "http", "url": url}}}, "allowed": ALLOWED_TOOLS}


def _rpc_result(msg_id, result):
    return jsonify({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _rpc_error(msg_id, code, message):
    return jsonify({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _text(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


@bp.post("/api/mcp")
def handle():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    if not hmac.compare_digest(request.args.get("key", ""), current_app.config["MCP_SECRET"]):
        abort(403)

    msg = request.get_json(silent=True)
    if not isinstance(msg, dict):
        return _rpc_error(None, -32700, "Expected one JSON-RPC message")
    if "id" not in msg:
        return "", 202  # a notification, e.g. notifications/initialized
    msg_id, method, params = msg["id"], msg.get("method"), msg.get("params") or {}

    if method == "initialize":
        return _rpc_result(msg_id, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "chatbot-memory", "version": "1.0"},
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        return _rpc_result(msg_id, _call(params.get("name"), params.get("arguments") or {}))
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


@bp.get("/api/mcp")
def no_stream():
    return "", 405


def _call(name, args):
    conversation_id = request.args.get("conv", "")
    try:
        turn = int(request.args.get("turn", "0"))
        before = int(request.args.get("before", "0")) or None
    except ValueError:
        return _text("Bad tool URL.", is_error=True)
    if db.session.get(Conversation, conversation_id) is None:
        return _text("This conversation no longer exists.", is_error=True)

    handlers = {
        "search_history": _search_history,
        "search_memory": _search_memory,
        "remember_fact": _remember_fact,
        "forget_fact": _forget_fact,
        "search_pdfs": _search_pdfs,
        "get_document_summary": _get_document_summary,
        "read_pdf_pages": _read_pdf_pages,
    }
    handler = handlers.get(name)
    if handler is None:
        return _text(f"Unknown tool: {name}", is_error=True)
    try:
        return handler(args, conversation_id, turn, before)
    except (KeyError, TypeError, ValueError) as err:
        return _text(f"Bad arguments for {name}: {err}", is_error=True)


def _search_history(args, conversation_id, turn, before):
    query = str(args["query"]).strip()
    all_chats = bool(args.get("all_chats"))
    found = memory.search_history(
        query, conversation_id=conversation_id, before_message_id=before or turn, all_chats=all_chats
    )
    memory.log_tool_call(conversation_id, turn, "search_history", args, message_ids=[m.id for m, _ in found])
    log.info("search_history %r (all_chats=%s): %d found", query, all_chats, len(found))
    if not found:
        return _text("No earlier messages match.")
    titles = {}
    blocks = []
    for message, _ in found:
        where = ""
        if all_chats and message.conversation_id != conversation_id:
            if message.conversation_id not in titles:
                chat = db.session.get(Conversation, message.conversation_id)
                titles[message.conversation_id] = chat.title if chat else "?"
            where = f" · chat \"{titles[message.conversation_id]}\""
        content = message.content if len(message.content) <= 3000 else message.content[:3000] + " [...]"
        blocks.append(f"[message {message.id} · {message.role} · {message.created_at:%Y-%m-%d}{where}]\n{content}")
    return _text(f"{len(found)} matching messages, best first:\n\n" + "\n\n".join(blocks))


def _search_memory(args, conversation_id, turn, before):
    query = str(args["query"]).strip()
    in_prompt = [f.id for f in memory.pinned_facts()]
    found = memory.search_facts(query, exclude_ids=in_prompt)
    memory.log_tool_call(conversation_id, turn, "search_memory", args, fact_ids=[f.id for f, _, _ in found])
    log.info("search_memory %r: %d found", query, len(found))
    if not found:
        return _text("Nothing else remembered about that.")
    lines = [f"[f{f.id}] {f.content} (confidence {score:.2f})" for f, score, _ in found]
    return _text("Remembered about the user:\n" + "\n".join(lines))


def _remember_fact(args, conversation_id, turn, before):
    content = str(args["fact"]).strip()
    if not content:
        return _text("Nothing to remember.", is_error=True)
    fact = memory.remember(content, conversation_id=conversation_id, message_id=turn)
    db.session.commit()
    memory.log_tool_call(conversation_id, turn, "remember_fact", args, fact_ids=[fact.id])
    return _text(f"Saved permanently as [f{fact.id}].")


def _forget_fact(args, conversation_id, turn, before):
    fact = db.session.get(Fact, int(args["fact_id"]))
    if fact is None:
        return _text("No fact with that id.", is_error=True)
    content = fact.content
    db.session.delete(fact)
    db.session.commit()
    memory.log_tool_call(conversation_id, turn, "forget_fact", args)
    log.info("Forgot fact %s: %s", args["fact_id"], content)
    return _text(f"Forgot: {content}")


# --- PDFs --------------------------------------------------------------------


def _pages_label(first, last):
    return f"p. {first}" if first == last else f"pp. {first}-{last}"


def _no_such_document(conversation_id, reference):
    docs = db.session.query(Document).filter_by(conversation_id=conversation_id).order_by(Document.id).all()
    if not docs:
        return _text("No PDFs have been uploaded to this conversation.", is_error=True)
    listed = ", ".join(f"{d.ref} {d.filename}" for d in docs)
    return _text(f"No PDF matches {reference!r}. This chat has: {listed}.", is_error=True)


def _document_arg(args, conversation_id):
    """(document, None) or (None, error result)."""
    reference = args["document"]
    document = documents.find(conversation_id, reference)
    if document is None:
        return None, _no_such_document(conversation_id, reference)
    if document.status == "failed":
        return None, _text(f"{document.filename} couldn't be read: {document.error}", is_error=True)
    return document, None


def _search_pdfs(args, conversation_id, turn, before):
    query = str(args["query"]).strip()
    if not query:
        return _text("Give a query to search for.", is_error=True)
    only = None
    if args.get("document"):
        only, error = _document_arg(args, conversation_id)
        if error:
            return error
    found = documents.search(conversation_id, query, document_id=only.id if only else None)
    memory.log_tool_call(conversation_id, turn, "search_pdfs", args)
    log.info("search_pdfs %r%s: %d passages", query, f" in {only.ref}" if only else "", len(found))
    if not found:
        processing = db.session.query(Document).filter_by(conversation_id=conversation_id, status="processing").count()
        if processing:
            return _text("Nothing found. Some PDFs are still being read, so try again in a moment.")
        return _text("No passages in the uploaded PDFs match that.")
    blocks = [
        f"[{doc.ref} · {doc.filename} · {_pages_label(chunk.page_from, chunk.page_to)}]\n{chunk.content}"
        for chunk, doc, _ in found
    ]
    return _text(f"{len(found)} matching passages, best first:\n\n" + "\n\n".join(blocks))


def _get_document_summary(args, conversation_id, turn, before):
    document, error = _document_arg(args, conversation_id)
    if error:
        return error
    memory.log_tool_call(conversation_id, turn, "get_document_summary", args)
    log.info("get_document_summary %s", document.ref)
    if not document.summary:
        if document.status == "processing":
            return _text(f"The summary of {document.filename} is still being written. "
                         "Use search_pdfs or read_pdf_pages meanwhile.")
        return _text(f"{document.filename} has no summary. Use search_pdfs or read_pdf_pages instead.")
    return _text(f"Summary of {document.filename} ({document.pages} pages):\n\n{document.summary}")


def _read_pdf_pages(args, conversation_id, turn, before):
    document, error = _document_arg(args, conversation_id)
    if error:
        return error
    if not document.pages:
        return _text(f"{document.filename} is still being read. Try again in a moment.")
    first = int(args["first_page"])
    last = int(args.get("last_page") or first)
    if first > last:
        first, last = last, first
    if first < 1 or first > document.pages:
        return _text(f"{document.filename} has pages 1 to {document.pages}.", is_error=True)
    last = min(last, document.pages)
    pages, cut = documents.read_pages(document, first, last)
    memory.log_tool_call(conversation_id, turn, "read_pdf_pages", args)
    log.info("read_pdf_pages %s %s", document.ref, _pages_label(first, last))
    blocks = [f"[page {number}]\n{text or '(no text on this page)'}" for number, text in pages]
    note = ""
    if cut:
        note = (f"\n\n[Cut off at about 30,000 characters, in page {pages[-1][0]}. "
                f"Read from page {pages[-1][0]} to continue.]")
    return _text(f"{document.filename}, {_pages_label(first, last)}:\n\n" + "\n\n".join(blocks) + note)
