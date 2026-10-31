from datetime import date, timedelta

import pytest

import memory
from models import ActivityDay, FactEvent, db

D0 = date(2026, 10, 1)


@pytest.fixture
def on_day(monkeypatch):
    """Pretend today is D0 + n days."""
    def set_day(n):
        monkeypatch.setattr(memory, "today", lambda: D0 + timedelta(days=n))
    set_day(0)
    return set_day


def active(*days):
    for n in days:
        db.session.merge(ActivityDay(day=D0 + timedelta(days=n)))
    db.session.commit()


def new_fact(content="The user likes old forts.", score=memory.START_CLEAR, **kw):
    fact = memory.create_fact(content, origin=kw.pop("origin", "extracted"), score=score, **kw)
    db.session.commit()
    return fact


def test_tiers():
    assert memory.tier(0.6) == "pinned"
    assert memory.tier(0.59) == "known"
    assert memory.tier(0.3) == "known"
    assert memory.tier(0.29) == "archived"


def test_score_fades_only_on_active_days_without_use(ctx, on_day):
    fact = new_fact(score=0.7)
    active(0, 1, 2, 10)  # three days off between day 2 and day 10 don't count...
    on_day(11)
    # ...so only days 1, 2 and 10 count: day 0 is the day it was made, day 11 is today.
    assert memory.Scorer().score(fact) == pytest.approx(0.7 - 3 * memory.DECAY_PER_DAY)


def test_today_does_not_count_until_it_is_over(ctx, on_day):
    fact = new_fact(score=0.7)
    active(0, 1)
    on_day(1)
    assert memory.Scorer().score(fact) == pytest.approx(0.7)


def test_a_clear_fact_stays_pinned_about_three_weeks_of_active_use(ctx, on_day):
    fact = new_fact(score=memory.START_CLEAR)
    active(*range(0, 40))
    on_day(21)
    assert memory.Scorer().score(fact) == pytest.approx(0.6)  # 20 idle active days
    assert memory.tier(memory.Scorer().score(fact)) == "pinned"
    on_day(22)
    assert memory.tier(memory.Scorer().score(fact)) == "known"


def test_locked_facts_never_fade(ctx, on_day):
    fact = new_fact(score=memory.REMEMBERED, locked=True, origin="remembered")
    active(*range(0, 60))
    on_day(60)
    assert memory.Scorer().score(fact) == 1.0


def test_using_a_fact_bumps_it_and_restarts_the_fade(ctx, on_day):
    fact = new_fact(score=0.7)
    active(0, 1, 2, 3, 4, 5)
    on_day(6)
    scorer = memory.Scorer()
    assert scorer.score(fact) == pytest.approx(0.675)
    memory.use_fact(fact, scorer=scorer)
    db.session.commit()
    assert fact.score == pytest.approx(0.825)
    assert fact.touched_on == D0 + timedelta(days=6)
    assert fact.use_count == 1 and fact.last_used_at is not None
    on_day(7)
    assert memory.Scorer().score(fact) == pytest.approx(0.825)  # day 6 itself is not idle


def test_score_is_capped_at_one(ctx, on_day):
    fact = new_fact(score=0.95)
    memory.use_fact(fact, scorer=memory.Scorer())
    assert fact.score == 1.0


def test_dug_up_fact_jumps_to_at_least_pinned_level(ctx, on_day):
    low = new_fact("The user once lived in Pune.", score=0.35)
    high = new_fact("The user is a teacher.", score=0.8)
    scorer = memory.Scorer()
    memory.use_fact(low, scorer=scorer, floor=memory.PROMOTED)
    memory.use_fact(high, scorer=scorer, floor=memory.PROMOTED)
    db.session.commit()
    assert low.score == pytest.approx(0.65)
    assert high.score == pytest.approx(0.95)
    assert [e.kind for e in low.events] == ["created", "promoted"]
    assert [e.kind for e in high.events] == ["created", "used"]


def test_pinned_facts_put_locked_first_then_score_and_respect_the_budget(ctx, on_day, monkeypatch):
    monkeypatch.setattr(memory, "PINNED_BUDGET_CHARS", 130)  # three short facts fit, the long one doesn't
    a = new_fact("The user is called Ravi.", score=0.7)
    b = new_fact("The user is vegetarian.", score=0.9)
    c = new_fact("Remember: the wifi password is on the fridge.", score=1.0, locked=True, origin="remembered")
    new_fact("The user likes tea.", score=0.5)  # known, never pinned
    new_fact("x" * 200, score=0.95)  # pinned tier but too big for the budget
    assert [f.id for f in memory.pinned_facts()] == [c.id, b.id, a.id]


def test_remember_locks_an_existing_duplicate_instead_of_adding(ctx, on_day):
    fact = new_fact("The user is allergic to peanuts.", score=0.4)
    again = memory.remember("The user is allergic to peanuts.")
    db.session.commit()
    assert again.id == fact.id
    assert fact.locked and fact.score == 1.0
    assert [e.kind for e in fact.events] == ["created", "locked", "used"]


def test_unlocking_keeps_the_score_and_lets_it_fade_again(ctx, on_day):
    fact = memory.remember("The user's flat number is 402.")
    db.session.commit()
    active(0, 1, 2)
    on_day(3)
    memory.set_locked(fact, False, scorer=memory.Scorer())
    db.session.commit()
    assert fact.score == 1.0
    active(3, 4)
    on_day(5)
    assert memory.Scorer().score(fact) == pytest.approx(1.0 - memory.DECAY_PER_DAY)


def test_every_change_is_logged(ctx, on_day):
    fact = new_fact(score=0.7)
    scorer = memory.Scorer()
    memory.use_fact(fact, scorer=scorer)
    memory.edit_fact(fact, "The user loves old forts.", scorer=scorer, counts_as_use=False)
    db.session.commit()
    events = db.session.query(FactEvent).filter_by(fact_id=fact.id).order_by(FactEvent.id).all()
    assert [(e.kind, round(e.score_after, 2)) for e in events] == [("created", 0.7), ("used", 0.85), ("edited", 0.85)]


def test_search_facts_finds_by_meaning_and_skips_excluded(ctx, on_day):
    forts = new_fact("The user likes old forts in Rajasthan.", score=0.5)
    tea = new_fact("The user drinks masala tea every morning.", score=0.5)
    found = memory.search_facts("old forts")
    assert found[0][0].id == forts.id
    assert all(f.id != forts.id for f, _, _ in memory.search_facts("old forts", exclude_ids=[forts.id]))
    assert tea.id in [f.id for f, _, _ in memory.search_facts("masala tea morning")]
