import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

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
        def command(system_prompt, model, effort=None):
            calls.append({"system_prompt": system_prompt, "model": model, "effort": effort})
            return [sys.executable, str(FAKE_CLAUDE), mode]

        monkeypatch.setattr(llm, "_command", command)
        return calls

    return use


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GENERATE_TITLES", "0")
    from app import create_app
    from models import db

    application = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "TESTING": True,
    })
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fake_bot(monkeypatch):
    """Replace the model with a scripted one. Returns the list of histories it saw."""
    import bot

    seen = []
    state = {"reply": ["Hi", " back"], "error": None}

    def stream_reply(history):
        seen.append(history)
        if state["error"]:
            raise bot.LLMError(state["error"])
        yield from state["reply"]

    monkeypatch.setattr(bot, "stream_reply", stream_reply)

    class FakeBot:
        pass

    fake = FakeBot()
    fake.seen = seen
    fake.state = state
    return fake
