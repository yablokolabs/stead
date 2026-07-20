"""Web-sourced claims must not become household truth on Stead's own say-so.

Stead can now search. A search result is neither something Kerstin stated nor
a verified outcome, so it takes the proposal route: inert until she approves
it, and never allowed to displace something she has said more recently.
"""
import sqlite3

import pytest

from helpers import call
from stead_mcp.store import SteadStore

SOURCE_URL = "https://school.example/term-dates"


def stored(server, name, scope="general"):
    """The fact as the model would read it back, or None."""
    for fact in call(server, "read_household_context")["facts"]:
        if fact["name"] == name and fact["scope"] == scope:
            return fact
    return None


# -- the approval gate --------------------------------------------------------

def test_the_suite_is_denied_outbound_connections():
    """The guard must fail loudly if it ever stops being applied."""
    import socket

    with pytest.raises(RuntimeError):
        socket.create_connection(("example.com", 80))


def test_a_proposed_fact_is_not_stored_until_it_is_approved(server):
    result = call(server, "propose_fact", name="term_ends", value="24 July",
                  source_url=SOURCE_URL)

    assert result["ok"] is True
    assert result["status"] == "pending"
    assert stored(server, "term_ends") is None


def test_approving_a_proposed_fact_stores_it(server):
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]

    call(server, "approve_proposal", ref=ref)

    assert stored(server, "term_ends")["value"] == "24 July"


def test_a_rejected_fact_never_reaches_the_household(server):
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]

    call(server, "reject_proposal", ref=ref)

    assert stored(server, "term_ends") is None


def test_a_fact_proposal_cannot_be_approved_twice(server):
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]
    call(server, "approve_proposal", ref=ref)

    second = call(server, "approve_proposal", ref=ref)

    assert second["ok"] is False
    # Not StaleProposal: the reason must be that it is already decided.
    assert second["error"] == "ApprovalRequired"


def test_an_empty_value_is_refused_at_the_boundary(server):
    result = call(server, "propose_fact", name="term_ends", value="   ",
                  source_url=SOURCE_URL)

    assert result["ok"] is False
    assert stored(server, "term_ends") is None


# -- Kerstin's word wins ------------------------------------------------------

def test_a_stale_approval_cannot_displace_what_kerstin_said_since(server):
    """She corrected the value after the proposal was made, then approved it."""
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]
    call(server, "confirm_fact", name="term_ends", value="22 July",
         provenance="Kerstin, 12 July")

    result = call(server, "approve_proposal", ref=ref)

    assert result["ok"] is False
    assert stored(server, "term_ends")["value"] == "22 July"


def test_she_can_always_correct_a_fact_that_came_from_the_web(server):
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]
    call(server, "approve_proposal", ref=ref)

    call(server, "confirm_fact", name="term_ends", value="22 July",
         provenance="Kerstin, 12 July")

    fact = stored(server, "term_ends")
    assert fact["value"] == "22 July"
    assert fact["source"] == "stated"
    # A URL left behind on a fact she stated would be a provenance lie.
    assert fact["source_url"] is None


def test_corroborating_what_she_said_does_not_relabel_it_as_web(server):
    """Agreeing with Kerstin must not turn her into a web source."""
    call(server, "confirm_fact", name="bin_day", value="Tuesday",
         provenance="Kerstin, 3 July")
    ref = call(server, "propose_fact", name="bin_day", value="Tuesday",
               source_url=SOURCE_URL)["ref"]

    call(server, "approve_proposal", ref=ref)

    fact = stored(server, "bin_day")
    assert fact["source"] == "stated"
    assert fact["provenance"] == "Kerstin, 3 July"


def test_a_fact_she_deleted_is_not_resurrected_by_a_stale_approval(server):
    """She stated it then removed it; the value is back where it started."""
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]
    call(server, "confirm_fact", name="term_ends", value="22 July",
         provenance="Kerstin, 12 July")
    call(server, "remove_fact", name="term_ends")

    result = call(server, "approve_proposal", ref=ref)

    assert result["ok"] is False
    assert stored(server, "term_ends") is None


# -- scopes stay separate -----------------------------------------------------

def test_a_proposal_in_one_scope_does_not_touch_another(server):
    call(server, "confirm_fact", name="term_ends", value="22 July",
         provenance="Kerstin", scope="general")
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL, scope="school")["ref"]

    assert call(server, "approve_proposal", ref=ref)["ok"] is True
    assert stored(server, "term_ends", scope="general")["value"] == "22 July"
    assert stored(server, "term_ends", scope="school")["value"] == "24 July"


