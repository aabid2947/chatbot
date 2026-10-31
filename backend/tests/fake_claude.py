"""Stands in for `claude -p --output-format stream-json` in tests.

Usage: python fake_claude.py <mode>. Reads the prompt from stdin and prints
the same kind of JSON lines Claude Code prints.
"""

import json
import sys
import time

# Claude Code reads and writes UTF-8 whatever the Windows code page is.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def emit(obj):
    print(json.dumps(obj), flush=True)


def message(*texts):
    emit({"type": "stream_event", "event": {"type": "message_start"}})
    for t in texts:
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}})
    emit({"type": "assistant", "message": {}})


def result(text, is_error=False):
    emit({"type": "result", "subtype": "success", "is_error": is_error, "result": text,
          "usage": {"input_tokens": 10, "output_tokens": 5}})


mode = sys.argv[1]
prompt = sys.stdin.read()
emit({"type": "system", "subtype": "init"})

if mode == "ok":
    message("Hello", " there", "!")
    result("Hello there!")
elif mode == "echo_prompt":
    message(prompt)
    result(prompt)
elif mode == "two_messages":
    message("Let me check.")
    emit({"type": "stream_event", "parent_tool_use_id": "toolu_1",
          "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "IGNORED"}}})
    message("Found it.")
    result("Let me check.\n\nFound it.")
elif mode == "thinking_only_then_text":
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
    message("Answer")
    result("Answer")
elif mode == "error_result":
    message()
    result("Not logged in", is_error=True)
elif mode == "empty":
    message()
    result("")
elif mode == "crash":
    print("boom: something broke", file=sys.stderr, flush=True)
    sys.exit(3)
elif mode == "garbage_line":
    print("this is not json", flush=True)
    message("Still fine")
    result("Still fine")
elif mode == "tool_then_text":
    emit({"type": "stream_event", "event": {"type": "message_start"}})
    emit({"type": "stream_event", "event": {"type": "content_block_start",
          "content_block": {"type": "tool_use", "name": "mcp__memory__search_history"}}})
    emit({"type": "assistant", "message": {}})
    message("Found it.")
    result("Found it.")
elif mode == "structured":
    emit({"type": "result", "subtype": "success", "is_error": False, "result": '{"facts": ["x"]}',
          "structured_output": {"facts": ["The user is Ravi."]}, "usage": {}})
elif mode == "structured_text_only":
    result('{"facts": ["from text"]}')
elif mode == "slow":
    message("first")
    time.sleep(30)
    message("never")
    result("x")
