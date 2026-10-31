"""Turns a chat into the single prompt a stateless Claude call needs.

Each turn sends, in order: pinned facts about the user, the rolling summary of
older messages, the recent messages word for word, and the new message.
Everything else Claude fetches with tools (see mcp.py) only when it needs it.
"""

import os
from dataclasses import dataclass, field
from datetime import date

SYSTEM_PROMPT = """You are a helpful, friendly assistant in a personal chat app, like a web-based AI chat. \
It is used by one person, and you have a long-term memory of them.

Each request gives you:
- <memory>: facts about the user from earlier chats, each with an id like [f12]. Use them naturally \
when relevant. Don't recite them or say "according to my memory".
- <summary>: a summary of the earlier part of this conversation, when it is long.
- <conversation>: the most recent messages, word for word. Reply to the last user message as the \
assistant, continuing naturally. Write only your reply: no role labels or tags.

You have tools for what isn't shown:
- search_history: look up earlier messages, when the user refers to something from before that isn't \
in the recent messages, or the summary lacks the detail you need. Set all_chats when they mean another chat.
- search_memory: look up other things you know about the user, when they ask about themselves or \
their past and <memory> doesn't cover it.
- remember_fact: when the user explicitly asks you to remember something.
- forget_fact: when the user asks you to forget something; pass the fact's id.
Only use a tool when it would change your answer. Don't announce or narrate tool use; just answer. \
If a search finds nothing, say you don't have that information rather than guessing.

You are not a coding agent here and you have no files, shell or working directory. Ignore any \
environment details such as folder paths, operating system or shell. Use Markdown when it helps."""

TITLE_SYSTEM_PROMPT = (
    "You name chat conversations for a sidebar. Given the first message of a chat, "
    "reply with a title of 2 to 6 words that names the topic, for example: "
    "Jaipur fort trip / Fixing a Python import error / Weekly meal plan. "
    "Write only the title itself: no label, no quotes, no ending punctuation."
)

MESSAGE_CHAR_LIMIT = 12000


def recent_limit():
    """Most messages ever sent word for word, even if summarising falls behind."""
    return int(os.getenv("RECENT_MESSAGES_MAX", "40"))


@dataclass
class Turn:
    """Everything one reply needs."""

    messages: list  # [{"role", "content"}] shown word for word; the last is the user's new message
    hidden: int = 0  # earlier messages not shown word for word
    summary: str = None
    facts: list = field(default_factory=list)  # [(id, content)] pinned facts
    tools: dict = None  # {"mcp_config": {...}, "allowed": [...]} or None for no tools


def _clean(text):
    # Keep message text from closing our own tags early.
    for tag in ("message", "conversation", "memory", "summary"):
        text = text.replace(f"</{tag}>", f"&lt;/{tag}&gt;")
    return text


def _trim(text):
    if len(text) <= MESSAGE_CHAR_LIMIT:
        return text
    return text[:MESSAGE_CHAR_LIMIT] + "\n[...message cut short here; use search_history for the rest]"


def build_chat_prompt(turn):
    parts = []
    if turn.facts:
        parts.append("<memory>")
        parts += [f"- [f{fid}] {_clean(content)}" for fid, content in turn.facts]
        parts.append("</memory>\n")
    if turn.summary:
        parts.append(f"<summary>\n{_clean(turn.summary)}\n</summary>\n")
    if turn.hidden:
        covered = "summarised above" if turn.summary else "not shown"
        parts.append(
            f"(This conversation has {turn.hidden} earlier messages that are {covered}. "
            "Use search_history for their exact wording.)\n"
        )
    parts.append("<conversation>")
    for message in turn.messages:
        parts.append(f'<message role="{message["role"]}">\n{_clean(_trim(message["content"]))}\n</message>')
    parts.append("</conversation>")
    parts.append("\nReply to the last user message.")
    return "\n".join(parts)


def build_title_prompt(first_message):
    return f"<first_message>\n{first_message[:2000]}\n</first_message>"


# --- background prompts ------------------------------------------------------

