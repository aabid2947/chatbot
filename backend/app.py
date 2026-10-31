import json
import logging
import os
import threading

from dotenv import load_dotenv
from flask import Flask, Response, abort, current_app, jsonify, request, send_file, stream_with_context
from sqlalchemy import event, text

load_dotenv()

import bot  # noqa: E402  (reads settings from the environment loaded above)
import documents  # noqa: E402
import embeddings  # noqa: E402
import jobs  # noqa: E402
import mcp  # noqa: E402
import memory  # noqa: E402
import memory_routes  # noqa: E402
import schema  # noqa: E402
from models import Conversation, Document, Message, db, utcnow  # noqa: E402
from prompt import Turn, recent_limit  # noqa: E402

log = logging.getLogger("chatbot.app")

DEFAULT_TITLE = "New chat"


def database_url():
    user = os.getenv("DB_USER", "chatbot")
    password = os.getenv("DB_PASSWORD", "chatbot")
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "chatbot")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


def create_app(config=None):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL") or database_url()
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
    app.config["GENERATE_TITLES"] = os.getenv("GENERATE_TITLES", "1") != "0"
    app.config["MEMORY_JOBS"] = os.getenv("MEMORY_JOBS", "1") != "0"
    app.config["TOOLS"] = os.getenv("TOOLS", "1") != "0"
    app.config["BACKGROUND_INLINE"] = False
    # Where Claude reaches our tool server: this backend, on this machine.
    app.config["SELF_URL"] = os.getenv("SELF_URL") or f"http://127.0.0.1:{os.getenv('PORT', '5000')}"
    app.config["MCP_SECRET"] = mcp.new_secret()
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("UPLOAD_MAX_MB", "50")) * 1024 * 1024
    if config:
        app.config.update(config)
    db.init_app(app)

    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            # Tests use SQLite. Make it honour ON DELETE CASCADE like MySQL does.
            event.listen(db.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        schema.upgrade(db.engine)
        db.create_all()

    register_routes(app)
    app.register_blueprint(mcp.bp)
    app.register_blueprint(memory_routes.bp)
    embeddings.warm_up()
    return app


# --- helpers -----------------------------------------------------------------


def get_conversation(conversation_id):
    convo = db.session.get(Conversation, conversation_id)
    if convo is None:
        abort(404, description="Conversation not found")
    return convo


def read_content():
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        abort(400, description="Message content is required")
    return content


def provisional_title(content):
    return content[:60] + ("…" if len(content) > 60 else "")


def save_user_message(convo, content):
    """Save the user's message straight away, so it is in the history even if
    the reply fails."""
    memory.record_activity()
    is_first = not convo.messages
    msg = Message(conversation=convo, role="user", content=content)
    db.session.add(msg)
    if is_first and convo.title == DEFAULT_TITLE:
        convo.title = provisional_title(content)
    convo.updated_at = utcnow()
    db.session.commit()
    return msg, is_first


def build_turn(convo, messages):
    """The prompt.Turn for replying to the last message in `messages`:
    pinned facts, the summary, the unsummarised recent messages, and tools."""
    upto = convo.summarized_upto_id or 0
    pending = [m for m in messages if m.id > upto] or messages[-1:]
    window = pending[-recent_limit():]
    tools = None
    if current_app.config["TOOLS"]:
        tools = mcp.tools_for_turn(convo.id, messages[-1].id, window[0].id)
    return Turn(
        messages=[{"role": m.role, "content": m.content} for m in window],
        hidden=len(messages) - len(window),
        summary=convo.summary if upto else None,
        facts=[(f.id, f.content) for f in memory.pinned_facts()],
        tools=tools,
        documents=[document_for_prompt(d) for d in convo.documents],
    )


def document_for_prompt(document):
    state = document.status
    if state == "processing" and document.stage in ("indexing", "summarizing"):
        state = document.stage
    return {"ref": document.ref, "filename": document.filename, "pages": document.pages, "state": state,
            "error": document.error, "one_line": document.one_line}


def get_document(document_id):
    document = db.session.get(Document, document_id)
    if document is None:
        abort(404, description="Document not found")
    return document


def save_reply(convo, content):
    reply = Message(conversation=convo, role="assistant", content=content)
    db.session.add(reply)
    # Bump explicitly: adding messages doesn't touch the conversation row itself,
    # and the chat list sorts by this.
    convo.updated_at = utcnow()
    db.session.commit()
    return reply


def reply_text(pieces):
    return "".join(p for p in pieces if isinstance(p, str))


def schedule_title(conversation_id, first_message):
    """Ask Claude for a short title in the background. The user never waits
    for it, and a title they set themselves in the meantime is kept."""
    app = current_app._get_current_object()
    if not app.config["GENERATE_TITLES"]:
        return
    placeholder = provisional_title(first_message)

    def run():
        with app.app_context():
            try:
                title = bot.make_title(first_message)
            except bot.LLMError as err:
                log.warning("Title for chat %s not generated: %s", conversation_id, err)
                return
            convo = db.session.get(Conversation, conversation_id)
            if title and convo is not None and convo.title == placeholder:
                convo.title = title
                db.session.commit()
                log.info("Chat %s titled %r", conversation_id, title)

    threading.Thread(target=run, name=f"title-{conversation_id}", daemon=True).start()


def after_reply(convo_id, user_msg_id, reply_msg_id, is_first, first_message):
    app = current_app._get_current_object()
    if is_first:
        schedule_title(convo_id, first_message)
    jobs.after_turn(app, convo_id, user_msg_id, reply_msg_id)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --- routes ------------------------------------------------------------------


def register_routes(app):
    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(502)
    def json_error(err):
        return jsonify(error=err.description), err.code

    @app.get("/api/health")
    def health():
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok", database="connected")

    @app.get("/api/conversations")
    def list_conversations():
        convos = Conversation.query.order_by(Conversation.updated_at.desc()).all()
        return jsonify([c.to_dict() for c in convos])

    @app.post("/api/conversations")
    def create_conversation():
        body = request.get_json(silent=True) or {}
        convo = Conversation(title=(body.get("title") or DEFAULT_TITLE)[:200])
        db.session.add(convo)
        db.session.commit()
        log.info("Created chat %s", convo.id)
        return jsonify(convo.to_dict()), 201

    @app.get("/api/conversations/<conversation_id>")
    def get_conversation_with_history(conversation_id):
        convo = get_conversation(conversation_id)
        return jsonify(
            conversation=convo.to_dict(),
            messages=[m.to_dict() for m in convo.messages],
            summary={"text": convo.summary, "covers_up_to_message_id": convo.summarized_upto_id},
        )

    @app.patch("/api/conversations/<conversation_id>")
    def rename_conversation(conversation_id):
        convo = get_conversation(conversation_id)
        body = request.get_json(silent=True) or {}
        title = (body.get("title") or "").strip()
        if not title:
            abort(400, description="Title is required")
        convo.title = title[:200]
        db.session.commit()
        return jsonify(convo.to_dict())

    @app.delete("/api/conversations/<conversation_id>")
    def delete_conversation(conversation_id):
        convo = get_conversation(conversation_id)
        files = [d.stored_name for d in convo.documents]
        db.session.delete(convo)
        db.session.commit()
        documents.remove_files(files)
        log.info("Deleted chat %s", conversation_id)
        return "", 204

    # --- PDFs ----------------------------------------------------------------

    @app.post("/api/conversations/<conversation_id>/documents")
    def upload_document(conversation_id):
        """Multipart upload, field `file`. Answers straight away with the
        document in `processing`; reading it happens in the background."""
        convo = get_conversation(conversation_id)
        upload = request.files.get("file")
        if upload is None:
            abort(400, description="Send the PDF as a multipart form field named 'file'")
        data = upload.read()
        if not data:
            abort(400, description="The file is empty")
        if not documents.looks_like_pdf(data):
            abort(400, description="Only PDF files can be uploaded")
        document = documents.save_upload(convo, upload.filename, data)
        convo.updated_at = utcnow()
        db.session.commit()
        log.info("Chat %s: uploaded %s as document %d (%d KB)", convo.id, document.filename, document.id,
                 len(data) // 1024)
        documents.start_processing(current_app._get_current_object(), document.id)
        db.session.refresh(document)
        return jsonify(document.to_dict()), 201

    @app.get("/api/conversations/<conversation_id>/documents")
    def list_documents(conversation_id):
        return jsonify([d.to_dict() for d in get_conversation(conversation_id).documents])

    @app.get("/api/documents/<int:document_id>")
    def get_document_details(document_id):
        return jsonify(get_document(document_id).to_dict(with_summary=True))

    @app.get("/api/documents/<int:document_id>/file")
    def get_document_file(document_id):
        document = get_document(document_id)
        path = documents.file_path(document)
        if not path.exists():
            abort(404, description="The file is missing from storage")
        return send_file(path, mimetype="application/pdf", download_name=document.filename)

    @app.post("/api/documents/<int:document_id>/retry")
    def retry_document(document_id):
        document = get_document(document_id)
        if document.status != "failed":
            abort(409, description="Only a failed document can be retried")
        document.status, document.stage, document.error = "processing", "reading", None
        db.session.commit()
        documents.start_processing(current_app._get_current_object(), document.id)
        db.session.refresh(document)
        return jsonify(document.to_dict())

    @app.delete("/api/documents/<int:document_id>")
    def delete_document(document_id):
        document = get_document(document_id)
        stored_name = document.stored_name
        db.session.delete(document)
        db.session.commit()
        documents.remove_files([stored_name])
        log.info("Deleted document %d", document_id)
        return "", 204

    @app.get("/api/conversations/<conversation_id>/messages")
    def list_messages(conversation_id):
        convo = get_conversation(conversation_id)
        return jsonify([m.to_dict() for m in convo.messages])

    @app.post("/api/conversations/<conversation_id>/messages")
    def send_message(conversation_id):
        """Save the message and return the whole reply in one response."""
        convo = get_conversation(conversation_id)
        user_msg, is_first = save_user_message(convo, read_content())
        try:
            text_reply = reply_text(bot.stream_reply(build_turn(convo, convo.messages)))
        except bot.LLMError as err:
            abort(502, description=str(err))
        reply = save_reply(convo, text_reply)
        after_reply(convo.id, user_msg.id, reply.id, is_first, user_msg.content)
        return jsonify(
            user_message=user_msg.to_dict(),
            reply=reply.to_dict(),
            conversation=convo.to_dict(),
        ), 201

    @app.post("/api/conversations/<conversation_id>/messages/stream")
    def send_message_stream(conversation_id):
        """Save the message and stream the reply as Server-Sent Events:

        user_message -> the saved message
        tool         -> {"name", "label"} when Claude starts looking something up
        delta        -> {"text": "..."}, repeated as Claude writes
        done         -> {"reply": ..., "conversation": ...} once the reply is saved
        error        -> {"error": "..."} if Claude failed; nothing is saved
        """
        convo = get_conversation(conversation_id)
        user_msg, is_first = save_user_message(convo, read_content())
        user_msg_data = user_msg.to_dict()
        turn = build_turn(convo, convo.messages)
        convo_id = convo.id

        @stream_with_context
        def events():
            yield sse("user_message", user_msg_data)
            parts = []
            try:
                for piece in bot.stream_reply(turn):
                    if isinstance(piece, str):
                        parts.append(piece)
                        yield sse("delta", {"text": piece})
                    else:
                        yield sse("tool", {"name": piece.name, "label": mcp.LABELS.get(piece.name, "Working…")})
            except bot.LLMError as err:
                yield sse("error", {"error": str(err)})
                return
            current = db.session.get(Conversation, convo_id)
            reply = save_reply(current, "".join(parts))
            reply_data, convo_data = reply.to_dict(), current.to_dict()
            after_reply(convo_id, user_msg_data["id"], reply.id, is_first, user_msg_data["content"])
            yield sse("done", {"reply": reply_data, "conversation": convo_data})

        return Response(
            events(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/conversations/<conversation_id>/regenerate")
    def regenerate(conversation_id):
        """Replace the last assistant reply with a fresh one."""
        convo = get_conversation(conversation_id)
        if not convo.messages or convo.messages[-1].role != "assistant":
            abort(400, description="There is no reply to regenerate")

        old = convo.messages[-1]
        try:
            text_reply = reply_text(bot.stream_reply(build_turn(convo, convo.messages[:-1])))
        except bot.LLMError as err:
            abort(502, description=str(err))  # the old reply stays
        convo.messages.remove(old)  # delete-orphan removes the row
        reply = save_reply(convo, text_reply)
        return jsonify(reply=reply.to_dict(), conversation=convo.to_dict()), 201


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    # With the debug reloader this file runs twice: a watcher process and the
    # real server. Only the real server picks up interrupted PDFs.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        documents.resume_unfinished(app)
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True, threaded=True)
