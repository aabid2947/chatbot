"""Upgrades for databases made by older versions of the backend.

`db.create_all()` creates missing tables but never changes existing ones.
Each step here checks whether it is needed first, so it is safe to run on
every start.
"""

import logging

from sqlalchemy import Integer, inspect, text

log = logging.getLogger("chatbot.schema")


def upgrade(engine):
    if engine.dialect.name != "mysql":
        return  # only the MySQL database has ever held the old layout
    insp = inspect(engine)
    if not insp.has_table("conversations") or not insp.has_table("messages"):
        return
    columns = {c["name"]: c for c in insp.get_columns("conversations")}
    if "new_id" in columns:
        raise RuntimeError(
            "An earlier conversation-id upgrade stopped halfway (conversations.new_id exists). "
            "Restore the database from a backup before starting the backend."
        )
    if isinstance(columns["id"]["type"], Integer):
        _conversation_ids_to_uuid(engine, insp)


def _conversation_ids_to_uuid(engine, insp):
    """Numeric conversation ids -> UUID strings, keeping every chat and message."""
    foreign_keys = [
        fk["name"] for fk in insp.get_foreign_keys("messages") if fk["referred_table"] == "conversations"
    ]
    statements = [
        "ALTER TABLE conversations ADD COLUMN new_id CHAR(36) NULL",
        "UPDATE conversations SET new_id = UUID()",
        "ALTER TABLE messages ADD COLUMN new_conversation_id CHAR(36) NULL",
        "UPDATE messages m JOIN conversations c ON m.conversation_id = c.id "
        "SET m.new_conversation_id = c.new_id",
        *[f"ALTER TABLE messages DROP FOREIGN KEY `{name}`" for name in foreign_keys],
        "ALTER TABLE messages DROP COLUMN conversation_id",
        "ALTER TABLE messages CHANGE new_conversation_id conversation_id CHAR(36) NOT NULL",
        # Drop AUTO_INCREMENT first: MySQL won't drop the key of an auto-increment column.
        "ALTER TABLE conversations MODIFY id INT NOT NULL",
        "ALTER TABLE conversations DROP PRIMARY KEY, DROP COLUMN id",
        "ALTER TABLE conversations CHANGE new_id id CHAR(36) NOT NULL, ADD PRIMARY KEY (id)",
        "ALTER TABLE messages ADD INDEX ix_messages_conversation_id (conversation_id), "
        "ADD CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) "
        "REFERENCES conversations (id) ON DELETE CASCADE",
    ]
    with engine.begin() as conn:
        chats = conn.execute(text("SELECT COUNT(*) FROM conversations")).scalar()
        messages = conn.execute(text("SELECT COUNT(*) FROM messages")).scalar()
        log.info("Upgrading conversation ids to UUIDs (%d chats, %d messages)...", chats, messages)
        for sql in statements:
            conn.execute(text(sql))
        after = conn.execute(text("SELECT COUNT(*) FROM messages WHERE conversation_id IS NOT NULL")).scalar()
    log.info("Conversation ids upgraded. %d of %d messages kept their chat.", after, messages)
