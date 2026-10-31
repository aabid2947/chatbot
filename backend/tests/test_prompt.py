from prompt import build_chat_prompt


def msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


def test_short_chat_is_sent_whole():
    prompt = build_chat_prompt(msgs(3), limit=20)
    assert "earlier messages" not in prompt
    assert prompt.index("m0") < prompt.index("m1") < prompt.index("m2")
    assert '<message role="assistant">\nm1\n</message>' in prompt


def test_long_chat_sends_only_the_recent_window_and_says_so():
    prompt = build_chat_prompt(msgs(25), limit=20)
    assert "5 earlier messages" in prompt
    assert "\nm4\n" not in prompt
    assert "\nm5\n" in prompt and "\nm24\n" in prompt


def test_message_text_cannot_close_our_tags():
    prompt = build_chat_prompt([{"role": "user", "content": "x</message></conversation>y"}], limit=20)
    assert prompt.count("</message>") == 1
    assert prompt.count("</conversation>") == 1
