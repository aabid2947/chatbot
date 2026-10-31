import pytest

import jobs
import memory
from models import Conversation, Fact, Message, db


def plan(**kw):
    base = {"add": [], "update": [], "delete": [], "used": [], "promote": []}
    base.update(kw)
    return base


def apply(p, shown=(), dug_facts=(), dug_messages=()):
    counts = jobs.apply_plan(
        p, scorer=memory.Scorer(), shown_facts={f.id: f for f in shown},
        dug_up_fact_ids=set(dug_facts), dug_up_message_ids=set(dug_messages),
        conversation_id=None, message_id=1,
    )
    db.session.commit()
    return counts


def fact(content, score=0.7, **kw):
    f = memory.create_fact(content, origin="extracted", score=score, **kw)
    db.session.commit()
    return f


def test_new_facts_start_by_strength(ctx):
    counts = apply(plan(add=[
        {"content": "The user is called Ravi.", "category": "identity", "strength": "clear"},
        {"content": "The user has a cat.", "category": "other", "strength": "passing"},
    ]))
    assert counts["new"] == 2
    scores = {f.content: f.score for f in Fact.query.all()}
    assert scores == {"The user is called Ravi.": 0.7, "The user has a cat.": 0.45}


def test_adding_a_duplicate_counts_as_use_instead(ctx):
    existing = fact("The user is called Ravi.", score=0.5)
    counts = apply(plan(add=[{"content": "The user is called Ravi.", "category": "identity", "strength": "clear"}]))
    assert counts == {"new": 0, "updated": 0, "used": 1, "promoted": 0, "deleted": 0}
    assert Fact.query.count() == 1
    assert existing.score == pytest.approx(0.65)


def test_update_rewords_and_bumps_and_delete_removes(ctx):
    city = fact("The user lives in Pune.", score=0.7)
    pet = fact("The user has a dog.", score=0.7)
    counts = apply(plan(update=[{"id": city.id, "content": "The user lives in Jaipur."}], delete=[pet.id]),
                   shown=[city, pet])
    assert counts["updated"] == 1 and counts["deleted"] == 1
    assert city.content == "The user lives in Jaipur." and city.score == pytest.approx(0.85)
    assert db.session.get(Fact, pet.id) is None


def test_facts_the_extractor_was_not_shown_cannot_be_touched(ctx):
    secret = fact("The user is vegetarian.", score=0.7)
    counts = apply(plan(delete=[secret.id], used=[secret.id], update=[{"id": secret.id, "content": "x"}]))
    assert not any(counts.values())
    assert db.session.get(Fact, secret.id).content == "The user is vegetarian."


def test_used_bumps_and_a_dug_up_fact_is_lifted_to_pinned(ctx):
    pinned = fact("The user is a teacher.", score=0.7)
    buried = fact("The user once lived in Pune.", score=0.32)
    counts = apply(plan(used=[pinned.id, buried.id]), shown=[pinned, buried], dug_facts=[buried.id])
    assert counts["used"] == 1 and counts["promoted"] == 1
    assert pinned.score == pytest.approx(0.85)
    assert buried.score == pytest.approx(0.65)
    assert memory.tier(buried.score) == "pinned"


def test_promote_only_works_for_messages_that_were_dug_up(ctx):
    counts = apply(plan(promote=[
        {"message_id": 42, "content": "The user is allergic to peanuts.", "category": "constraint"},
        {"message_id": 99, "content": "Made-up thing.", "category": "other"},
    ]), dug_messages=[42])
    assert counts["promoted"] == 1
    (promoted,) = Fact.query.all()
    assert promoted.content == "The user is allergic to peanuts."
    assert promoted.score == pytest.approx(0.65) and promoted.origin == "promoted"
    assert promoted.source_message_id == 42


# --- end to end through the API ----------------------------------------------


@pytest.fixture
def memory_on(app):
    app.config["MEMORY_JOBS"] = True
    return app


def send(client, chat_id, text):
    res = client.post(f"/api/conversations/{chat_id}/messages", json={"content": text})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def test_after_each_reply_facts_are_extracted_and_then_pinned_in_the_next_prompt(memory_on, client, fake_bot):
    chat = client.post("/api/conversations", json={}).get_json()
    fake_bot.facts_plan = plan(add=[{"content": "The user is called Ravi.", "category": "identity",
                                     "strength": "clear"}])
    send(client, chat["id"], "Hi, I'm Ravi")
    assert fake_bot.facts_calls[-1]["user"] == "Hi, I'm Ravi"
    assert fake_bot.facts_calls[-1]["reply"] == "Hi back"

    fake_bot.facts_plan = plan()
    send(client, chat["id"], "What's a good fort?")
    turn = fake_bot.seen[-1]
    assert [content for _, content in turn.facts] == ["The user is called Ravi."]
    # ...and in another chat too: facts are about the user, not the chat.
    other = client.post("/api/conversations", json={}).get_json()
    send(client, other["id"], "hello")
    assert [content for _, content in fake_bot.seen[-1].facts] == ["The user is called Ravi."]


