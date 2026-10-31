import json
import uuid


def new_chat(client):
    res = client.post("/api/conversations", json={})
    assert res.status_code == 201
    return res.get_json()


def sse_events(body):
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_new_chat_gets_a_uuid(client):
    chat = new_chat(client)
    assert str(uuid.UUID(chat["id"])) == chat["id"]
    assert chat["title"] == "New chat"


def test_sending_saves_both_messages_and_history_comes_back_in_order(client, fake_bot):
    chat = new_chat(client)
    res = client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "Plan a trip to Jaipur"})
    assert res.status_code == 201
    data = res.get_json()
    assert data["reply"]["content"] == "Hi back"
    assert data["conversation"]["title"] == "Plan a trip to Jaipur"

    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "Make it 3 days"})

    full = client.get(f"/api/conversations/{chat['id']}").get_json()
    assert full["conversation"]["id"] == chat["id"]
    assert [(m["role"], m["content"]) for m in full["messages"]] == [
        ("user", "Plan a trip to Jaipur"),
        ("assistant", "Hi back"),
        ("user", "Make it 3 days"),
        ("assistant", "Hi back"),
    ]
    assert all(m["conversation_id"] == chat["id"] for m in full["messages"])
    # The model saw the whole history, ending with the new message.
    assert fake_bot.seen[-1].messages[-1] == {"role": "user", "content": "Make it 3 days"}
    assert len(fake_bot.seen[-1].messages) == 3


def test_failed_reply_still_saves_the_users_message(client, fake_bot):
    chat = new_chat(client)
    fake_bot.error = "Claude failed: Not logged in"
    res = client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "hello?"})
    assert res.status_code == 502
    assert "Not logged in" in res.get_json()["error"]
    messages = client.get(f"/api/conversations/{chat['id']}/messages").get_json()
    assert [(m["role"], m["content"]) for m in messages] == [("user", "hello?")]


def test_streaming_sends_pieces_then_saves_the_reply(client, fake_bot):
    chat = new_chat(client)
    fake_bot.reply = ["Pink ", "City"]
    res = client.post(f"/api/conversations/{chat['id']}/messages/stream", json={"content": "Jaipur nickname?"})
    assert res.status_code == 200
    assert res.mimetype == "text/event-stream"
    events = sse_events(res.get_data(as_text=True))
    assert [e[0] for e in events] == ["user_message", "delta", "delta", "done"]
    assert events[0][1]["content"] == "Jaipur nickname?"
    assert [e[1]["text"] for e in events if e[0] == "delta"] == ["Pink ", "City"]
    assert events[-1][1]["reply"]["content"] == "Pink City"

    messages = client.get(f"/api/conversations/{chat['id']}/messages").get_json()
    assert [m["content"] for m in messages] == ["Jaipur nickname?", "Pink City"]


def test_streaming_error_is_reported_and_no_reply_is_saved(client, fake_bot):
    chat = new_chat(client)
    fake_bot.error = "Claude failed: timed out after 300s"
    res = client.post(f"/api/conversations/{chat['id']}/messages/stream", json={"content": "hi"})
    events = sse_events(res.get_data(as_text=True))
    assert [e[0] for e in events] == ["user_message", "error"]
    assert "timed out" in events[1][1]["error"]
    messages = client.get(f"/api/conversations/{chat['id']}/messages").get_json()
    assert [m["role"] for m in messages] == ["user"]


def test_chats_are_listed_newest_activity_first(client, fake_bot):
    first = new_chat(client)
    second = new_chat(client)
    client.post(f"/api/conversations/{first['id']}/messages", json={"content": "bump"})
    ids = [c["id"] for c in client.get("/api/conversations").get_json()]
    assert ids == [first["id"], second["id"]]


def test_rename(client):
    chat = new_chat(client)
    res = client.patch(f"/api/conversations/{chat['id']}", json={"title": "  Trip ideas "})
    assert res.get_json()["title"] == "Trip ideas"
    assert client.patch(f"/api/conversations/{chat['id']}", json={"title": " "}).status_code == 400


def test_delete_removes_chat_and_messages(client, fake_bot):
    chat = new_chat(client)
    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "hi"})
    assert client.delete(f"/api/conversations/{chat['id']}").status_code == 204
    assert client.get(f"/api/conversations/{chat['id']}").status_code == 404
    assert client.get("/api/conversations").get_json() == []


def test_unknown_chat_is_404_and_empty_message_is_400(client):
    assert client.get(f"/api/conversations/{uuid.uuid4()}").status_code == 404
    assert client.post(f"/api/conversations/{uuid.uuid4()}/messages", json={"content": "x"}).status_code == 404
    chat = new_chat(client)
    assert client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "  "}).status_code == 400


def test_regenerate_replaces_only_the_last_reply(client, fake_bot):
    chat = new_chat(client)
    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "hi"})
    fake_bot.reply = ["second try"]
    res = client.post(f"/api/conversations/{chat['id']}/regenerate")
    assert res.status_code == 201
    messages = client.get(f"/api/conversations/{chat['id']}/messages").get_json()
    assert [m["content"] for m in messages] == ["hi", "second try"]
    # The model was not shown the reply it is replacing.
    assert fake_bot.seen[-1].messages == [{"role": "user", "content": "hi"}]


def test_failed_regenerate_keeps_the_old_reply(client, fake_bot):
    chat = new_chat(client)
    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "hi"})
    fake_bot.error = "Claude failed"
    assert client.post(f"/api/conversations/{chat['id']}/regenerate").status_code == 502
    messages = client.get(f"/api/conversations/{chat['id']}/messages").get_json()
    assert [m["content"] for m in messages] == ["hi", "Hi back"]


def test_title_job_replaces_the_placeholder_but_not_a_users_rename(app, client, fake_bot, monkeypatch):
    import threading

    import bot

    app.config["GENERATE_TITLES"] = True
    release = threading.Event()

    def make_title(first_message):
        release.wait(5)
        return "Jaipur trip plan"

    monkeypatch.setattr(bot, "make_title", make_title)

    chat = new_chat(client)
    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "Plan a 3-day trip to Jaipur please"})
    release.set()
    for t in threading.enumerate():
        if t.name == f"title-{chat['id']}":
            t.join(5)
    title = client.get(f"/api/conversations/{chat['id']}").get_json()["conversation"]["title"]
    assert title == "Jaipur trip plan"

    # A chat the user renamed before the title arrived keeps their name.
    release.clear()
    other = new_chat(client)
    client.post(f"/api/conversations/{other['id']}/messages", json={"content": "Something else entirely"})
    client.patch(f"/api/conversations/{other['id']}", json={"title": "Mine"})
    release.set()
    for t in threading.enumerate():
        if t.name == f"title-{other['id']}":
            t.join(5)
    assert client.get(f"/api/conversations/{other['id']}").get_json()["conversation"]["title"] == "Mine"