FACTS_SYSTEM_PROMPT = """You maintain the long-term memory of a personal chat assistant. After each \
exchange you decide what to remember about the user. You only output data matching the schema.

The test for a fact: "will this still matter in a future conversation?"
Remember: who they are (name, job, location, family), lasting preferences (how they like answers, \
likes and dislikes), ongoing goals and projects, decisions they made, constraints (budget, deadlines, \
tools they can't use), and corrections to earlier facts.
Do not remember: small talk, one-off questions or tasks, things the assistant said or suggested \
(unless the user clearly accepted it as a decision), or general knowledge.
Only record what the user said or confirmed, never guesses. Write each fact as one short, \
self-contained sentence about "the user" (e.g. "The user is vegetarian."). Never guess the user's \
gender; don't use he/she. Turn relative dates ("tomorrow", "next week") into real dates: use \
<today> for the current exchange, and the "sent" date for a message found in history.

Fields:
- add: new facts. strength "clear" if stated plainly, "passing" if mentioned in passing.
- update: an existing fact (by id) that the user corrected or refined; give the full new wording. \
Prefer this to adding a near-duplicate. A contradicted fact must be updated, not left alongside.
- delete: ids of facts the user asked to forget, or that are now plainly false and can't be reworded.
- used: ids of known facts the user referred to in this exchange, or that the assistant's reply \
clearly relied on. Don't list facts that were merely present.
- promote: when <found_in_history> is present, the assistant had to dig those messages up because the \
user asked about something. For each found message that the user's question was about (the reply \
used it), you MUST promote it, even if it looked like a one-off detail when first said: the user \
asking about it again is exactly what makes it worth remembering. Skip found messages that weren't \
relevant, and skip it if a known fact already says the same (list that fact under used instead). \
Give the source message id and the fact as one sentence.
Leave any field as an empty list when nothing applies. Most exchanges add nothing."""

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "add": {"type": "array", "items": {"type": "object", "properties": {
            "content": {"type": "string"},
            "category": {"type": "string", "enum": ["identity", "preference", "goal", "project",
                                                     "decision", "constraint", "other"]},
            "strength": {"type": "string", "enum": ["clear", "passing"]},
        }, "required": ["content", "category", "strength"]}},
        "update": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "content": {"type": "string"},
        }, "required": ["id", "content"]}},
        "delete": {"type": "array", "items": {"type": "integer"}},
        "used": {"type": "array", "items": {"type": "integer"}},
        "promote": {"type": "array", "items": {"type": "object", "properties": {
            "message_id": {"type": "integer"},
            "content": {"type": "string"},
            "category": {"type": "string", "enum": ["identity", "preference", "goal", "project",
                                                     "decision", "constraint", "other"]},
        }, "required": ["message_id", "content", "category"]}},
    },
    "required": ["add", "update", "delete", "used", "promote"],
}


def build_facts_prompt(known, found_messages, found_facts, user_text, reply_text, today=None):
    """known / found_facts: [(id, content, tier)]; found_messages: [(id, role, content, sent_on)]."""
    today = today or date.today()
    lines = [f"<today>{today:%A %d %B %Y}</today>\n", "<known_facts>"]
    lines += [f"[{fid}] ({t}) {_clean(c)}" for fid, c, t in known] or ["(none yet)"]
    lines.append("</known_facts>\n")
    if found_facts:
        lines.append("<found_by_memory_search>")
        lines += [f"[{fid}] ({t}) {_clean(c)}" for fid, c, t in found_facts]
        lines.append("</found_by_memory_search>\n")
    if found_messages:
        lines.append("<found_in_history>")
        for mid, role, content, sent_on in found_messages:
            lines.append(f"[message {mid} · sent {sent_on:%A %d %B %Y}] {role}: {_clean(content[:1500])}")
        lines.append("</found_in_history>\n")
    lines.append("<exchange>")
    lines.append(f"<user>\n{_clean(user_text[:6000])}\n</user>")
    lines.append(f"<assistant>\n{_clean(reply_text[:6000])}\n</assistant>")
    lines.append("</exchange>")
    return "\n".join(lines)


SUMMARY_SYSTEM_PROMPT = """You keep a running summary of a long chat between a user and an assistant, \
so the assistant can continue the conversation without the old messages.

You get the current summary (may be empty) and the next messages to fold in. Write the new summary: \
the topics in order, what the user wanted, key details (names, numbers, places, dates, choices), \
decisions made and open questions. Keep specifics, drop pleasantries. Write in plain prose or short \
bullets, in the third person: call them "the user" or by name, and never guess their gender (no \
he/she). Record what was said; don't add your own commentary, judgements or advice. At most 350 \
words. Output only the summary itself: no preamble, and don't mention whether the current summary \
was empty."""


def build_summary_prompt(old_summary, messages):
    lines = [f"<current_summary>\n{_clean(old_summary or '(empty)')}\n</current_summary>\n", "<new_messages>"]
    lines += [f'<message role="{m["role"]}">\n{_clean(m["content"][:4000])}\n</message>' for m in messages]
    lines.append("</new_messages>")
    return "\n".join(lines)
