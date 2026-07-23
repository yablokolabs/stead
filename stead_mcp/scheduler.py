"""The only component permitted to create or remove Hermes cron jobs.

The raw `cronjob` toolset is disabled for the Stead profile, because it lets the
model choose both the delivery target and the prompt — which routes around the
proposal/approval state machine entirely. This module is the replacement: a
narrow path that will only place a job for a proposal SQLite says is approved.

What the model can influence: a six-character proposal reference. Nothing else.
The chat id comes from protected configuration, the prompt from a fixed
template, the profile is pinned, and the subprocess runs from an argv array with
no shell and a scrubbed environment.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from stead_mcp.store import ApprovalRequired, SteadStore, UnknownProposal, _moment

log = logging.getLogger("stead.scheduler")

PROFILE = "stead-kerstin-demo"
SKILL = "stead-household-chief-of-staff"

REF_PATTERN = re.compile(r"^[A-Z0-9]{6}$")
CHAT_ID_PATTERN = re.compile(r"^-?\d{1,20}$")
JOB_ID_PATTERN = re.compile(r"([A-Za-z0-9_-]{4,64})")

# The cron prompt is a template. The only interpolated value is a validated
# proposal reference, so the model cannot smuggle instructions into a job.
PROMPT_TEMPLATE = (
    "Scheduled Stead reminder check for proposal {ref}. "
    "Load the {skill} skill. Call read_household_context, then call "
    "due_reminders with the current time. If nothing is due, send nothing at "
    "all. If something is due, send one short message covering it, then call "
    "mark_delivered for each reminder delivered and add_audit_event."
)


class SchedulingRefused(Exception):
    """The request was well-formed but must not be scheduled."""


class SchedulerMisconfigured(Exception):
    """Configuration is missing or unsafe — fail closed rather than guess."""


def _protected_destination_config() -> tuple[str, str]:
    """Read only scheduling IDs from the owner-only Stead env file.

    Hermes deliberately filters parent environment variables before starting a
    stdio MCP server. The server therefore cannot inherit the destination from
    the gateway launcher. Read the two non-credential scheduling values from
    the protected file instead of weakening the MCP environment filter or
    duplicating private IDs in profile config.
    """
    demo_home = Path(os.environ.get("STEAD_DEMO_HOME",
                                    os.path.expanduser("~/.stead-demo")))
    env_file = demo_home / ".env"
    fd: Optional[int] = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(env_file, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SchedulerMisconfigured(
                "Protected Stead environment path must be a regular file."
            )
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise SchedulerMisconfigured(
                "Protected Stead environment file must have mode 600."
            )
        if info.st_uid != os.getuid():
            raise SchedulerMisconfigured(
                "Protected Stead environment file must be owned by this user."
            )
        stream = os.fdopen(fd, "r", encoding="utf-8")
        fd = None  # stream owns it from here
        with stream:
            lines = stream.read().splitlines()
    except SchedulerMisconfigured:
        raise
    except (OSError, UnicodeError) as exc:
        raise SchedulerMisconfigured(
            "Protected Stead environment file could not be read."
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)

    values: Dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        if name not in ("STEAD_TELEGRAM_CHAT_ID",
                        "STEAD_ALLOWED_TELEGRAM_IDS"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value.strip()

    return (values.get("STEAD_TELEGRAM_CHAT_ID", ""),
            values.get("STEAD_ALLOWED_TELEGRAM_IDS", ""))


def _run_argv(argv: List[str]) -> subprocess.CompletedProcess:
    """Execute an argv array with no shell and a deliberately minimal env."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.path.expanduser("~"),
        "HERMES_HOME": f"{os.path.expanduser('~')}/.hermes/profiles/{PROFILE}",
    }
    return subprocess.run(argv, shell=False, env=env, capture_output=True,
                          text=True, timeout=120, check=False)


def _default_hermes_cli() -> str:
    """Resolve the host-local Hermes launcher without pinning a VM username."""
    configured = os.environ.get("STEAD_HERMES_BIN", "").strip()
    if configured:
        return configured
    home_launcher = Path.home() / ".local" / "bin" / "hermes"
    if home_launcher.is_file():
        return str(home_launcher)
    return shutil.which("hermes") or str(home_launcher)


