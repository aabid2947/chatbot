"""Facts about the user, their confidence scores, and search.

Scores (0 to 1) decide the tier:

    pinned   >= 0.6   sent with every message (within a size budget)
    known    0.3-0.6  not sent; Claude finds it with search_memory
    archived < 0.3    kept and searchable, hidden in the panel by default

A fact's stored `score` is its value on `touched_on`, the last day it was
created, used or edited. From then on it loses DECAY_PER_DAY for each *active*
day (a day the user chatted) that passes without it being used. Days off don't
count, and the live score is worked out when read, so no nightly job is needed.
Locked facts ("remember this") never fade. Scoring never deletes a fact.
"""

import bisect
import json
import logging

from sqlalchemy import select

import embeddings
from models import ActivityDay, Fact, FactEvent, Message, ToolCall, db, today, utcnow

log = logging.getLogger("chatbot.memory")

PIN_AT = 0.6
KNOWN_AT = 0.3
START_CLEAR = 0.7
START_PASSING = 0.45
PROMOTED = 0.65
REMEMBERED = 1.0
USE_BUMP = 0.15
# 0.1 of headroom above the pin line / 0.005 = a clearly stated fact stays
# pinned for about 20 active days (~3 weeks) without coming up.
DECAY_PER_DAY = 0.005
PINNED_BUDGET_CHARS = 2000  # about 500 tokens
DUPLICATE_SIMILARITY = 0.9

CATEGORIES = ["identity", "preference", "goal", "project", "decision", "constraint", "other"]


def _clamp(score):
    # Rounded so float noise never drops a score just under a tier line.
    return round(max(0.0, min(1.0, score)), 6)


# --- activity days and live scores -------------------------------------------


def record_activity():
    """Mark today as a day the user chatted."""
    day = today()
    if db.session.get(ActivityDay, day) is None:
        db.session.add(ActivityDay(day=day))
        db.session.commit()


class Scorer:
    """Works out live scores. Load once per request; activity days are few."""

    def __init__(self):
        self.days = sorted(db.session.scalars(select(ActivityDay.day)).all())
        self.today = today()

    def idle_days(self, touched_on):
        """Active days strictly after touched_on and before today."""
        start = bisect.bisect_right(self.days, touched_on)
        end = bisect.bisect_left(self.days, self.today)
        return max(0, end - start)

    def score(self, fact):
        if fact.locked:
            return fact.score
        return _clamp(fact.score - DECAY_PER_DAY * self.idle_days(fact.touched_on))


def tier(score):
    if score >= PIN_AT:
        return "pinned"
    if score >= KNOWN_AT:
        return "known"
    return "archived"


def all_facts_scored(scorer=None):
    """Every fact with its live score, best first: [(fact, score), ...]."""
    scorer = scorer or Scorer()
    facts = db.session.scalars(select(Fact)).all()
    scored = [(f, scorer.score(f)) for f in facts]
    scored.sort(key=lambda fs: (not fs[0].locked, -fs[1], fs[0].id))
    return scored


def pinned_facts(scorer=None):
    """The facts that go in every prompt: pinned tier, locked first, then by
    score, until the size budget is full."""
    chosen, used = [], 0
    for fact, score in all_facts_scored(scorer):
        if score < PIN_AT:
            break
        cost = len(fact.content) + 10
        if used + cost > PINNED_BUDGET_CHARS:
            continue
        chosen.append(fact)
        used += cost
    return chosen


def fact_to_dict(fact, score, in_prompt=None):
    return {
        "id": fact.id,
        "content": fact.content,
        "category": fact.category,
        "score": round(score, 3),
        "tier": tier(score),
        "locked": fact.locked,
        "origin": fact.origin,
        "use_count": fact.use_count,
        "last_used_at": fact.last_used_at.isoformat() + "Z" if fact.last_used_at else None,
        "in_prompt": in_prompt,
        "created_at": fact.created_at.isoformat() + "Z",
    }


# --- changing facts ----------------------------------------------------------


def _event(fact, kind, before, conversation_id=None, message_id=None):
    db.session.add(FactEvent(
        fact=fact, kind=kind, delta=fact.score - before, score_after=fact.score,
        conversation_id=conversation_id, message_id=message_id,
    ))


def _set_score(fact, scorer, new_score):
    before = scorer.score(fact)
    fact.score = _clamp(new_score)
    fact.touched_on = scorer.today
    return before


def create_fact(content, *, origin, score, category="other", locked=False,
                conversation_id=None, message_id=None, scorer=None):
    scorer = scorer or Scorer()
    fact = Fact(
        content=content.strip()[:1000], category=category if category in CATEGORIES else "other",
        score=_clamp(score), touched_on=scorer.today, locked=locked, origin=origin,
        source_conversation_id=conversation_id, source_message_id=message_id,
        embedding=embeddings.to_bytes(embeddings.embed_documents([content])[0]),
    )
    db.session.add(fact)
    _event(fact, "promoted" if origin == "promoted" else "created", 0.0, conversation_id, message_id)
    log.info("New fact (%s, %.2f%s): %s", origin, fact.score, ", locked" if locked else "", fact.content)
    return fact


