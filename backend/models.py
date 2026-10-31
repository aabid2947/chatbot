import uuid
from datetime import date, datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today():
    """The user's calendar day (this machine's local time)."""
    return date.today()


def new_conversation_id():
    return str(uuid.uuid4())


def iso(value):
    return value.isoformat() + "Z" if value else None


class Conversation(db.Model):
    __tablename__ = "conversations"

    # A UUID string, e.g. "3f2b8c1e-...". Every API call about a chat uses it.
    id = db.Column(db.CHAR(36), primary_key=True, default=new_conversation_id)
    title = db.Column(db.String(200), nullable=False, default="New chat")
    # Rolling summary of every message up to and including summarized_upto_id.
    summary = db.Column(db.Text, nullable=True)
    summarized_upto_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    messages = db.relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )
    tool_calls = db.relationship("ToolCall", cascade="all, delete-orphan", passive_deletes=True)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.CHAR(36),
        db.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(db.Enum("user", "assistant", name="message_role"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    # float32 vector from embeddings.py; filled in by the background job.
    embedding = db.Column(db.LargeBinary, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    conversation = db.relationship("Conversation", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "created_at": iso(self.created_at),
        }


class Fact(db.Model):
    """Something about the user worth remembering across every chat.

    `score` is the confidence as of `touched_on`, the last day the fact was
    created, used or edited. The live score fades from there for each active
    day it goes unused (see memory.py), so nothing has to run nightly.
    """

    __tablename__ = "facts"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(32), nullable=False, default="other")
    score = db.Column(db.Float, nullable=False)
    touched_on = db.Column(db.Date, nullable=False, default=today)
    locked = db.Column(db.Boolean, nullable=False, default=False)
    # extracted | remembered | promoted | manual
    origin = db.Column(db.String(16), nullable=False)
    use_count = db.Column(db.Integer, nullable=False, default=0)
    last_used_at = db.Column(db.DateTime, nullable=True)
    # Where it came from. Plain columns, not foreign keys: a fact outlives its chat.
    source_conversation_id = db.Column(db.CHAR(36), nullable=True)
    source_message_id = db.Column(db.Integer, nullable=True)
    embedding = db.Column(db.LargeBinary, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    events = db.relationship(
        "FactEvent", back_populates="fact", cascade="all, delete-orphan", passive_deletes=True,
        order_by="FactEvent.id",
    )


class FactEvent(db.Model):
    """Why a fact's score changed: created | used | promoted | edited | locked | unlocked."""

    __tablename__ = "fact_events"

    id = db.Column(db.Integer, primary_key=True)
    fact_id = db.Column(db.Integer, db.ForeignKey("facts.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)
    delta = db.Column(db.Float, nullable=False, default=0)
    score_after = db.Column(db.Float, nullable=False)
    conversation_id = db.Column(db.CHAR(36), nullable=True)
    message_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    fact = db.relationship("Fact", back_populates="events")

    def to_dict(self):
        return {
            "kind": self.kind,
            "delta": round(self.delta, 3),
            "score_after": round(self.score_after, 3),
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "created_at": iso(self.created_at),
        }


class ActivityDay(db.Model):
    """Each calendar day the user chatted at all. Facts only fade on these days."""

    __tablename__ = "activity_days"

    day = db.Column(db.Date, primary_key=True)


class ToolCall(db.Model):
    """Every tool Claude called, tied to the user message it was answering.
    The background job reads these to see what had to be dug up."""

    __tablename__ = "tool_calls"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.CHAR(36), db.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_message_id = db.Column(db.Integer, nullable=False, index=True)
    tool = db.Column(db.String(64), nullable=False)
    arguments = db.Column(db.Text, nullable=False)  # JSON
    found_message_ids = db.Column(db.Text, nullable=False, default="[]")  # JSON list
    found_fact_ids = db.Column(db.Text, nullable=False, default="[]")  # JSON list
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
