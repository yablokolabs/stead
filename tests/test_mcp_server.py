"""Integration tests driving the Stead tools through the MCP server itself.

These go through the registered MCP tool surface — the same path the model
takes — rather than calling the store directly.
"""
import asyncio

import pytest

from stead_mcp.server import build_server
from stead_mcp.store import SteadStore

# Tools that must never appear, and the full set that must.
EXPECTED_TOOLS = {
    "read_household_context", "unresolved_priorities",
    "confirm_fact", "correct_fact", "remove_fact",
    "create_goal", "get_goal", "create_event",
    "add_task", "list_tasks", "complete_task", "dismiss_task",
    "propose_reminder", "approve_proposal", "reject_proposal",
    "schedule_approved_reminder",
    "list_approved_reminders", "due_reminders", "mark_delivered",
    "record_outcome", "add_audit_event",
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def server(tmp_path):
    store = SteadStore(tmp_path / "stead.sqlite")
    store.migrate()
    return build_server(store=store)


def call(server, tool, /, **kwargs):
    """Invoke an MCP tool and return its structured payload.

    `tool` is positional-only so a tool's own `name` argument cannot collide
    with this helper's parameters.
    """
    result = run(server.call_tool(tool, kwargs))
    # FastMCP returns (content_blocks, structured_result)
    payload = result[1] if isinstance(result, tuple) else result
    if isinstance(payload, dict) and "result" in payload and len(payload) == 1:
        return payload["result"]
    return payload


# -- surface ------------------------------------------------------------------

def test_only_the_intended_tools_are_exposed(server):
    names = {t.name for t in run(server.list_tools())}
    assert names == EXPECTED_TOOLS


def test_no_tool_accepts_a_household_identifier(server):
    for tool in run(server.list_tools()):
        params = set((tool.inputSchema or {}).get("properties", {}))
        assert not {p for p in params if "household" in p.lower()}, tool.name


def test_schemas_stay_within_the_portable_subset(server):
    """Flat scalars only, so the same schemas work on Anthropic and Gemini."""
    banned = {"anyOf", "oneOf", "allOf", "$ref", "not"}
    for tool in run(server.list_tools()):
        schema = tool.inputSchema or {}
        for prop, spec in schema.get("properties", {}).items():
            assert not banned & set(spec), f"{tool.name}.{prop} uses {set(spec) & banned}"
            assert spec.get("type") in {"string", "integer", "number", "boolean"}, \
                f"{tool.name}.{prop} is {spec.get('type')}"


# -- approval gate through MCP ------------------------------------------------

def test_reminder_is_not_scheduled_until_approved_via_mcp(server):
    task = call(server, "add_task", title="Buy vegetables", due="2026-07-23")
    proposed = call(server, "propose_reminder", task_id=task["id"],
                    fire_at="2026-07-22T18:00:00+01:00", message="Shopping")

    assert proposed["ok"] is True
    assert call(server, "list_approved_reminders")["reminders"] == []

    approved = call(server, "approve_proposal", ref=proposed["ref"])
    assert approved["ok"] is True
    assert len(call(server, "list_approved_reminders")["reminders"]) == 1


def test_rejected_then_approved_is_refused_via_mcp(server):
    task = call(server, "add_task", title="Pay fee")
    proposed = call(server, "propose_reminder", task_id=task["id"],
                    fire_at="2026-07-23T08:00:00+01:00", message="Pay")
    call(server, "reject_proposal", ref=proposed["ref"])

    retry = call(server, "approve_proposal", ref=proposed["ref"])

    assert retry["ok"] is False
    assert call(server, "list_approved_reminders")["reminders"] == []


def test_unknown_reference_is_refused_without_crashing(server):
    result = call(server, "approve_proposal", ref="ZZZZZZ")
    assert result["ok"] is False
    assert result["error"] == "UnknownProposal"


def test_lowercase_reference_is_accepted(server):
    task = call(server, "add_task", title="Consent form")
    proposed = call(server, "propose_reminder", task_id=task["id"],
                    fire_at="2026-07-23T08:00:00+01:00", message="Sign")

    assert call(server, "approve_proposal", ref=proposed["ref"].lower())["ok"] is True


# -- memory discipline through MCP -------------------------------------------

def test_scoped_correction_leaves_other_scopes_alone_via_mcp(server):
    for scope in ("general", "school"):
        call(server, "confirm_fact", name="reminder_timing",
             value="evening before", provenance="stated", scope=scope)

    call(server, "correct_fact", name="reminder_timing", value="morning of",
         provenance="correction", scope="school")

    facts = {(f["name"], f["scope"]): f["value"]
             for f in call(server, "read_household_context")["facts"]}
    assert facts[("reminder_timing", "school")] == "morning of"
    assert facts[("reminder_timing", "general")] == "evening before"


def test_empty_values_are_rejected(server):
    assert call(server, "confirm_fact", name="  ", value="x",
                provenance="stated")["ok"] is False
    assert call(server, "add_task", title="")["ok"] is False
    assert call(server, "create_goal", title="")["ok"] is False


def test_outcome_requires_stated_evidence(server):
    task = call(server, "add_task", title="Pay £12")
    assert call(server, "record_outcome", task_id=task["id"],
                outcome="paid", verified_by="")["ok"] is False
    assert call(server, "record_outcome", task_id=task["id"],
                outcome="paid", verified_by="Kerstin confirmed")["ok"] is True


# -- briefing and suppression through MCP -------------------------------------

def test_briefing_never_exceeds_four_or_includes_finished_work(server):
    for n in range(6):
        call(server, "add_task", title=f"Open {n}", due="2026-07-25")
    done = call(server, "add_task", title="Finished", due="2026-07-21")
    call(server, "complete_task", task_id=done["id"])

    items = call(server, "unresolved_priorities", limit=10)["items"]

    assert len(items) <= 4
    assert "Finished" not in {i["title"] for i in items}


@pytest.mark.parametrize("resolver", ["complete_task", "dismiss_task"])
def test_resolved_task_stops_its_reminder_via_mcp(server, resolver):
    task = call(server, "add_task", title="Packed lunch")
    proposed = call(server, "propose_reminder", task_id=task["id"],
                    fire_at="2026-07-23T18:00:00+01:00", message="Lunch")
    call(server, "approve_proposal", ref=proposed["ref"])
    call(server, resolver, task_id=task["id"])

    assert call(server, "due_reminders",
                now="2026-07-23T19:00:00+01:00")["reminders"] == []


def test_delivered_reminder_is_not_returned_again_via_mcp(server):
    task = call(server, "add_task", title="Shopping")
    proposed = call(server, "propose_reminder", task_id=task["id"],
                    fire_at="2026-07-22T18:00:00+01:00", message="Shop")
    call(server, "approve_proposal", ref=proposed["ref"])

    due = call(server, "due_reminders", now="2026-07-22T18:30:00+01:00")["reminders"]
    assert len(due) == 1
    call(server, "mark_delivered", reminder_id=due[0]["id"])

    assert call(server, "due_reminders",
                now="2026-07-22T20:00:00+01:00")["reminders"] == []


def test_malformed_timestamp_is_refused_not_crashed(server):
    task = call(server, "add_task", title="Anything")
    result = call(server, "propose_reminder", task_id=task["id"],
                  fire_at="next tuesday-ish", message="x")
    assert result["ok"] is False


# -- goals and events ---------------------------------------------------------

def test_goal_carries_its_tasks_via_mcp(server):
    goal = call(server, "create_goal", title="Friday dinner for six",
                detail="two vegetarian", due="2026-07-24")
    call(server, "add_task", title="Shop", due="2026-07-23", goal_id=goal["id"])

    fetched = call(server, "get_goal", goal_id=goal["id"])

    assert fetched["title"] == "Friday dinner for six"
    assert "Shop" in {t["title"] for t in fetched["tasks"]}


def test_missing_goal_is_reported_not_raised(server):
    assert call(server, "get_goal", goal_id=999)["ok"] is False


def test_event_is_recorded_separately_from_preferences(server):
    call(server, "create_event", title="Year 5 museum trip",
         occurs_on="2026-07-29", detail="packed lunch needed")

    context = call(server, "read_household_context")

    assert "Year 5 museum trip" in {e["title"] for e in context["events"]}
    assert context["facts"] == []


def test_removing_a_fact_takes_it_out_of_context(server):
    call(server, "confirm_fact", name="shopping_day", value="Thursday",
         provenance="stated")
    call(server, "remove_fact", name="shopping_day")

    assert call(server, "read_household_context")["facts"] == []


def test_audit_events_are_accepted(server):
    assert call(server, "add_audit_event", kind="briefing_sent",
                detail="4 items")["ok"] is True
