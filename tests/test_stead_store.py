"""Behavioural tests for the Stead household store.

These drive the store's public tool surface against a real temporary SQLite
file. Nothing here mocks the database, and nothing asserts on table layout,
SQL text or call counts — only on what a caller can observe.
"""
import pytest

from stead_mcp.store import ApprovalRequired, SteadStore, UnknownProposal


@pytest.fixture()
def store(tmp_path):
    s = SteadStore(tmp_path / "stead.sqlite")
    s.migrate()
    return s


# --- approval gate -----------------------------------------------------------

def test_proposed_reminder_is_not_active_until_approved(store):
    task = store.add_task(title="Buy vegetables", due="2026-07-23")
    proposal = store.propose_reminder(
        task_id=task["id"], fire_at="2026-07-22T18:00:00+01:00",
        message="Shopping tomorrow",
    )

    assert store.list_approved_reminders() == []

    store.approve_proposal(proposal["ref"])
    approved = store.list_approved_reminders()

    assert len(approved) == 1
    assert approved[0]["task_id"] == task["id"]


def test_rejected_proposal_never_becomes_a_reminder(store):
    task = store.add_task(title="Pay trip fee", due="2026-07-24")
    proposal = store.propose_reminder(
        task_id=task["id"], fire_at="2026-07-23T08:00:00+01:00", message="Pay",
    )

    store.reject_proposal(proposal["ref"])

    assert store.list_approved_reminders() == []
    with pytest.raises(ApprovalRequired):
        store.approve_proposal(proposal["ref"])


def test_unknown_proposal_reference_is_refused(store):
    with pytest.raises(UnknownProposal):
        store.approve_proposal("NOPE99")
    assert store.list_approved_reminders() == []


# --- corrections replace, and stay in scope ----------------------------------

def test_correction_replaces_only_the_scoped_preference(store):
    store.confirm_fact(
        name="reminder_timing", value="evening before", scope="general",
        provenance="stated by user",
    )
    store.confirm_fact(
        name="reminder_timing", value="evening before", scope="school",
        provenance="stated by user",
    )

    store.correct_fact(
        name="reminder_timing", scope="school", value="morning of",
        provenance="correction",
    )

    facts = {
        (f["name"], f["scope"]): f["value"]
        for f in store.read_household_context()["facts"]
    }
    assert facts[("reminder_timing", "school")] == "morning of"
    assert facts[("reminder_timing", "general")] == "evening before"


def test_correction_does_not_leave_a_duplicate_behind(store):
    store.confirm_fact(name="shopping_day", value="Thursday", scope="general",
                       provenance="stated")
    store.correct_fact(name="shopping_day", scope="general", value="Wednesday",
                       provenance="correction")

    matching = [
        f for f in store.read_household_context()["facts"]
        if f["name"] == "shopping_day" and f["scope"] == "general"
    ]
    assert len(matching) == 1
    assert matching[0]["value"] == "Wednesday"


# --- briefing ----------------------------------------------------------------

def test_briefing_caps_at_four_and_omits_finished_work(store):
    for n in range(7):
        store.add_task(title=f"Open task {n}", due="2026-07-25")
    done = [store.add_task(title=f"Done task {n}", due="2026-07-25")
            for n in range(3)]
    for t in done:
        store.complete_task(t["id"])

    priorities = store.unresolved_priorities(limit=4)

    assert len(priorities) == 4
    titles = {p["title"] for p in priorities}
    assert not any(t.startswith("Done task") for t in titles)


def test_completing_an_item_removes_it_from_the_next_briefing(store):
    store.add_task(title="Filler", due="2026-07-25")
    target = store.add_task(title="Consent form", due="2026-07-24")

    assert "Consent form" in {p["title"] for p in store.unresolved_priorities()}

    store.complete_task(target["id"])

    assert "Consent form" not in {p["title"] for p in store.unresolved_priorities()}


# --- reminders stop when the work is finished --------------------------------

@pytest.mark.parametrize("resolve", ["complete_task", "dismiss_task"])
def test_reminder_for_resolved_task_is_not_delivered(store, resolve):
    task = store.add_task(title="Packed lunch", due="2026-07-24")
    proposal = store.propose_reminder(
        task_id=task["id"], fire_at="2026-07-23T18:00:00+01:00", message="Lunch",
    )
    store.approve_proposal(proposal["ref"])

    getattr(store, resolve)(task["id"])

    assert store.due_reminders(now="2026-07-23T19:00:00+01:00") == []


def test_reminder_is_not_delivered_twice(store):
    task = store.add_task(title="Shopping", due="2026-07-23")
    proposal = store.propose_reminder(
        task_id=task["id"], fire_at="2026-07-22T18:00:00+01:00", message="Shop",
    )
    store.approve_proposal(proposal["ref"])

    first = store.due_reminders(now="2026-07-22T18:30:00+01:00")
    assert len(first) == 1
    store.mark_delivered(first[0]["id"])

    assert store.due_reminders(now="2026-07-22T19:00:00+01:00") == []


# --- the household binding is not negotiable ---------------------------------

def test_household_cannot_be_redirected_by_a_caller_supplied_id(store):
    store.confirm_fact(name="members", value="4", scope="general",
                       provenance="stated")

    with pytest.raises(TypeError):
        store.read_household_context(household_id="other-household")

    assert store.read_household_context()["household_id"] == store.household_id


def test_sql_metacharacters_are_stored_as_data(store):
    hostile = "'; DROP TABLE tasks; --"
    store.confirm_fact(name=hostile, value=hostile, scope="general",
                       provenance="stated")

    facts = store.read_household_context()["facts"]
    assert any(f["name"] == hostile and f["value"] == hostile for f in facts)
    store.add_task(title="still works", due="2026-07-25")


# --- setup is safe to re-run -------------------------------------------------

def test_migrate_is_idempotent_and_preserves_data(store):
    store.add_task(title="Survivor", due="2026-07-25")
    store.migrate()

    assert "Survivor" in {t["title"] for t in store.list_tasks()}
