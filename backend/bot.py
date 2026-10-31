"""The chatbot's "brain": prompt in, reply out.

The routes only talk to this module, so how replies are made (which model,
what goes in the prompt) can change without touching routes or the database.
"""

import re

import llm
from prompt import SYSTEM_PROMPT, TITLE_SYSTEM_PROMPT, build_chat_prompt, build_title_prompt

LLMError = llm.LLMError


def stream_reply(history):
    """Yield the assistant's reply in pieces.

    `history` is the full conversation so far, oldest first, as a list of
    {"role": "user" | "assistant", "content": str}. The last item is the
    message the user just sent.
    """
    yield from llm.stream(build_chat_prompt(history), SYSTEM_PROMPT, label="chat")


def generate_reply(history):
    """The assistant's whole reply as one string."""
    return "".join(stream_reply(history))


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
