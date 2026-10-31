import json
import time

import pytest

import llm


def test_streams_text_pieces_in_order(fake_claude):
    fake_claude("ok")
    assert list(llm.stream("hi", "sys")) == ["Hello", " there", "!"]


def test_prompt_goes_in_on_stdin(fake_claude):
    fake_claude("echo_prompt")
    assert llm.complete("ünïcode prompt ✓", "sys") == "ünïcode prompt ✓"


def test_separate_assistant_messages_get_a_paragraph_break_and_subagent_text_is_ignored(fake_claude):
    fake_claude("two_messages")
    assert llm.complete("hi", "sys") == "Let me check.\n\nFound it."


def test_thinking_is_not_part_of_the_reply(fake_claude):
    fake_claude("thinking_only_then_text")
    assert llm.complete("hi", "sys") == "Answer"


def test_non_json_lines_are_skipped(fake_claude):
    fake_claude("garbage_line")
    assert llm.complete("hi", "sys") == "Still fine"


def test_error_result_raises_with_claudes_message(fake_claude):
    fake_claude("error_result")
    with pytest.raises(llm.LLMError, match="Not logged in"):
        llm.complete("hi", "sys")


def test_empty_reply_raises(fake_claude):
    fake_claude("empty")
    with pytest.raises(llm.LLMError, match="empty"):
        llm.complete("hi", "sys")


def test_crash_raises_with_stderr(fake_claude):
    fake_claude("crash")
    with pytest.raises(llm.LLMError, match="boom"):
        llm.complete("hi", "sys")


def test_stopping_early_kills_the_process(fake_claude):
    fake_claude("slow")
    started = time.monotonic()
    gen = llm.stream("hi", "sys")
    assert next(gen) == "first"
    gen.close()
    assert time.monotonic() - started < 10


def test_timeout_kills_a_stuck_process(fake_claude, monkeypatch):
    fake_claude("slow")
    monkeypatch.setenv("CLAUDE_TIMEOUT", "2")
    started = time.monotonic()
    with pytest.raises(llm.LLMError, match="timed out"):
        llm.complete("hi", "sys")
    assert time.monotonic() - started < 10


def test_model_and_effort_are_passed_through(fake_claude):
    calls = fake_claude("ok")
    llm.complete("hi", "sys", model="haiku", effort="low")
    assert calls[-1] == {"system_prompt": "sys", "model": "haiku", "effort": "low", "extra": []}


def test_tool_calls_are_reported_and_tools_are_handed_over(fake_claude):
    calls = fake_claude("tool_then_text")
    config = {"mcpServers": {"memory": {"type": "http", "url": "http://x"}}}
    items = list(llm.stream("hi", "sys", mcp_config=config, allowed_tools=["mcp__memory__a", "mcp__memory__b"]))
    assert items == [llm.ToolUse("search_history"), "Found it."]
    extra = calls[-1]["extra"]
    assert json.loads(extra[extra.index("--mcp-config") + 1]) == config
    assert extra[extra.index("--allowedTools") + 1] == "mcp__memory__a,mcp__memory__b"


def test_structured_returns_the_schema_data(fake_claude):
    calls = fake_claude("structured")
    schema = {"type": "object"}
    assert llm.structured("hi", "sys", schema) == {"facts": ["The user is Ravi."]}
    extra = calls[-1]["extra"]
    assert json.loads(extra[extra.index("--json-schema") + 1]) == schema
    assert calls[-1]["model"] == "haiku"


def test_structured_falls_back_to_json_text(fake_claude):
    fake_claude("structured_text_only")
    assert llm.structured("hi", "sys", {}) == {"facts": ["from text"]}


def test_real_command_is_stateless_and_locked_down(monkeypatch):
    monkeypatch.setattr(llm, "claude_executable", lambda: "claude-test")
    cmd = llm._command("SYS", "sonnet")
    assert cmd[0] == "claude-test"
    for flag in ["-p", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"]:
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == "local"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert "--safe-mode" not in cmd  # it would also block phase 2's tools
