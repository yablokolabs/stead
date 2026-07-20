"""MCP adapter exposing the Stead household store.

Design constraints:
  * Every parameter is a flat scalar (str / int). No nested objects, no
    anyOf/oneOf, no $ref — so the schemas survive both Anthropic and Gemini
    function-calling.
  * No tool accepts a household identifier. The household is bound when the
    store is constructed, from configuration the model cannot reach.
  * Logging records tool names and outcomes only, never argument values or
    household content.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from stead_mcp.store import ApprovalRequired, SteadStore, UnknownProposal

log = logging.getLogger("stead.mcp")

DEFAULT_DEMO_HOME = Path.home() / ".stead-demo"


def demo_home() -> Path:
    return Path(os.environ.get("STEAD_DEMO_HOME") or DEFAULT_DEMO_HOME)


def build_store() -> SteadStore:
    home = demo_home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = SteadStore(home / "stead.sqlite")
    store.migrate()
    return store


def _fail(exc: Exception) -> Dict[str, Any]:
    """Turn a refusal into a result the model can act on, without a stack trace."""
    return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}


def build_server(store: SteadStore | None = None) -> FastMCP:
    store = store or build_store()
    mcp = FastMCP("stead")

    def _audit(tool: str, ok: bool) -> None:
        log.info("tool=%s ok=%s", tool, ok)

    # -- context -------------------------------------------------------------

    @mcp.tool()
    def read_household_context() -> Dict[str, Any]:
        """Return confirmed household facts, members, events, goals and open tasks."""
        _audit("read_household_context", True)
        return {"ok": True, **store.read_household_context()}

    @mcp.tool()
    def unresolved_priorities(limit: int = 4) -> Dict[str, Any]:
        """Return at most four outstanding items. Never includes finished work."""
        _audit("unresolved_priorities", True)
        return {"ok": True, "items": store.unresolved_priorities(limit=limit)}

    # -- facts ---------------------------------------------------------------

    @mcp.tool()
    def confirm_fact(name: str, value: str, provenance: str,
                     scope: str = "general") -> Dict[str, Any]:
        """Record a fact Kerstin explicitly stated or confirmed.

        Use scope to separate domains, e.g. 'school' vs 'general', so a later
        correction in one domain does not overwrite the others.
        """
        if not name.strip() or not value.strip():
            return _fail(ValueError("name and value must be non-empty"))
        _audit("confirm_fact", True)
        return {"ok": True, **store.confirm_fact(name=name, value=value,
                                                 provenance=provenance, scope=scope)}

    @mcp.tool()
    def correct_fact(name: str, value: str, provenance: str,
                     scope: str = "general") -> Dict[str, Any]:
        """Replace a fact within one scope, leaving other scopes untouched."""
        if not name.strip() or not value.strip():
            return _fail(ValueError("name and value must be non-empty"))
        _audit("correct_fact", True)
        return {"ok": True, **store.correct_fact(name=name, value=value,
                                                 provenance=provenance, scope=scope)}

    @mcp.tool()
    def remove_fact(name: str, scope: str = "general") -> Dict[str, Any]:
        """Delete a fact that is no longer true."""
        store.remove_fact(name=name, scope=scope)
        _audit("remove_fact", True)
        return {"ok": True, "removed": name, "scope": scope}

    # -- goals, events, tasks -------------------------------------------------

    @mcp.tool()
    def create_goal(title: str, detail: str = "", due: str = "") -> Dict[str, Any]:
        """Create a durable goal, e.g. 'Friday dinner for six'."""
        if not title.strip():
            return _fail(ValueError("title must be non-empty"))
        _audit("create_goal", True)
        return {"ok": True, **store.create_goal(title=title, detail=detail,
                                                due=due or None)}

    @mcp.tool()
    def get_goal(goal_id: int) -> Dict[str, Any]:
        """Inspect one goal and its tasks."""
        goal = store.get_goal(goal_id)
        _audit("get_goal", goal is not None)
        if goal is None:
            return _fail(LookupError(f"no goal {goal_id}"))
        return {"ok": True, **goal}

    @mcp.tool()
    def create_event(title: str, occurs_on: str = "",
                     detail: str = "") -> Dict[str, Any]:
        """Record a dated event. Event details are not household preferences."""
        if not title.strip():
            return _fail(ValueError("title must be non-empty"))
        _audit("create_event", True)
        return {"ok": True, **store.create_event(title=title,
                                                 occurs_on=occurs_on or None,
                                                 detail=detail)}

    @mcp.tool()
    def add_task(title: str, due: str = "", goal_id: int = 0) -> Dict[str, Any]:
        """Add an actionable task, optionally attached to a goal."""
        if not title.strip():
            return _fail(ValueError("title must be non-empty"))
        _audit("add_task", True)
        return {"ok": True, **store.add_task(title=title, due=due or None,
                                             goal_id=goal_id or None)}

    @mcp.tool()
    def list_tasks(status: str = "") -> Dict[str, Any]:
        """List tasks, optionally filtered to 'open', 'complete' or 'dismissed'."""
        _audit("list_tasks", True)
        return {"ok": True, "tasks": store.list_tasks(status=status or None)}

    @mcp.tool()
    def complete_task(task_id: int) -> Dict[str, Any]:
        """Mark a task done. It stops appearing in briefings and reminders."""
        store.complete_task(task_id)
        _audit("complete_task", True)
        return {"ok": True, "task_id": task_id, "status": "complete"}

    @mcp.tool()
    def dismiss_task(task_id: int) -> Dict[str, Any]:
        """Drop a task Kerstin no longer wants. It will not resurface."""
        store.dismiss_task(task_id)
        _audit("dismiss_task", True)
        return {"ok": True, "task_id": task_id, "status": "dismissed"}

    # -- proposals and approval ----------------------------------------------

    @mcp.tool()
    def propose_reminder(task_id: int, fire_at: str,
                         message: str) -> Dict[str, Any]:
        """Propose a reminder. This schedules NOTHING until it is approved.

        Returns a short reference to quote back when asking Kerstin to approve.
        fire_at must be an ISO 8601 timestamp including a timezone offset.
        """
        try:
            result = store.propose_reminder(task_id=task_id, fire_at=fire_at,
                                            message=message)
        except ValueError as exc:
            _audit("propose_reminder", False)
            return _fail(exc)
        _audit("propose_reminder", True)
        return {"ok": True, **result,
                "note": "Not scheduled. Ask Kerstin to approve reference "
                        f"{result['ref']}."}

    @mcp.tool()
    def approve_proposal(ref: str) -> Dict[str, Any]:
        """Approve a proposal by its short reference. Only then is it scheduled."""
        try:
            result = store.approve_proposal(ref.strip().upper())
        except (UnknownProposal, ApprovalRequired) as exc:
            _audit("approve_proposal", False)
            return _fail(exc)
        _audit("approve_proposal", True)
        return {"ok": True, **result}

    @mcp.tool()
    def reject_proposal(ref: str) -> Dict[str, Any]:
        """Reject a proposal. It can never later be approved."""
        try:
            result = store.reject_proposal(ref.strip().upper())
        except (UnknownProposal, ApprovalRequired) as exc:
            _audit("reject_proposal", False)
            return _fail(exc)
        _audit("reject_proposal", True)
        return {"ok": True, **result}

    @mcp.tool()
    def list_approved_reminders() -> Dict[str, Any]:
        """List reminders that have been approved and are therefore scheduled."""
        _audit("list_approved_reminders", True)
        return {"ok": True, "reminders": store.list_approved_reminders()}

    @mcp.tool()
    def due_reminders(now: str) -> Dict[str, Any]:
        """Reminders due at `now` whose task is still outstanding.

        Returns an empty list when there is nothing worth saying — in that case
        say nothing at all.
        """
        try:
            items = store.due_reminders(now=now)
        except ValueError as exc:
            _audit("due_reminders", False)
            return _fail(exc)
        _audit("due_reminders", True)
        return {"ok": True, "reminders": items}

    @mcp.tool()
    def mark_delivered(reminder_id: int) -> Dict[str, Any]:
        """Record that a reminder was delivered, so it is never sent twice."""
        store.mark_delivered(reminder_id)
        _audit("mark_delivered", True)
        return {"ok": True, "reminder_id": reminder_id}

    # -- outcomes and audit ---------------------------------------------------

    @mcp.tool()
    def record_outcome(task_id: int, outcome: str,
                       verified_by: str) -> Dict[str, Any]:
        """Record a verified outcome. Only use this when there is real evidence."""
        if not verified_by.strip():
            return _fail(ValueError("verified_by must say how this was verified"))
        _audit("record_outcome", True)
        return {"ok": True, **store.record_outcome(task_id=task_id,
                                                   outcome=outcome,
                                                   verified_by=verified_by)}

    @mcp.tool()
    def add_audit_event(kind: str, detail: str = "") -> Dict[str, Any]:
        """Append an operational audit entry."""
        store.add_audit_event(kind=kind, detail=detail)
        _audit("add_audit_event", True)
        return {"ok": True, "kind": kind}

    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s stead.mcp %(levelname)s %(message)s")
    build_server().run()


if __name__ == "__main__":
    main()
