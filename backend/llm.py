"""Local Claude Code used as a stateless LLM.

Every call starts a fresh `claude -p` process and nothing carries over between
calls, so the caller sends the whole prompt every time. The flags switch off
everything that makes Claude Code a coding agent (built-in tools, skills, the
owner's settings, hooks and CLAUDE.md) so it behaves like a plain chat model.
The only tools it gets are the ones we hand it with `--mcp-config`.

Do not add `--safe-mode`: besides the owner's setup it also blocks the
`--mcp-config` tool servers.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger("chatbot.llm")


class LLMError(RuntimeError):
    """Claude could not produce a reply."""


class ToolUse(NamedTuple):
    """Yielded by `stream` when Claude starts calling one of our tools."""

    name: str


def chat_model():
    return os.getenv("CLAUDE_MODEL", "sonnet")


def background_model():
    return os.getenv("CLAUDE_BACKGROUND_MODEL", "haiku")


def timeout_seconds():
    return int(os.getenv("CLAUDE_TIMEOUT", "300"))


def claude_executable():
    configured = os.getenv("CLAUDE_BIN")
    if configured:
        return configured
    found = shutil.which("claude")
    if found and found.lower().endswith((".cmd", ".bat", ".ps1")):
        # npm's launcher script. Run the binary it points at instead, because
        # cmd.exe mangles arguments that contain quotes or newlines.
        exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    if found:
        return found
    raise LLMError("Claude Code is not installed or not on PATH. Set CLAUDE_BIN in backend/.env.")


def _workdir():
    # An empty folder outside every project, so Claude never picks up a
    # project's CLAUDE.md or local settings.
    path = Path(tempfile.gettempdir()) / "chatbot-claude"
    path.mkdir(exist_ok=True)
    return str(path)


def _command(system_prompt, model, effort=None, extra=()):
    cmd = [
        claude_executable(),
        "-p",
        "--setting-sources", "local",
        "--disable-slash-commands",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--system-prompt", system_prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if effort:
        cmd += ["--effort", effort]
    return cmd + list(extra)


def _top_level_stream_event(event):
    if event.get("type") != "stream_event" or event.get("parent_tool_use_id"):
        return None
    return event.get("event") or {}


def _text_delta(event):
    """The text in one stream event, or None if it carries no reply text."""
    inner = _top_level_stream_event(event)
    if not inner or inner.get("type") != "content_block_delta":
        return None
    delta = inner.get("delta") or {}
    if delta.get("type") != "text_delta":
        return None
    return delta.get("text") or None


def _tool_start(event):
    """The tool name if this event starts a tool call, else None."""
    inner = _top_level_stream_event(event)
    if not inner or inner.get("type") != "content_block_start":
        return None
    block = inner.get("content_block") or {}
    if block.get("type") not in ("tool_use", "mcp_tool_use", "server_tool_use"):
        return None
    name = block.get("name") or ""
    return name.split("__")[-1] if name.startswith("mcp__") else name


def _is_message_start(event):
    inner = _top_level_stream_event(event)
    return bool(inner) and inner.get("type") == "message_start"


def _run(prompt, system_prompt, model, effort, label, extra, out):
    """Run one Claude process. Yields reply text (str) and ToolUse items, and
    puts Claude's final `result` event in out["result"]. Raises LLMError if
    Claude fails. If the caller stops reading early, the process is killed."""
    cmd = _command(system_prompt, model, effort, extra)
    started = time.monotonic()
    first_text_at = None
    chars_out = 0
    tools_used = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=_workdir(),
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except OSError as err:
        log.error("[%s] could not start Claude Code: %s", label, err)
        raise LLMError(f"Could not start Claude Code: {err}") from err

    # Read stderr on the side so a chatty process can never fill the pipe and stall.
    stderr_parts = []
    stderr_reader = threading.Thread(target=lambda: stderr_parts.append(proc.stderr.read()), daemon=True)
    stderr_reader.start()
    timer = threading.Timer(timeout_seconds(), proc.kill)
    timer.start()

    result = None
    finished = False
    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass  # the process already died; its exit is reported below

        texts_in_message = 0
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.warning("[%s] skipped a line that is not JSON: %.200s", label, line)
                continue

            if _is_message_start(event):
                texts_in_message = 0
                continue
            tool = _tool_start(event)
            if tool:
                tools_used.append(tool)
                yield ToolUse(tool)
                continue
            text = _text_delta(event)
            if text:
                # A new assistant message after a tool call: keep paragraphs apart.
                if texts_in_message == 0 and chars_out:
                    text = "\n\n" + text
                texts_in_message += 1
                if first_text_at is None:
                    first_text_at = time.monotonic() - started
                chars_out += len(text)
                yield text
            elif event.get("type") == "result":
                result = event

        proc.wait()
        finished = True
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
            if not finished:
                log.info("[%s] stopped early after %.1fs; Claude process killed", label, time.monotonic() - started)

    stderr_reader.join(timeout=2)
    stderr = "".join(p for p in stderr_parts if p).strip()
    elapsed = time.monotonic() - started

    if result is None or result.get("is_error"):
        detail = (result or {}).get("result") or stderr or f"exit code {proc.returncode}"
        if elapsed >= timeout_seconds():
            detail = f"timed out after {timeout_seconds()}s"
        log.error("[%s] Claude failed after %.1fs (model %s): %.500s", label, elapsed, model, detail)
        raise LLMError(f"Claude failed: {detail}"[:500])

    usage = result.get("usage") or {}
    log.info(
        "[%s] model %s: first words after %.1fs, finished in %.1fs, prompt %d chars, reply %d chars, "
        "tokens in/out %s/%s%s",
        label,
        model,
        first_text_at or elapsed,
        elapsed,
        len(prompt),
        chars_out,
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        usage.get("output_tokens"),
        f", tools used: {', '.join(tools_used)}" if tools_used else "",
    )
    out["result"] = result


def stream(prompt, system_prompt, model=None, effort=None, label="chat", mcp_config=None, allowed_tools=()):
    """Yield Claude's reply piece by piece as it is written: str for text,
    ToolUse when it starts calling one of our tools.

    `mcp_config` is a {"mcpServers": {...}} dict giving Claude our tools;
    `allowed_tools` lists the full tool names it may call without asking.
    Raises LLMError if Claude fails or writes no text."""
    extra = []
    if mcp_config:
        extra += ["--mcp-config", json.dumps(mcp_config)]
    if allowed_tools:
        extra += ["--allowedTools", ",".join(allowed_tools)]
    chars = 0
    for item in _run(prompt, system_prompt, model or chat_model(), effort, label, extra, {}):
        if isinstance(item, str):
            chars += len(item)
        yield item
    if chars == 0:
        log.error("[%s] Claude returned an empty reply", label)
        raise LLMError("Claude returned an empty reply.")


def complete(prompt, system_prompt, model=None, effort=None, label="chat"):
    """The whole reply as one string."""
    return "".join(
        item for item in stream(prompt, system_prompt, model=model, effort=effort, label=label) if isinstance(item, str)
    )


def structured(prompt, system_prompt, schema, model=None, effort=None, label="json"):
    """Claude's answer as data matching the JSON `schema`."""
    out = {}
    for _ in _run(prompt, system_prompt, model or background_model(), effort, label,
                  ["--json-schema", json.dumps(schema)], out):
        pass
    result = out["result"]
    data = result.get("structured_output")
    if data is None:
        try:
            data = json.loads(result.get("result") or "")
        except json.JSONDecodeError as err:
            raise LLMError("Claude did not return the requested JSON.") from err
    return data
