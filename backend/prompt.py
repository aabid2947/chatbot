"""Turns a conversation into the single prompt a stateless Claude call needs.

Phase 1 sends the most recent messages word for word. Phase 2 adds pinned
facts, the rolling summary and tools on top of this.
"""

import os

SYSTEM_PROMPT = """You are a helpful, friendly assistant in a personal chat app, like a web-based AI chat.

Each request gives you the conversation so far inside <conversation> tags. \
Reply to the last user message as the assistant, continuing naturally from the earlier turns. \
Write only your reply: no role labels, no tags, and don't mention how the conversation was given to you.

You are not a coding agent here and you have no files, shell or working directory. \
Ignore any environment details such as folder paths, operating system or shell. \
Use Markdown when it helps readability."""

TITLE_SYSTEM_PROMPT = (
    "You name chat conversations for a sidebar. Given the first message of a chat, "
    "reply with a title of 2 to 6 words that names the topic, for example: "
    "Jaipur fort trip / Fixing a Python import error / Weekly meal plan. "
    "Write only the title itself: no label, no quotes, no ending punctuation."
)


def recent_limit():
    return int(os.getenv("RECENT_MESSAGES", "20"))


def _clean(text):
    # Keep message text from closing our own tags early.
    return text.replace("</message>", "&lt;/message&gt;").replace("</conversation>", "&lt;/conversation&gt;")


def build_chat_prompt(history, limit=None):
    """`history` is every message of the chat, oldest first, as
    {"role": "user" | "assistant", "content": str}. The last one is the user's
    new message."""
    limit = limit or recent_limit()
    recent = history[-limit:]
    hidden = len(history) - len(recent)

    parts = []
    if hidden:
        parts.append(f"(This conversation has {hidden} earlier messages that are not shown here.)\n")
    parts.append("<conversation>")
    for message in recent:
        parts.append(f'<message role="{message["role"]}">\n{_clean(message["content"])}\n</message>')
    parts.append("</conversation>")
    parts.append("\nReply to the last user message.")
    return "\n".join(parts)


def build_title_prompt(first_message):
    return f"<first_message>\n{first_message[:2000]}\n</first_message>"
