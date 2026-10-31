import os

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from sqlalchemy import text

from bot import generate_reply
from models import Conversation, Message, db

load_dotenv()


def database_url():
    user = os.getenv("DB_USER", "chatbot")
    password = os.getenv("DB_PASSWORD", "chatbot")
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "chatbot")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL") or database_url()
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
    db.init_app(app)

    with app.app_context():
        db.create_all()

    register_routes(app)
    return app


def get_conversation(conversation_id):
    convo = db.session.get(Conversation, conversation_id)
    if convo is None:
        abort(404, description="Conversation not found")
    return convo


def register_routes(app):
    @app.errorhandler(400)
    @app.errorhandler(404)
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
        convo = Conversation(title=(body.get("title") or "New chat")[:200])
        db.session.add(convo)
        db.session.commit()
        return jsonify(convo.to_dict()), 201

    @app.delete("/api/conversations/<int:conversation_id>")
    def delete_conversation(conversation_id):
        db.session.delete(get_conversation(conversation_id))
        db.session.commit()
        return "", 204

    @app.get("/api/conversations/<int:conversation_id>/messages")
    def list_messages(conversation_id):
        convo = get_conversation(conversation_id)
        return jsonify([m.to_dict() for m in convo.messages])

    @app.post("/api/conversations/<int:conversation_id>/messages")
    def send_message(conversation_id):
        convo = get_conversation(conversation_id)
        body = request.get_json(silent=True) or {}
        content = (body.get("content") or "").strip()
        if not content:
            abort(400, description="Message content is required")

        user_msg = Message(conversation=convo, role="user", content=content)
        db.session.add(user_msg)

        # Name the chat after its first message.
        if convo.title == "New chat" and len(convo.messages) == 1:
            convo.title = content[:60] + ("…" if len(content) > 60 else "")

        history = [{"role": m.role, "content": m.content} for m in convo.messages]
        reply = Message(conversation=convo, role="assistant", content=generate_reply(history))
        db.session.add(reply)
        db.session.commit()

        return jsonify(
            user_message=user_msg.to_dict(),
            reply=reply.to_dict(),
            conversation=convo.to_dict(),
        ), 201


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
