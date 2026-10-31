"""The chatbot's "brain".

This is a placeholder that echoes the user back. Swap `generate_reply` for a
real model call (Claude, etc.) without touching the routes or the database.
"""


def generate_reply(history):
    """Return the assistant's reply text.

    `history` is the full conversation so far, oldest first, as a list of
    {"role": "user" | "assistant", "content": str}. The last item is the
    message the user just sent.
    """
    last = history[-1]["content"].strip()
    turn = sum(1 for m in history if m["role"] == "user")
    return f'You said: "{last}". (Message {turn} in this chat. Hook up a real model in backend/bot.py.)'
