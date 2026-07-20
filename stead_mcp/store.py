"""SQLite-backed household state for the Stead Preview demo.

The household is bound at construction. No method accepts a household
identifier, so a model cannot redirect reads or writes to another household.
Every statement is parameterised.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

DEMO_HOUSEHOLD = "stead-demo-household"

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike glyphs
_REF_LENGTH = 6


class SteadError(Exception):
    """Base for store-level refusals."""


class UnknownProposal(SteadError):
    """No proposal exists with that reference."""


class ApprovalRequired(SteadError):
    """The proposal is not in a state that can be approved."""


class StaleProposal(SteadError):
    """The fact changed after the proposal was made, so approval is not current."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _moment(value: str) -> datetime:
    """Parse an ISO timestamp into an aware datetime.

    Naive input is interpreted in the local zone so that comparisons across
    BST/GMT boundaries stay correct rather than drifting by an hour.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


class SteadStore:
    def __init__(self, db_path: Path | str, household_id: str = DEMO_HOUSEHOLD):
        self.db_path = Path(db_path)
        self.household_id = household_id
        self._db = sqlite3.connect(self.db_path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")

    # -- lifecycle ------------------------------------------------------------

    def migrate(self) -> None:
        """Create the schema. Safe to run repeatedly; preserves existing rows."""
        self._db.executescript(SCHEMA_PATH.read_text())
        self._add_missing_columns()
        self._db.commit()
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def _add_missing_columns(self) -> None:
        """Bring an older database up to the current shape without losing rows.

        CREATE TABLE IF NOT EXISTS silently skips a table that already exists,
        so a column added after first release needs an explicit ALTER.
        """
        wanted = {
            ("reminders", "cron_job_id"): "TEXT",
            ("facts", "source"): "TEXT NOT NULL DEFAULT 'stated'",
            ("facts", "source_url"): "TEXT",
        }
        for (table, column), coltype in wanted.items():
            existing = {r["name"] for r in self._rows(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    def close(self) -> None:
        self._db.close()

    def _write(self, sql: str, params: tuple) -> int:
        cur = self._db.execute(sql, params)
        self._db.commit()
        return int(cur.lastrowid or 0)

    def _rows(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        found = self._db.execute(sql, params).fetchone()
        return dict(found) if found else None

    # -- facts ----------------------------------------------------------------

    def _store_fact(self, name: str, value: str, provenance: str, scope: str,
                    source: str, source_url: Optional[str]) -> Dict[str, Any]:
        now = _now()
        self._write(
            """INSERT INTO facts (household, name, scope, value, provenance,
                                  source, source_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (household, name, scope) DO UPDATE SET
                   value = excluded.value,
                   provenance = excluded.provenance,
                   source = excluded.source,
                   source_url = excluded.source_url,
                   updated_at = excluded.updated_at""",
            (self.household_id, name, scope, value, provenance, source,
             source_url, now, now),
        )
        return {"name": name, "scope": scope, "value": value, "source": source}

    def confirm_fact(self, name: str, value: str, provenance: str,
                     scope: str = "general") -> Dict[str, Any]:
        """Record a fact the user explicitly stated or confirmed."""
        stored = self._store_fact(name, value, provenance, scope,
                                  source="stated", source_url=None)
        self.add_audit_event("fact_written", f"name={name} scope={scope}")
        return stored

    def _fact_row(self, name: str, scope: str) -> Optional[Dict[str, Any]]:
        return self._row(
            "SELECT value, source FROM facts "
            "WHERE household = ? AND name = ? AND scope = ?",
            (self.household_id, name, scope),
        )

    def _audit_high_water(self) -> int:
        """The latest audit id, used to detect any change made since.

        Comparing values cannot see a change that was undone — she states a
        fact and then deletes it, leaving the value as it was. Comparing
        against the audit log sees the round trip.
        """
        row = self._row("SELECT MAX(id) AS top FROM audit_events")
        return int((row or {}).get("top") or 0)

    def correct_fact(self, name: str, value: str, provenance: str,
                     scope: str = "general") -> Dict[str, Any]:
        """Replace a fact in one scope, leaving other scopes untouched."""
        return self.confirm_fact(name=name, value=value, scope=scope,
                                 provenance=provenance)

    def remove_fact(self, name: str, scope: str = "general") -> None:
        self._write(
            "DELETE FROM facts WHERE household = ? AND name = ? AND scope = ?",
            (self.household_id, name, scope),
        )
        self.add_audit_event("fact_removed", f"name={name} scope={scope}")

    def read_household_context(self) -> Dict[str, Any]:
        """Return the confirmed state. Deliberately takes no household id."""
        return {
            "household_id": self.household_id,
            "facts": self._rows(
                "SELECT name, scope, value, provenance, source, source_url, "
                "updated_at FROM facts WHERE household = ? ORDER BY name, scope",
                (self.household_id,),
            ),
            "members": self._rows(
                "SELECT name, detail FROM members WHERE household = ?",
                (self.household_id,),
            ),
            "events": self._rows(
                "SELECT id, title, occurs_on, detail FROM events "
                "WHERE household = ? ORDER BY occurs_on",
                (self.household_id,),
            ),
            "goals": self._rows(
                "SELECT id, title, detail, due, status FROM goals "
                "WHERE household = ? AND status = 'open'",
                (self.household_id,),
            ),
            "open_tasks": self.list_tasks(status="open"),
        }

    # -- goals, events, tasks -------------------------------------------------

    def create_goal(self, title: str, detail: str = "",
                    due: Optional[str] = None) -> Dict[str, Any]:
        goal_id = self._write(
            "INSERT INTO goals (household, title, detail, due, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.household_id, title, detail, due, _now()),
        )
        return {"id": goal_id, "title": title}

    def get_goal(self, goal_id: int) -> Optional[Dict[str, Any]]:
        goal = self._row(
            "SELECT id, title, detail, due, status FROM goals "
            "WHERE household = ? AND id = ?",
            (self.household_id, goal_id),
        )
        if goal:
            goal["tasks"] = self._rows(
                "SELECT id, title, due, status FROM tasks "
                "WHERE household = ? AND goal_id = ?",
                (self.household_id, goal_id),
            )
        return goal

    def create_event(self, title: str, occurs_on: Optional[str] = None,
                     detail: str = "") -> Dict[str, Any]:
        event_id = self._write(
            "INSERT INTO events (household, title, occurs_on, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.household_id, title, occurs_on, detail, _now()),
        )
        return {"id": event_id, "title": title}

    def add_task(self, title: str, due: Optional[str] = None,
                 goal_id: Optional[int] = None) -> Dict[str, Any]:
        task_id = self._write(
            "INSERT INTO tasks (household, goal_id, title, due, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.household_id, goal_id, title, due, _now()),
        )
        return {"id": task_id, "title": title, "due": due, "status": "open"}

    def _resolve_task(self, task_id: int, status: str) -> None:
        self._write(
            "UPDATE tasks SET status = ?, resolved_at = ? "
            "WHERE household = ? AND id = ?",
            (status, _now(), self.household_id, task_id),
        )

    def complete_task(self, task_id: int) -> None:
        self._resolve_task(task_id, "complete")

    def dismiss_task(self, task_id: int) -> None:
        self._resolve_task(task_id, "dismissed")

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            return self._rows(
                "SELECT id, goal_id, title, due, status FROM tasks "
                "WHERE household = ? AND status = ? ORDER BY due IS NULL, due",
                (self.household_id, status),
            )
        return self._rows(
            "SELECT id, goal_id, title, due, status FROM tasks "
            "WHERE household = ? ORDER BY due IS NULL, due",
            (self.household_id,),
        )

    def unresolved_priorities(self, limit: int = 4) -> List[Dict[str, Any]]:
        """The few things that actually need attention. Never finished work."""
        capped = max(1, min(int(limit), 4))
        return self._rows(
            "SELECT id, title, due, goal_id FROM tasks "
            "WHERE household = ? AND status = 'open' "
            "ORDER BY due IS NULL, due, id LIMIT ?",
            (self.household_id, capped),
        )

    # -- proposals and approval ----------------------------------------------

    def _new_ref(self) -> str:
        while True:
            ref = "".join(secrets.choice(_REF_ALPHABET) for _ in range(_REF_LENGTH))
            if not self._row("SELECT 1 FROM proposals WHERE ref = ?", (ref,)):
                return ref

    def propose_reminder(self, task_id: int, fire_at: str,
                         message: str) -> Dict[str, Any]:
        """Record an intent to remind. Creates nothing schedulable on its own."""
        _moment(fire_at)  # reject an unparseable time at the boundary
        ref = self._new_ref()
        payload = json.dumps({"task_id": int(task_id), "fire_at": fire_at,
                              "message": message})
        proposal_id = self._write(
            "INSERT INTO proposals (household, ref, kind, payload, created_at) "
            "VALUES (?, ?, 'reminder', ?, ?)",
            (self.household_id, ref, payload, _now()),
        )
        self.add_audit_event("reminder_proposed", f"ref={ref}")
        return {"id": proposal_id, "ref": ref, "status": "pending"}

    def _pending(self, ref: str) -> Dict[str, Any]:
        proposal = self._row(
            "SELECT * FROM proposals WHERE household = ? AND ref = ?",
            (self.household_id, ref),
        )
        if proposal is None:
            raise UnknownProposal(f"No proposal with reference {ref!r}")
        if proposal["status"] != "pending":
            raise ApprovalRequired(
                f"Proposal {ref} is already {proposal['status']}"
            )
        return proposal

    def propose_fact(self, name: str, value: str, source_url: str,
                     scope: str = "general") -> Dict[str, Any]:
        """Record a searched claim as a proposal. Stores no fact on its own.

        The value held at proposal time is captured so that a later approval
        cannot displace something Kerstin has said in the meantime.
        """
        ref = self._new_ref()
        payload = json.dumps({
            "name": name, "value": value, "scope": scope,
            "source_url": source_url,
            "seen_audit": self._audit_high_water(),
        })
        proposal_id = self._write(
            "INSERT INTO proposals (household, ref, kind, payload, created_at) "
            "VALUES (?, ?, 'fact', ?, ?)",
            (self.household_id, ref, payload, _now()),
        )
        self.add_audit_event("fact_proposed", f"ref={ref}")
        return {"id": proposal_id, "ref": ref, "status": "pending"}

    def _approve_fact(self, proposal: Dict[str, Any], ref: str) -> Dict[str, Any]:
        payload = json.loads(proposal["payload"])
        name, scope = payload["name"], payload["scope"]
        touched = self._rows(
            "SELECT 1 FROM audit_events WHERE household = ? AND id > ? "
            "AND kind IN ('fact_written', 'fact_removed') AND detail = ?",
            (self.household_id, payload["seen_audit"], f"name={name} scope={scope}"),
        )
        if touched:
            self.add_audit_event("fact_approval_stale", f"ref={ref}")
            raise StaleProposal(
                f"{name!r} changed since proposal {ref} was made. "
                "Ask Kerstin again rather than overwriting what she said."
            )
        self._write(
            "UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?",
            (_now(), proposal["id"]),
        )
        held = self._fact_row(name, scope)
        if held and held["source"] == "stated" and held["value"] == payload["value"]:
            # The search agreed with her. Corroboration is not a new source:
            # relabelling the row 'web' would erase that she is the one who
            # said it.
            self.add_audit_event("fact_corroborated", f"ref={ref}")
            return {"ref": ref, "status": "approved", "name": name,
                    "scope": scope, "value": payload["value"],
                    "source": "stated"}
        stored = self._store_fact(
            name, payload["value"],
            provenance=f"web search, approved by Kerstin (ref {ref})",
            scope=scope, source="web", source_url=payload["source_url"],
        )
        self.add_audit_event("fact_approved", f"ref={ref}")
        return {"ref": ref, "status": "approved", **stored}

    def approve_proposal(self, ref: str) -> Dict[str, Any]:
        proposal = self._pending(ref)
        if proposal["kind"] == "fact":
            return self._approve_fact(proposal, ref)
        self._write(
            "UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?",
            (_now(), proposal["id"]),
        )
        payload = json.loads(proposal["payload"])
        reminder_id = self._write(
            "INSERT INTO reminders (household, task_id, proposal_id, fire_at, "
            "message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.household_id, payload["task_id"], proposal["id"],
             payload["fire_at"], payload["message"], _now()),
        )
        self.add_audit_event("reminder_approved", f"ref={ref}")
        return {"reminder_id": reminder_id, "ref": ref, "status": "approved"}

    def reject_proposal(self, ref: str) -> Dict[str, Any]:
        proposal = self._pending(ref)
        self._write(
            "UPDATE proposals SET status = 'rejected', decided_at = ? WHERE id = ?",
            (_now(), proposal["id"]),
        )
        self.add_audit_event("reminder_rejected", f"ref={ref}")
        return {"ref": ref, "status": "rejected"}

    def list_approved_reminders(self) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT r.id, r.task_id, r.fire_at, r.message, r.delivered_at "
            "FROM reminders r JOIN proposals p ON p.id = r.proposal_id "
            "WHERE r.household = ? AND p.status = 'approved' ORDER BY r.fire_at",
            (self.household_id,),
        )

    # -- scheduling state -----------------------------------------------------

    def reminder_for_proposal_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        """The reminder created by an approved proposal, with its task status."""
        return self._row(
            "SELECT r.id, r.task_id, r.fire_at, r.message, r.cron_job_id, "
            "       r.delivered_at, p.status AS proposal_status, "
            "       t.status AS task_status "
            "FROM proposals p "
            "LEFT JOIN reminders r ON r.proposal_id = p.id "
            "LEFT JOIN tasks t ON t.id = r.task_id "
            "WHERE p.household = ? AND p.ref = ?",
            (self.household_id, ref),
        )

    def proposal_status(self, ref: str) -> Optional[str]:
        row = self._row(
            "SELECT status FROM proposals WHERE household = ? AND ref = ?",
            (self.household_id, ref),
        )
        return row["status"] if row else None

    def attach_cron_job(self, reminder_id: int, cron_job_id: str) -> None:
        """Record the Hermes job id. Presence of this is the idempotency key."""
        self._write(
            "UPDATE reminders SET cron_job_id = ? WHERE household = ? AND id = ?",
            (cron_job_id, self.household_id, reminder_id),
        )

    def clear_cron_job(self, reminder_id: int) -> None:
        self._write(
            "UPDATE reminders SET cron_job_id = NULL WHERE household = ? AND id = ?",
            (self.household_id, reminder_id),
        )

    def scheduled_jobs_for_task(self, task_id: int) -> List[Dict[str, Any]]:
        """Live cron jobs attached to a task — used to cancel on resolution."""
        return self._rows(
            "SELECT id, cron_job_id FROM reminders "
            "WHERE household = ? AND task_id = ? AND cron_job_id IS NOT NULL "
            "AND delivered_at IS NULL",
            (self.household_id, task_id),
        )

    # -- delivery -------------------------------------------------------------

    def due_reminders(self, now: str) -> List[Dict[str, Any]]:
        """Approved, undelivered reminders whose task is still outstanding."""
        cutoff = _moment(now)
        due = []
        for reminder in self._rows(
            "SELECT r.id, r.task_id, r.fire_at, r.message "
            "FROM reminders r "
            "JOIN proposals p ON p.id = r.proposal_id "
            "LEFT JOIN tasks t ON t.id = r.task_id "
            "WHERE r.household = ? AND p.status = 'approved' "
            "  AND r.delivered_at IS NULL "
            "  AND (t.status IS NULL OR t.status = 'open') "
            "ORDER BY r.fire_at",
            (self.household_id,),
        ):
            if _moment(reminder["fire_at"]) <= cutoff:
                due.append(reminder)
        return due

    def mark_delivered(self, reminder_id: int) -> None:
        self._write(
            "UPDATE reminders SET delivered_at = ? WHERE household = ? AND id = ?",
            (_now(), self.household_id, reminder_id),
        )
        self.add_audit_event("reminder_delivered", f"reminder={reminder_id}")

    # -- outcomes and audit ---------------------------------------------------

    def record_outcome(self, task_id: int, outcome: str,
                       verified_by: str) -> Dict[str, Any]:
        outcome_id = self._write(
            "INSERT INTO outcomes (household, task_id, outcome, verified_by, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (self.household_id, task_id, outcome, verified_by, _now()),
        )
        return {"id": outcome_id}

    def add_audit_event(self, kind: str, detail: str = "") -> None:
        self._write(
            "INSERT INTO audit_events (household, kind, detail, created_at) "
            "VALUES (?, ?, ?, ?)",
            (self.household_id, kind, detail, _now()),
        )
