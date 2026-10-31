"""The chatbot's "brain": prompt in, reply out.

Routes and background jobs only talk to this module, so how replies are made
(which model, what goes in the prompt) can change without touching them.
"""

import re

import llm
from prompt import (
    FACTS_SCHEMA,
    FACTS_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    TITLE_SYSTEM_PROMPT,
    build_chat_prompt,
    build_facts_prompt,
    build_summary_prompt,
    build_title_prompt,
)

LLMError = llm.LLMError
ToolUse = llm.ToolUse


def stream_reply(turn):
    """Yield the reply for a prompt.Turn: str pieces of text, and ToolUse
    when Claude starts looking something up."""
    tools = turn.tools or {}
    yield from llm.stream(
        build_chat_prompt(turn),
        SYSTEM_PROMPT,
        label="chat",
        mcp_config=tools.get("mcp_config"),
        allowed_tools=tools.get("allowed", ()),
    )


def generate_reply(turn):
    """The whole reply as one string."""
    return "".join(piece for piece in stream_reply(turn) if isinstance(piece, str))


def make_title(first_message):
    title = llm.complete(
        build_title_prompt(first_message),
        TITLE_SYSTEM_PROMPT,
        model=llm.background_model(),
        effort="low",
        label="title",
    )
    return clean_title(title)


def clean_title(raw):
    lines = [line for line in raw.strip().splitlines() if line.strip()]
    if not lines:
        return None
    # Drop a "Title:" label wherever the model put one.
    title = re.split(r"(?i)\btitle\s*:\s*", lines[0])[-1]
    title = title.strip().strip("\"'*#`").rstrip(".").strip()
    return title[:80] or None


def extract_facts(known, found_messages, found_facts, user_text, reply_text):
    """What to remember from one exchange. See prompt.FACTS_SCHEMA."""
    data = llm.structured(
        build_facts_prompt(known, found_messages, found_facts, user_text, reply_text),
        FACTS_SYSTEM_PROMPT,
        FACTS_SCHEMA,
        model=llm.background_model(),
        label="facts",
    )
    return {key: data.get(key) or [] for key in ("add", "update", "delete", "used", "promote")}


def summarize(old_summary, messages):
    """Fold `messages` ([{"role", "content"}]) into the running summary."""
    return llm.complete(
        build_summary_prompt(old_summary, messages),
        SUMMARY_SYSTEM_PROMPT,
        model=llm.background_model(),
        effort="low",
        label="summary",
    ).strip()
