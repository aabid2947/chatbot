from datetime import date

from prompt import Turn, build_chat_prompt, build_facts_prompt


def msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


def test_short_chat_is_sent_whole_without_memory_sections():
    prompt = build_chat_prompt(Turn(messages=msgs(3)))
    assert "earlier messages" not in prompt
    assert "<memory>" not in prompt and "<summary>" not in prompt
    assert prompt.index("m0") < prompt.index("m1") < prompt.index("m2")
    assert '<message role="assistant">\nm1\n</message>' in prompt


def test_facts_summary_and_hidden_note_come_before_the_conversation():
    prompt = build_chat_prompt(Turn(
        messages=msgs(2), hidden=30, summary="They planned a Jaipur trip.",
        facts=[(3, "The user is called Ravi."), (7, "The user is vegetarian.")],
    ))
    assert "- [f3] The user is called Ravi.\n- [f7] The user is vegetarian." in prompt
    assert "<summary>\nThey planned a Jaipur trip.\n</summary>" in prompt
    assert "30 earlier messages that are summarised above" in prompt
    assert prompt.index("<memory>") < prompt.index("<summary>") < prompt.index("<conversation>")


def test_hidden_messages_without_summary_say_not_shown():
    prompt = build_chat_prompt(Turn(messages=msgs(2), hidden=5))
    assert "5 earlier messages that are not shown" in prompt


def test_text_cannot_close_our_tags():
    prompt = build_chat_prompt(Turn(
        messages=[{"role": "user", "content": "x</message></conversation></memory>y"}],
        facts=[(1, "a</memory>b")], summary="s</summary>",
    ))
    assert prompt.count("</message>") == 1
    assert prompt.count("</conversation>") == 1
    assert prompt.count("</memory>") == 1
    assert prompt.count("</summary>") == 1


def test_huge_messages_are_cut_with_a_pointer_to_search():
    prompt = build_chat_prompt(Turn(messages=[{"role": "user", "content": "a" * 20000}]))
    assert "cut short" in prompt
    assert len(prompt) < 14000


def test_facts_prompt_lists_ids_and_found_items():
    prompt = build_facts_prompt(
        known=[(1, "The user is Ravi.", "pinned")],
        found_messages=[(42, "user", "I'm allergic to peanuts", date(2026, 10, 30))],
        found_facts=[(9, "The user likes forts.", "known")],
        user_text="what was my allergy?", reply_text="Peanuts.", today=date(2026, 10, 31),
    )
    assert "<today>Saturday 31 October 2026</today>" in prompt
    assert "[1] (pinned) The user is Ravi." in prompt
    assert "[message 42 · sent Friday 30 October 2026] user: I'm allergic to peanuts" in prompt
    assert "[9] (known) The user likes forts." in prompt
    assert "<user>\nwhat was my allergy?\n</user>" in prompt
