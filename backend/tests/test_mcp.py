import json
from urllib.parse import parse_qs, urlparse

import pytest

import memory
from models import Fact, Message, ToolCall, db


@pytest.fixture
def chat(client, fake_bot):
    chat = client.post("/api/conversations", json={}).get_json()
    for text in ["I am planning a trip to Jaipur", "I am allergic to peanuts", "What should I pack?"]:
        client.post(f"/api/conversations/{chat['id']}/messages", json={"content": text})
    return chat


def mcp_url(app, conv, turn=6, before=5, key=None):
    return f"/api/mcp?key={key or app.config['MCP_SECRET']}&conv={conv}&turn={turn}&before={before}"


def rpc(client, url, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(url, json=body)


def call(client, url, name, **arguments):
    res = rpc(client, url, "tools/call", {"name": name, "arguments": arguments})
    assert res.status_code == 200
    return res.get_json()["result"]


def test_wrong_or_missing_key_is_refused(app, client, chat):
    assert rpc(client, mcp_url(app, chat["id"], key="nope"), "tools/list").status_code == 403
    assert rpc(client, f"/api/mcp?conv={chat['id']}", "tools/list").status_code == 403


def test_handshake_and_tool_list(app, client, chat):
    url = mcp_url(app, chat["id"])
    init = rpc(client, url, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}}).get_json()
    assert init["result"]["protocolVersion"] == "2025-11-25"
    assert init["result"]["capabilities"] == {"tools": {}}
    assert client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    tools = rpc(client, url, "tools/list").get_json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["search_history", "search_memory", "remember_fact", "forget_fact",
                                          "search_pdfs", "get_document_summary", "read_pdf_pages"]
    unknown = rpc(client, url, "server/discover").get_json()
    assert unknown["error"]["code"] == -32601


def test_search_history_finds_older_messages_and_logs_them(app, client, chat):
    # Messages: 1 Jaipur, 2 reply, 3 peanuts, 4 reply, 5 pack, 6 reply. Window starts at 5.
    result = call(client, mcp_url(app, chat["id"], turn=5, before=5), "search_history", query="allergic peanuts")
    text = result["content"][0]["text"]
    assert "[message 3 · user" in text and "allergic to peanuts" in text
    assert "What should I pack" not in text  # already in the prompt window
    with app.app_context():
        logged = ToolCall.query.one()
        assert logged.tool == "search_history" and logged.turn_message_id == 5
        assert 3 in json.loads(logged.found_message_ids)


def test_search_history_can_look_across_chats(app, client, chat, fake_bot):
    other = client.post("/api/conversations", json={"title": "Cooking"}).get_json()
    client.post(f"/api/conversations/{other['id']}/messages", json={"content": "My favourite dish is dal baati"})
    url = mcp_url(app, chat["id"])
    assert "dal baati" not in call(client, url, "search_history", query="favourite dish dal baati")["content"][0]["text"]
    text = call(client, url, "search_history", query="favourite dish dal baati", all_chats=True)["content"][0]["text"]
    assert 'chat "Cooking"' in text and "dal baati" in text


def test_search_memory_skips_pinned_facts_and_logs_what_it_found(app, client, chat):
    with app.app_context():
        memory.create_fact("The user is called Ravi.", origin="extracted", score=0.9)
        buried = memory.create_fact("The user drinks masala tea every morning.", origin="extracted", score=0.4)
        db.session.commit()
        buried_id = buried.id
    url = mcp_url(app, chat["id"])
    text = call(client, url, "search_memory", query="masala tea morning")["content"][0]["text"]
    assert f"[f{buried_id}] The user drinks masala tea every morning." in text
    assert "Ravi" not in call(client, url, "search_memory", query="called Ravi")["content"][0]["text"]
    with app.app_context():
        assert buried_id in json.loads(ToolCall.query.first().found_fact_ids)


def test_remember_and_forget(app, client, chat):
    url = mcp_url(app, chat["id"])
    saved = call(client, url, "remember_fact", fact="The user's passport expires in March 2027.")
    with app.app_context():
        fact = Fact.query.one()
        assert (fact.locked, fact.score, fact.origin) == (True, 1.0, "remembered")
        fact_id = fact.id
    assert f"[f{fact_id}]" in saved["content"][0]["text"]
    forgot = call(client, url, "forget_fact", fact_id=fact_id)
    assert "passport" in forgot["content"][0]["text"]
    with app.app_context():
        assert Fact.query.count() == 0
    assert call(client, url, "forget_fact", fact_id=999).get("isError") is True


def test_only_searches_count_as_digging_something_up(app, client, chat):
    url = mcp_url(app, chat["id"], turn=5, before=5)
    call(client, url, "remember_fact", fact="The user's passport expires in March 2027.")
    call(client, url, "search_history", query="allergic peanuts")
    with app.app_context():
        message_ids, fact_ids = memory.dug_up_this_turn(5)
        assert 3 in message_ids
        assert fact_ids == []  # the remembered fact was saved, not dug up


def test_bad_arguments_and_unknown_tools_are_tool_errors(app, client, chat):
    url = mcp_url(app, chat["id"])
    assert call(client, url, "search_history").get("isError") is True
    assert call(client, url, "launch_rockets").get("isError") is True


def test_each_reply_gets_tools_scoped_to_its_chat_and_turn(app, client, fake_bot):
    chat = client.post("/api/conversations", json={}).get_json()
    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "hello"})
    tools = fake_bot.seen[-1].tools
    assert tools["allowed"] == ["mcp__memory__search_history", "mcp__memory__search_memory",
                                "mcp__memory__remember_fact", "mcp__memory__forget_fact",
                                "mcp__memory__search_pdfs", "mcp__memory__get_document_summary",
                                "mcp__memory__read_pdf_pages"]
    url = urlparse(tools["mcp_config"]["mcpServers"]["memory"]["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "http://127.0.0.1:5999/api/mcp"
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query == {"key": app.config["MCP_SECRET"], "conv": chat["id"], "turn": "1", "before": "1"}


def test_stream_reports_tool_use(client, fake_bot):
    import bot

    chat = client.post("/api/conversations", json={}).get_json()
    fake_bot.reply = [bot.ToolUse("search_history"), "Peanuts."]
    body = client.post(f"/api/conversations/{chat['id']}/messages/stream",
                       json={"content": "what am I allergic to?"}).get_data(as_text=True)
    assert 'event: tool\ndata: {"name": "search_history", "label": "Searching earlier messages…"}' in body
    with client.application.app_context():
        assert Message.query.order_by(Message.id.desc()).first().content == "Peanuts."