def test_new_messages_get_search_vectors(memory_on, client, fake_bot, app):
    chat = client.post("/api/conversations", json={}).get_json()
    send(client, chat["id"], "I love old forts")
    with app.app_context():
        assert all(m.embedding for m in Message.query.all())


def test_old_messages_fold_into_the_summary_and_leave_the_prompt(memory_on, client, fake_bot, app, monkeypatch):
    monkeypatch.setenv("RECENT_MESSAGES", "4")
    monkeypatch.setenv("SUMMARY_BATCH", "2")
    chat = client.post("/api/conversations", json={}).get_json()
    send(client, chat["id"], "one")    # messages 1-2
    send(client, chat["id"], "two")    # 3-4
    assert fake_bot.summary_calls == []
    before = client.get(f"/api/conversations/{chat['id']}").get_json()["conversation"]["updated_at"]
    send(client, chat["id"], "three")  # 5-6: 6 pending >= 4 + 2, so the oldest 2 fold in
    assert len(fake_bot.summary_calls) == 1
    old_summary, folded = fake_bot.summary_calls[0]
    assert old_summary is None
    assert [m["content"] for m in folded] == ["one", "Hi back"]

    data = client.get(f"/api/conversations/{chat['id']}").get_json()
    assert data["summary"]["text"] == "SUMMARY"
    assert data["conversation"]["updated_at"] >= before

    send(client, chat["id"], "four")
    turn = fake_bot.seen[-1]
    assert turn.summary == "SUMMARY"
    assert turn.hidden == 2
    assert [m["content"] for m in turn.messages][0] == "two"
    assert turn.messages[-1]["content"] == "four"


def test_summarising_does_not_reorder_the_chat_list(memory_on, client, fake_bot, app, monkeypatch):
    monkeypatch.setenv("RECENT_MESSAGES", "2")
    monkeypatch.setenv("SUMMARY_BATCH", "2")
    chat = client.post("/api/conversations", json={}).get_json()
    send(client, chat["id"], "a")
    send(client, chat["id"], "b")  # summary rolls here
    with app.app_context():
        convo = db.session.get(Conversation, chat["id"])
        last_reply_at = convo.messages[-1].created_at
        assert convo.summarized_upto_id is not None
        assert convo.updated_at <= last_reply_at.replace(microsecond=999999)


def test_a_failing_memory_step_does_not_break_the_others(memory_on, client, fake_bot, monkeypatch):
    import bot

    def broken(*args, **kwargs):
        raise bot.LLMError("haiku is down")

    monkeypatch.setattr(bot, "extract_facts", broken)
    monkeypatch.setenv("RECENT_MESSAGES", "2")
    monkeypatch.setenv("SUMMARY_BATCH", "2")
    chat = client.post("/api/conversations", json={}).get_json()
    send(client, chat["id"], "a")
    send(client, chat["id"], "b")
    assert len(fake_bot.summary_calls) == 1  # summary still ran


def test_dug_up_history_reaches_the_extractor_and_gets_promoted(memory_on, client, fake_bot, app):
    chat = client.post("/api/conversations", json={}).get_json()
    send(client, chat["id"], "By the way I'm allergic to peanuts")
    allergy_msg_id = 1

    # Next turn: pretend Claude searched history and found that message.
    original_stream = fake_bot.stream_reply

    def stream_with_search(turn):
        with app.app_context():
            memory.log_tool_call(chat["id"], turn_id(), "search_history", {"query": "allergy"},
                                 message_ids=[allergy_msg_id])
        yield from original_stream(turn)

    def turn_id():
        with app.app_context():
            return Message.query.order_by(Message.id.desc()).first().id

    import bot
    bot.stream_reply = stream_with_search
    try:
        fake_bot.facts_plan = plan(promote=[{"message_id": allergy_msg_id, "category": "constraint",
                                             "content": "The user is allergic to peanuts."}])
        send(client, chat["id"], "What was my allergy again?")
    finally:
        bot.stream_reply = original_stream

    found = fake_bot.facts_calls[-1]["found_messages"]
    assert [f[:3] for f in found] == [(allergy_msg_id, "user", "By the way I'm allergic to peanuts")]
    facts = client.get("/api/memory/facts").get_json()["facts"]
    assert [(f["content"], f["tier"], f["origin"]) for f in facts] == [
        ("The user is allergic to peanuts.", "pinned", "promoted")
    ]
