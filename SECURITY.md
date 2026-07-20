# Security

## Boundaries

**Polaris is out of scope, permanently.** Stead never reads, writes, stops,
restarts or inspects the `default` profile, `hermes-gateway.service`, or any
Polaris secret. Verification asserts Polaris's unit is active, its unit file is
byte-identical, and the default profile's tools are unchanged.

**No shared model credential.** Stead uses an application API key owned by the
demo, read only from `$STEAD_DEMO_HOME/.env`. It does not use Claude Code
authentication, a personal subscription, `~/.claude/.credentials.json`, the
`openai-codex` OAuth Polaris uses, or the Gemini CLI's OAuth. Sharing an OAuth
credential between two long-running agents causes single-use refresh-token
rotation failures — Hermes' own source documents this — and would put another
agent's availability at the mercy of this demo.

## Secrets

| | |
|---|---|
| Location | `$STEAD_DEMO_HOME/.env` (default `~/.stead-demo/.env`) |
| Mode | `600`, owner only |
| In git | never — `.gitignore` excludes it, verification asserts it |
| In the systemd unit | never — the unit sets `HERMES_HOME` and nothing else |
| In SQLite | never — no table stores a token, key or credential |
| In logs | never — the MCP server logs tool names and outcomes only |
| In this chat | never — `check-secrets.sh` reports PRESENT/MISSING only |

The verification suite greps the unit for `TOKEN|API_KEY|SECRET|PASSWORD` and
fails if any appears.

## Tool restriction

Enabled for `stead-kerstin-demo`: `memory`, `session_search`, `clarify`, plus
the 21 Stead MCP tools.

`cronjob` is **disabled**. Hermes' built-in scheduler tool lets the caller choose
both the delivery target and the job prompt, which routes around the proposal
and approval state machine entirely — a reminder could reach Telegram with no
approval row in SQLite. It is replaced by the single narrow tool
`schedule_approved_reminder(ref)`, which takes only a proposal reference and
refuses anything SQLite does not report as approved.

Disabled on both `cli` and `telegram`: `terminal`, `file`, `code_execution`,
`browser`, `computer_use`, `skills`, `delegation`, `todo`, `web`, `vision`,
`image_gen`, `tts`, `video`, `video_gen`, `x_search`, `context_engine`,
`homeassistant`, `spotify`, `yuanbao`.

`skills` is disabled deliberately: the Stead skill is installed on disk by
`setup.sh`, so the agent never needs — and never has — the ability to install or
author skills at runtime.

Hermes dangerous-command approvals stay enabled. YOLO mode is not used.
Verification checks all of this on every run.

## Scheduling

`stead_mcp/scheduler.py` is the only component permitted to create or remove
cron jobs, and it is not exposed as a general capability.

- **Authority** comes from SQLite: `proposal_status(ref)` must be `approved`.
  Pending, rejected, unknown and malformed references are all refused before any
  subprocess runs.
- **Destination** comes from `STEAD_ALLOWED_TELEGRAM_IDS`. The tool has no
  chat-id parameter; an ambiguous or absent configuration fails closed.
- **Instruction** is a fixed template. The only interpolated value is a
  reference matched against `^[A-Z0-9]{6}$`. Task titles and reminder text never
  reach the job prompt.
- **Execution** is a fixed interpreter path, an argv array, `shell=False`, a
  minimal environment and a timeout. No `--workdir`, `--script` or `--no-agent`.
- **Profile** is pinned to `stead-kerstin-demo`.
- **Idempotency**: `reminders.cron_job_id` is recorded only after a successful
  create. A repeat call returns the existing job instead of creating a second.
- **Suppression** is two-layered: `due_reminders` filters on task status and
  `delivered_at`, so completed or dismissed work is never delivered even if the
  cron cancellation fails.

## What the MCP server will not accept

- **No caller-supplied household id.** Bound at construction. A test walks every
  tool schema and fails if any parameter mentions "household".
- **Parameterised SQL only.** No string interpolation anywhere.
- **Validated inputs.** Empty names, empty titles, and unparseable timestamps are
  refused at the boundary and returned as `{"ok": false, ...}` rather than
  raising into the model.
- **Evidence required for outcomes.** `record_outcome` refuses an empty
  `verified_by`.

## What Stead cannot do

It is not connected to email, calendar, banking, shops or any external service.
It cannot make a payment, submit a form, or send a message anywhere except this
Telegram chat. Approving a reminder schedules a message; it does not perform an
action in the world. The identity file and the skill both state this, and
`record_outcome` requires stated evidence rather than an assumption.

## Access

Only Telegram IDs in `STEAD_ALLOWED_TELEGRAM_IDS` may interact. This is
Kerstin's numeric ID only.

**Not yet verified live.** Unauthorised-user rejection has not been tested
against the real bot, because no bot token is configured yet. It must be tested
before Kerstin is invited — see `HANDOFF.md`.

## Data

The database holds household facts, members, events, goals, tasks, reminders,
outcomes and an audit log. It does not hold raw transcripts. Delete it with
`scripts/reset.sh`, which demands the exact file path plus typed confirmation
and refuses directories.

## Reporting

Verification never claims a live capability it has not exercised. Telegram
delivery, unauthorised-user rejection and live cron delivery are reported as
blocked until they have actually run against the dedicated bot.

## Known residual risk: shared codex credential store

`hermes auth remove openai-codex` records a suppression in the Stead profile's
`auth.json`, and the profile's own credential pool is empty. Despite that, a
runtime `load_pool("openai-codex")` inside the Stead profile still resolves a
live OAuth access token that is **not** stored in the profile — so the read
reaches a credential store outside `HERMES_HOME`.

That token belongs to Polaris's provider. Closing it at source would mean
modifying `~/.hermes`, which is out of scope.

Mitigated within the Stead boundary:

- `STEAD_MODEL_PROVIDER` is pinned to `anthropic` or `gemini`; `openai-codex`
  is never selected.
- No fallback providers are configured, so nothing can fall back to it.
- The launcher fails closed (exit 78, no restart) if the pinned provider's
  application key is absent, rather than silently reaching for another
  credential.

**Residual:** if the Stead profile were ever reconfigured to use `openai-codex`,
it would find Polaris's token. Verification asserts the provider is pinned and
no fallback exists. This is a Hermes-level behaviour, not something this project
can fully close.
