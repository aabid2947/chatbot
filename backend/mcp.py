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

import memory
from models import Conversation, Fact, db

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

ALLOWED_TOOLS = [f"mcp__{SERVER}__{t['name']}" for t in TOOLS]

LABELS = {
    "search_history": "Searching earlier messages…",
    "search_memory": "Checking what I remember…",
    "remember_fact": "Saving to memory…",
    "forget_fact": "Removing from memory…",
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