class SteadScheduler:
    def __init__(self, store: SteadStore, chat_id: str,
                 runner: Callable[[List[str]], subprocess.CompletedProcess] = _run_argv,
                 hermes_cli: Optional[str] = None):
        if not chat_id or not CHAT_ID_PATTERN.match(str(chat_id)):
            raise SchedulerMisconfigured(
                "Stead chat id is missing or malformed. It must come from "
                "configuration, never from a model argument."
            )
        self.store = store
        self.chat_id = str(chat_id)
        self._run = runner
        self.hermes_cli = hermes_cli or _default_hermes_cli()

    # -- configuration --------------------------------------------------------

    @classmethod
    def from_environment(cls, store: SteadStore, **kwargs) -> "SteadScheduler":
        """Build from protected configuration only.

        Interactive access may have several allowed Telegram IDs, while
        proactive delivery has one explicit destination. A stdio MCP process
        gets that pair from the owner-only Stead env file because Hermes strips
        non-baseline parent variables before spawning it.
        """
        chat_id = os.environ.get("STEAD_TELEGRAM_CHAT_ID", "").strip()
        allowed_raw = os.environ.get("STEAD_ALLOWED_TELEGRAM_IDS", "").strip()

        if not chat_id or not allowed_raw:
            file_chat_id, file_allowed = _protected_destination_config()
            chat_id = chat_id or file_chat_id
            allowed_raw = allowed_raw or file_allowed

        ids = [part.strip() for part in allowed_raw.split(",") if part.strip()]
        if not chat_id:
            raise SchedulerMisconfigured(
                "No Stead Telegram destination configured "
                "(STEAD_TELEGRAM_CHAT_ID)."
            )
        if chat_id not in ids:
            raise SchedulerMisconfigured(
                "Stead Telegram destination must name one allowed user."
            )
        return cls(store=store, chat_id=chat_id, **kwargs)

    # -- the trusted path -----------------------------------------------------

    def schedule_approved_reminder(self, ref: str) -> Dict[str, Any]:
        """Place a cron job for an approved proposal. Idempotent.

        Refuses anything not approved, anything whose task is already resolved,
        and anything already delivered.
        """
        ref = str(ref).strip().upper()
        if not REF_PATTERN.match(ref):
            raise UnknownProposal(f"Not a valid proposal reference: {ref!r}")

        status = self.store.proposal_status(ref)
        if status is None:
            raise UnknownProposal(f"No proposal with reference {ref}")
        if status != "approved":
            raise ApprovalRequired(
                f"Proposal {ref} is {status}. Only an approved proposal can be "
                f"scheduled."
            )

        reminder = self.store.reminder_for_proposal_ref(ref)
        if reminder is None or reminder["id"] is None:
            raise SchedulingRefused(f"Proposal {ref} has no reminder to schedule.")

        # Idempotency: the job id already recorded is the answer.
        if reminder["cron_job_id"]:
            return {"ref": ref, "cron_job_id": reminder["cron_job_id"],
                    "created": False, "reason": "already scheduled"}

        if reminder["delivered_at"]:
            raise SchedulingRefused(f"Reminder for {ref} has already been delivered.")
        if reminder["task_status"] not in (None, "open"):
            raise SchedulingRefused(
                f"Task for {ref} is {reminder['task_status']} — nothing to remind about."
            )

        schedule = self._schedule_expression(reminder["fire_at"])
        argv = self._build_argv(ref=ref, schedule=schedule)

        result = self._run(argv)
        if result.returncode != 0:
            log.warning("cron create failed ref=%s rc=%s", ref, result.returncode)
            raise SchedulingRefused(
                f"Scheduler rejected the job for {ref} (exit {result.returncode})."
            )

        job_id = self._extract_job_id(result.stdout)
        if not job_id:
            raise SchedulingRefused(
                f"Could not determine a job id for {ref}; refusing to record one."
            )

        self.store.attach_cron_job(reminder["id"], job_id)
        self.store.add_audit_event("reminder_scheduled", f"ref={ref} job={job_id}")
        log.info("scheduled ref=%s job=%s", ref, job_id)
        return {"ref": ref, "cron_job_id": job_id, "created": True}

    def cancel_for_task(self, task_id: int) -> List[str]:
        """Remove future jobs for a task that is no longer outstanding."""
        cancelled: List[str] = []
        for row in self.store.scheduled_jobs_for_task(int(task_id)):
            argv = [self.hermes_cli, "--profile", PROFILE,
                    "cron", "remove", str(row["cron_job_id"])]
            result = self._run(argv)
            if result.returncode == 0:
                self.store.clear_cron_job(row["id"])
                cancelled.append(str(row["cron_job_id"]))
                self.store.add_audit_event("reminder_cancelled",
                                           f"job={row['cron_job_id']}")
            else:
                # Delivery is still suppressed by due_reminders' task-status
                # filter, so a failed cancel degrades safely rather than firing.
                log.warning("cron remove failed job=%s rc=%s",
                            row["cron_job_id"], result.returncode)
        return cancelled

    # -- construction helpers -------------------------------------------------

    def _build_argv(self, ref: str, schedule: str) -> List[str]:
        """Fixed executable, pinned profile, templated prompt, no shell."""
        return [
            self.hermes_cli,
            "--profile", PROFILE,
            "cron", "create", schedule,
            PROMPT_TEMPLATE.format(ref=ref, skill=SKILL),
            "--deliver", f"telegram:{self.chat_id}",
            "--skill", SKILL,
            "--name", f"stead-reminder-{ref}",
            "--repeat", "1",
        ]

    @staticmethod
    def _schedule_expression(fire_at: str) -> str:
        """Minutes from now, as Hermes' relative schedule form."""
        delta = _moment(fire_at) - datetime.now().astimezone()
        minutes = max(1, int(delta.total_seconds() // 60))
        return f"{minutes}m"

    @staticmethod
    def _extract_job_id(stdout: str) -> Optional[str]:
        for line in (stdout or "").splitlines():
            if "job" in line.lower() or "id" in line.lower():
                match = JOB_ID_PATTERN.search(line.split(":")[-1].strip())
                if match:
                    return match.group(1)
        return None