def use_fact(fact, *, scorer, floor=None, conversation_id=None, message_id=None):
    """The user brought it up again, or a reply relied on it: score goes up.
    `floor` lifts a fact that had to be dug up to at least that score."""
    current = scorer.score(fact)
    new = current + USE_BUMP
    if floor is not None:
        new = max(new, floor)
    before = _set_score(fact, scorer, new if not fact.locked else fact.score)
    fact.use_count += 1
    fact.last_used_at = utcnow()
    _event(fact, "promoted" if floor is not None and before < floor else "used", before, conversation_id, message_id)
    log.info("Fact %d used: %.2f -> %.2f", fact.id, before, fact.score)


def edit_fact(fact, content, *, scorer, conversation_id=None, message_id=None, counts_as_use=True):
    fact.content = content.strip()[:1000]
    fact.embedding = embeddings.to_bytes(embeddings.embed_documents([fact.content])[0])
    if counts_as_use:
        use_fact(fact, scorer=scorer, conversation_id=conversation_id, message_id=message_id)
    else:
        before = _set_score(fact, scorer, scorer.score(fact))
        _event(fact, "edited", before, conversation_id, message_id)


def set_locked(fact, locked, *, scorer):
    if fact.locked == locked:
        return
    before = scorer.score(fact)
    fact.locked = locked
    fact.score = REMEMBERED if locked else _clamp(before)
    fact.touched_on = scorer.today
    _event(fact, "locked" if locked else "unlocked", before)


def find_duplicate(content, scorer=None):
    """An existing fact that says the same thing, if any."""
    facts = db.session.scalars(select(Fact)).all()
    if not facts:
        return None
    sims = embeddings.rank(embeddings.embed_query(content), [f.embedding for f in facts])
    best = int(sims.argmax())
    return facts[best] if sims[best] >= DUPLICATE_SIMILARITY else None


def remember(content, *, conversation_id=None, message_id=None):
    """User said "remember this": pinned at 1.0 and locked."""
    scorer = Scorer()
    existing = find_duplicate(content)
    if existing:
        set_locked(existing, True, scorer=scorer)
        use_fact(existing, scorer=scorer, conversation_id=conversation_id, message_id=message_id)
        return existing
    return create_fact(content, origin="remembered", score=REMEMBERED, locked=True,
                       conversation_id=conversation_id, message_id=message_id, scorer=scorer)


# --- search ------------------------------------------------------------------


def embed_missing_messages(conversation_id=None):
    """Give every message without a vector one. Returns how many were filled."""
    query = select(Message).where(Message.embedding.is_(None))
    if conversation_id:
        query = query.where(Message.conversation_id == conversation_id)
    missing = db.session.scalars(query).all()
    if missing:
        vectors = embeddings.embed_documents([m.content for m in missing])
        for message, vector in zip(missing, vectors):
            message.embedding = embeddings.to_bytes(vector)
        db.session.commit()
    return len(missing)


def search_history(query, *, conversation_id, before_message_id=None, all_chats=False, limit=6):
    """Older messages that match the query, best first: [(message, similarity)]."""
    embed_missing_messages(None if all_chats else conversation_id)
    stmt = select(Message)
    if not all_chats:
        stmt = stmt.where(Message.conversation_id == conversation_id)
    if before_message_id:
        stmt = stmt.where((Message.conversation_id != conversation_id) | (Message.id < before_message_id))
    messages = db.session.scalars(stmt).all()
    if not messages:
        return []
    sims = embeddings.rank(embeddings.embed_query(query), [m.embedding for m in messages])
    words = [w for w in query.lower().split() if len(w) > 3]
    for i, message in enumerate(messages):
        text = message.content.lower()
        if words and all(w in text for w in words):
            sims[i] += 0.15  # exact words matter too
    order = sims.argsort()[::-1][:limit]
    return [(messages[i], float(sims[i])) for i in order if sims[i] >= 0.3]


def search_facts(query, *, limit=8, exclude_ids=()):
    scorer = Scorer()
    excluded = set(exclude_ids)
    facts = [f for f in db.session.scalars(select(Fact)).all() if f.id not in excluded]
    if not facts:
        return []
    sims = embeddings.rank(embeddings.embed_query(query), [f.embedding for f in facts])
    order = sims.argsort()[::-1][:limit]
    return [(facts[i], scorer.score(facts[i]), float(sims[i])) for i in order if sims[i] >= 0.3]


def similar_facts(text, limit=10):
    return [f for f, _, _ in search_facts(text, limit=limit)]


# --- what was dug up this turn -----------------------------------------------


def log_tool_call(conversation_id, turn_message_id, tool, arguments, message_ids=(), fact_ids=()):
    db.session.add(ToolCall(
        conversation_id=conversation_id, turn_message_id=turn_message_id, tool=tool,
        arguments=json.dumps(arguments, ensure_ascii=False),
        found_message_ids=json.dumps(list(message_ids)), found_fact_ids=json.dumps(list(fact_ids)),
    ))
    db.session.commit()


SEARCH_TOOLS = ("search_history", "search_memory")


def dug_up_this_turn(turn_message_id):
    """(message ids found by search_history, fact ids found by search_memory).
    Only searches count: a fact saved by remember_fact wasn't dug up."""
    calls = db.session.scalars(
        select(ToolCall).where(ToolCall.turn_message_id == turn_message_id, ToolCall.tool.in_(SEARCH_TOOLS))
    ).all()
    message_ids, fact_ids = [], []
    for call in calls:
        message_ids += [i for i in json.loads(call.found_message_ids) if i not in message_ids]
        fact_ids += [i for i in json.loads(call.found_fact_ids) if i not in fact_ids]
    return message_ids, fact_ids