def test_a_change_in_another_scope_does_not_make_a_proposal_stale(server):
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL, scope="school")["ref"]
    call(server, "confirm_fact", name="term_ends", value="22 July",
         provenance="Kerstin", scope="general")

    assert call(server, "approve_proposal", ref=ref)["ok"] is True


def test_a_proposal_must_say_where_it_came_from(server):
    assert call(server, "propose_fact", name="term_ends", value="24 July",
                source_url="  ")["ok"] is False
    assert call(server, "propose_fact", name="  ", value="24 July",
                source_url=SOURCE_URL)["ok"] is False


def test_a_web_sourced_fact_is_distinguishable_from_one_she_stated(server):
    """Provenance is free text the model writes; source is not."""
    call(server, "confirm_fact", name="bin_day", value="Tuesday",
         provenance="Kerstin, 3 July")
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]
    call(server, "approve_proposal", ref=ref)

    assert stored(server, "bin_day")["source"] == "stated"
    web_fact = stored(server, "term_ends")
    assert web_fact["source"] == "web"
    assert web_fact["source_url"] == SOURCE_URL


def test_a_proposal_for_an_unheld_fact_is_approved_normally(server):
    """Nothing to displace, so nothing to guard against."""
    ref = call(server, "propose_fact", name="bin_collection_change",
               value="moved to Wednesday", source_url=SOURCE_URL)["ref"]

    result = call(server, "approve_proposal", ref=ref)

    assert result["ok"] is True
    assert stored(server, "bin_collection_change")["value"] == "moved to Wednesday"


# -- migrating a database that already holds her data -------------------------

def test_migrating_an_existing_database_preserves_her_facts(tmp_path):
    """Kerstin's live database predates the source column."""
    db = tmp_path / "stead.sqlite"
    old = SteadStore(db)
    old.migrate()
    old.confirm_fact(name="bin_day", value="Tuesday", provenance="Kerstin")
    old.close()

    # Drop the column to reproduce the pre-upgrade shape, then migrate again.
    raw = sqlite3.connect(db)
    raw.execute("ALTER TABLE facts DROP COLUMN source")
    raw.execute("ALTER TABLE facts DROP COLUMN source_url")
    raw.commit()
    raw.close()

    upgraded = SteadStore(db)
    upgraded.migrate()
    facts = upgraded.read_household_context()["facts"]

    assert [f["name"] for f in facts] == ["bin_day"]
    assert facts[0]["value"] == "Tuesday"
    assert facts[0]["source"] == "stated"


# -- the guard must see every write, including its own -------------------------

def test_the_second_of_two_pending_proposals_is_refused(server):
    """Approving one is a write; the other proposal is now stale."""
    first = call(server, "propose_fact", name="bin_day", value="Thursday",
                 source_url=SOURCE_URL)["ref"]
    second = call(server, "propose_fact", name="bin_day", value="Friday",
                  source_url=SOURCE_URL)["ref"]
    call(server, "approve_proposal", ref=first)

    result = call(server, "approve_proposal", ref=second)

    assert result["ok"] is False
    assert stored(server, "bin_day")["value"] == "Thursday"


def test_a_delete_that_removed_nothing_does_not_invalidate_a_proposal(server):
    """remove_fact on a key never held must not kill an unrelated approval."""
    ref = call(server, "propose_fact", name="term_ends", value="24 July",
               source_url=SOURCE_URL)["ref"]
    # Nothing was ever stored under this name, so this deletes zero rows.
    call(server, "remove_fact", name="term_ends")

    result = call(server, "approve_proposal", ref=ref)

    assert result["ok"] is True
    assert stored(server, "term_ends")["value"] == "24 July"


def audit_kinds(store):
    """The audit log has no MCP read path by design, so read it from the store."""
    return [r["kind"] for r in store._rows(
        "SELECT kind FROM audit_events WHERE household = ? ORDER BY id",
        (store.household_id,))]


def test_rejecting_a_fact_is_recorded_as_a_fact_rejection(tmp_path):
    """The audit log is a security artefact; a fact must not log as a reminder."""
    store = SteadStore(tmp_path / "stead.sqlite")
    store.migrate()
    ref = store.propose_fact(name="term_ends", value="24 July",
                             source_url=SOURCE_URL)["ref"]

    store.reject_proposal(ref)

    kinds = audit_kinds(store)
    assert "fact_rejected" in kinds
    assert "reminder_rejected" not in kinds
