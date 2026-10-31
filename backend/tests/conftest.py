import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ["EMBEDDINGS"] = "fake"  # no model download in tests

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Tests never start the real Claude. A test that forgets to pick a fake fails loudly."""
    import llm

    def refuse(*args, **kwargs):
        raise AssertionError("test tried to run real Claude; use the fake_claude or fake_bot fixture")

    monkeypatch.setattr(llm, "claude_executable", refuse)


@pytest.fixture
def fake_claude(monkeypatch):
    """Make llm.py run tests/fake_claude.py in the given mode instead of Claude."""
    import llm

    calls = []

    def use(mode):
        def command(system_prompt, model, effort=None, extra=()):
            calls.append({"system_prompt": system_prompt, "model": model, "effort": effort, "extra": list(extra)})
            return [sys.executable, str(FAKE_CLAUDE), mode]

        monkeypatch.setattr(llm, "_command", command)
        return calls

    return use


@pytest.fixture
def app(tmp_path, monkeypatch):
    from app import create_app
    from models import db

    application = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "TESTING": True,
        "GENERATE_TITLES": False,
        "MEMORY_JOBS": False,
        "BACKGROUND_INLINE": True,
        "SELF_URL": "http://127.0.0.1:5999",
    })
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    """An app context, for tests that call memory/jobs functions directly."""
    with app.app_context():
        yield


@pytest.fixture
def fake_bot(monkeypatch):
    """Replace the model with a scripted one. `seen` holds every prompt.Turn it got."""
    import bot

    class FakeBot:
        def __init__(self):
            self.seen = []
            self.reply = ["Hi", " back"]
            self.error = None
            self.facts_plan = {"add": [], "update": [], "delete": [], "used": [], "promote": []}
            self.facts_calls = []
            self.summary = "SUMMARY"
            self.summary_calls = []

        def stream_reply(self, turn):
            self.seen.append(turn)
            if self.error:
                raise bot.LLMError(self.error)
            yield from self.reply

        def extract_facts(self, known, found_messages, found_facts, user_text, reply_text):
            self.facts_calls.append({"known": known, "found_messages": found_messages,
                                     "found_facts": found_facts, "user": user_text, "reply": reply_text})
            return self.facts_plan

        def summarize(self, old_summary, messages):
            self.summary_calls.append((old_summary, messages))
            return self.summary

    fake = FakeBot()
    monkeypatch.setattr(bot, "stream_reply", fake.stream_reply)
    monkeypatch.setattr(bot, "extract_facts", fake.extract_facts)
    monkeypatch.setattr(bot, "summarize", fake.summarize)
    return fake
