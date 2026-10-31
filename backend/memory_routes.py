"""API for the memory panel: see, add, edit, lock and delete facts."""

import logging

from flask import Blueprint, abort, jsonify, request

import memory
from models import Fact, db

log = logging.getLogger("chatbot.memory")

bp = Blueprint("memory_api", __name__)


def _get(fact_id):
    fact = db.session.get(Fact, fact_id)
    if fact is None:
        abort(404, description="Fact not found")
    return fact


def _one(fact, scorer):
    in_prompt = fact.id in {f.id for f in memory.pinned_facts(scorer)}
    return memory.fact_to_dict(fact, scorer.score(fact), in_prompt)


@bp.get("/api/memory/facts")
def list_facts():
    """Every fact, best first, with its live score and tier. ?tier=pinned|known|archived filters."""
    scorer = memory.Scorer()
    in_prompt = {f.id for f in memory.pinned_facts(scorer)}
    wanted = request.args.get("tier")
    facts = [
        memory.fact_to_dict(fact, score, fact.id in in_prompt)
        for fact, score in memory.all_facts_scored(scorer)
        if not wanted or memory.tier(score) == wanted
    ]
    counts = {t: 0 for t in ("pinned", "known", "archived")}
    for fact, score in memory.all_facts_scored(scorer):
        counts[memory.tier(score)] += 1
    return jsonify(facts=facts, counts=counts, thresholds={"pinned": memory.PIN_AT, "known": memory.KNOWN_AT})


@bp.post("/api/memory/facts")
def add_fact():
    """Add a fact by hand. Like "remember this": pinned at 1.0 and locked."""
    content = ((request.get_json(silent=True) or {}).get("content") or "").strip()
    if not content:
        abort(400, description="Fact content is required")
    fact = memory.remember(content)
    db.session.commit()
    return jsonify(_one(fact, memory.Scorer())), 201


@bp.get("/api/memory/facts/<int:fact_id>")
def get_fact(fact_id):
    fact = _get(fact_id)
    data = _one(fact, memory.Scorer())
    data["history"] = [e.to_dict() for e in fact.events]
    return jsonify(data)


@bp.patch("/api/memory/facts/<int:fact_id>")
def change_fact(fact_id):
    """{"content": "..."} rewords it; {"locked": true|false} locks or unlocks it."""
    fact = _get(fact_id)
    body = request.get_json(silent=True) or {}
    scorer = memory.Scorer()
    if "content" in body:
        content = (body.get("content") or "").strip()
        if not content:
            abort(400, description="Fact content can't be empty")
        memory.edit_fact(fact, content, scorer=scorer, counts_as_use=False)
    if "locked" in body:
        memory.set_locked(fact, bool(body["locked"]), scorer=scorer)
    db.session.commit()
    return jsonify(_one(fact, memory.Scorer()))


@bp.delete("/api/memory/facts/<int:fact_id>")
def delete_fact(fact_id):
    fact = _get(fact_id)
    log.info("Fact %d deleted by the user: %s", fact.id, fact.content)
    db.session.delete(fact)
    db.session.commit()
    return "", 204
