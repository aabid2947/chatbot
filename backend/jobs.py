"""Work done after each reply, in the background, so the user never waits:

1. Give the new messages search vectors.
2. Ask Claude (haiku) what to remember from the exchange, and apply it:
   new facts, corrections, forgotten facts, score bumps for facts that were
   used, and promotion of things that had to be dug out of history.
3. Fold old messages into the chat's rolling summary once enough pile up.

Jobs for one chat run one at a time, in order.
"""

import logging
import os
import threading
from collections import defaultdict

from sqlalchemy import update

import bot
import memory
from models import Conversation, Fact, Message, db

log = logging.getLogger("chatbot.jobs")

_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _lock_for(conversation_id):
    with _locks_guard:
        return _locks[conversation_id]


def keep_recent():
    """Messages always kept word for word, never folded into the summary."""
    return int(os.getenv("RECENT_MESSAGES", "12"))


def summary_batch():
    """How many older messages pile up before they are folded in."""
    return int(os.getenv("SUMMARY_BATCH", "8"))


def after_turn(app, conversation_id, user_message_id, reply_message_id):
    if not app.config["MEMORY_JOBS"]:
        return
    args = (app, conversation_id, user_message_id, reply_message_id)
    if app.config.get("BACKGROUND_INLINE"):
        _run(*args)
    else:
        threading.Thread(target=_run, args=args, name=f"memory-{conversation_id}", daemon=True).start()


def _run(app, conversation_id, user_message_id, reply_message_id):
    with _lock_for(conversation_id), app.app_context():
        try:
            for step, call in (
                ("embedding", lambda: memory.embed_missing_messages(conversation_id)),
                ("facts", lambda: update_facts(conversation_id, user_message_id, reply_message_id)),
                ("summary", lambda: roll_summary(conversation_id)),
            ):
                try:
                    call()
                except bot.LLMError as err:
                    db.session.rollback()
                    log.warning("Memory %s step skipped for chat %s: %s", step, conversation_id, err)
                except Exception:
                    db.session.rollback()
                    log.exception("Memory %s step failed for chat %s", step, conversation_id)
        finally:
            db.session.remove()


def update_facts(conversation_id, user_message_id, reply_message_id):
    user = db.session.get(Message, user_message_id)
    reply = db.session.get(Message, reply_message_id)
    if user is None or reply is None:
        return  # the chat was deleted meanwhile

    scorer = memory.Scorer()
    known = {f.id: f for f in memory.pinned_facts(scorer)}
    for fact in memory.similar_facts(f"{user.content}\n{reply.content}"):
        known.setdefault(fact.id, fact)
    found_message_ids, found_fact_ids = memory.dug_up_this_turn(user_message_id)
    found_messages = [m for m in (db.session.get(Message, i) for i in found_message_ids) if m]
    found_facts = [f for f in (db.session.get(Fact, i) for i in found_fact_ids) if f and f.id not in known]

    plan = bot.extract_facts(
        [(f.id, f.content, memory.tier(scorer.score(f))) for f in known.values()],
        [(m.id, m.role, m.content, m.created_at) for m in found_messages],
        [(f.id, f.content, memory.tier(scorer.score(f))) for f in found_facts],
        user.content,
        reply.content,
    )
    counts = apply_plan(
        plan,
        scorer=scorer,
        shown_facts={**known, **{f.id: f for f in found_facts}},
        dug_up_fact_ids=set(found_fact_ids),
        dug_up_message_ids={m.id for m in found_messages},
        conversation_id=conversation_id,
        message_id=user_message_id,
    )
    db.session.commit()
    if any(counts.values()):
        log.info("Memory after message %d: %s", user_message_id,
                 ", ".join(f"{n} {k}" for k, n in counts.items() if n))


def apply_plan(plan, *, scorer, shown_facts, dug_up_fact_ids, dug_up_message_ids, conversation_id, message_id):
    """Apply the extractor's answer. Only facts it was shown can be changed,
    and only messages that were actually dug up can be promoted."""
    where = {"conversation_id": conversation_id, "message_id": message_id}
    counts = {"new": 0, "updated": 0, "used": 0, "promoted": 0, "deleted": 0}
    touched = set()

    for fact_id in plan["delete"]:
        fact = shown_facts.pop(fact_id, None)
        if fact is not None:
            log.info("Fact %d deleted: %s", fact.id, fact.content)
            db.session.delete(fact)
            counts["deleted"] += 1

    for item in plan["update"]:
        fact = shown_facts.get(item.get("id"))
        content = (item.get("content") or "").strip()
        if fact is not None and content and fact.id not in touched:
            memory.edit_fact(fact, content, scorer=scorer, **where)
            touched.add(fact.id)
            counts["updated"] += 1

    for fact_id in plan["used"]:
        fact = shown_facts.get(fact_id)
        if fact is not None and fact.id not in touched:
            floor = memory.PROMOTED if fact.id in dug_up_fact_ids else None
            memory.use_fact(fact, scorer=scorer, floor=floor, **where)
            touched.add(fact.id)
            counts["promoted" if floor else "used"] += 1

    db.session.flush()  # so duplicate checks below see the changes

    for item in plan["add"]:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        duplicate = memory.find_duplicate(content)
        if duplicate is not None:
            if duplicate.id not in touched:
                memory.use_fact(duplicate, scorer=scorer, **where)
                touched.add(duplicate.id)
                counts["used"] += 1
            continue
        start = memory.START_CLEAR if item.get("strength") == "clear" else memory.START_PASSING
        fact = memory.create_fact(content, origin="extracted", score=start, category=item.get("category"),
                                  scorer=scorer, **where)
        db.session.flush()
        touched.add(fact.id)
        counts["new"] += 1

    for item in plan["promote"]:
        content = (item.get("content") or "").strip()
        source = item.get("message_id")
        if not content or source not in dug_up_message_ids:
            continue
        duplicate = memory.find_duplicate(content)
        if duplicate is not None:
            if duplicate.id not in touched:
                memory.use_fact(duplicate, scorer=scorer, floor=memory.PROMOTED, **where)
                touched.add(duplicate.id)
                counts["promoted"] += 1
            continue
        fact = memory.create_fact(content, origin="promoted", score=memory.PROMOTED, category=item.get("category"),
                                  scorer=scorer, conversation_id=conversation_id, message_id=source)
        db.session.flush()
        touched.add(fact.id)
        counts["promoted"] += 1

    return counts


def roll_summary(conversation_id):
    convo = db.session.get(Conversation, conversation_id)
    if convo is None:
        return
    upto = convo.summarized_upto_id or 0
    pending = [m for m in convo.messages if m.id > upto]
    if len(pending) < keep_recent() + summary_batch():
        return
    fold = pending[: -keep_recent()][:60]
    summary = bot.summarize(convo.summary, [{"role": m.role, "content": m.content} for m in fold])
    if not summary:
        return
    # A direct UPDATE that keeps updated_at, so summarising doesn't reorder the chat list.
    db.session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(summary=summary, summarized_upto_id=fold[-1].id, updated_at=Conversation.updated_at)
    )
    db.session.commit()
    log.info("Chat %s summary now covers %d more messages (up to message %d, %d words)",
             conversation_id, len(fold), fold[-1].id, len(summary.split()))
