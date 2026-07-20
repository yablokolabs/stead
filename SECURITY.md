# Security

## Boundaries

**Polaris is out of scope, permanently.** The Stead runtime never reads,
writes, stops or restarts the `default` profile, `hermes-gateway.service`, or
any Polaris secret. Offline verification asserts that Polaris remains active,
Stead uses a separate unit that does not reference Polaris's `HERMES_HOME`, and
the default profile's terminal tool remains enabled. Unit-file byte identity
and PID stability were checked manually during the build but are not rechecked
by `verify.sh`; `HANDOFF.md` records that distinction.

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
| In logs | never — the MCP server logs tool names and outcomes only, and the gateway runs at `WARNING`, below the level at which tool arguments are emitted |
| In this chat | never — `check-secrets.sh` reports PRESENT/MISSING only |

The verification suite greps the unit for `TOKEN|API_KEY|SECRET|PASSWORD` and
fails if any appears.

## Tool restriction

Enabled for `stead-kerstin-demo` on both `cli` and `telegram`: `clarify`,
`kanban`, `memory`, `session_search`, `web`, plus the 22 Stead MCP tools.

`cronjob` is **disabled**. Hermes' built-in scheduler tool lets the caller choose
both the delivery target and the job prompt, which routes around the proposal
and approval state machine entirely — a reminder could reach Telegram with no
approval row in SQLite. It is replaced by the single narrow tool
`schedule_approved_reminder(ref)`, which takes only a proposal reference and
refuses anything SQLite does not report as approved.

Disabled on both `cli` and `telegram`: `terminal`, `file`, `code_execution`,
`browser`, `computer_use`, `skills`, `delegation`, `todo`, `vision`,
`image_gen`, `tts`, `video`, `video_gen`, `x_search`, `context_engine`,
`homeassistant`, `spotify`, `yuanbao`.

`skills` is disabled deliberately: the Stead skill is installed on disk by
`setup.sh`, so the agent never needs — and never has — the ability to install or
author skills at runtime.

`web` was previously in the disabled list. That is now reversed deliberately —
see **Web search**. `x_search` stays disabled: it needs an xAI credential this
demo does not own, and it would add a second egress vendor for no benefit.

Hermes dangerous-command approvals stay enabled. YOLO mode is not used.
Verification checks all of this on every run.

## Web search

Stead can search the web. This is the only outbound path other than the model
API, and it is a reversal of the original no-egress posture.

**Backend.** A SearXNG instance on this VM, published on `127.0.0.1` only,
selected by `web.backend: searxng` and `SEARXNG_URL` in
`$STEAD_DEMO_HOME/.env`. There is no search-vendor account, no API key, and no
billed subscription tied to Kerstin.

**SearXNG is a metasearch proxy, not a local index.** It forwards each query to
upstream engines and aggregates their results. Query text does leave this
machine. What is avoided is a vendor relationship that could link a query to
Kerstin's identity or to a paid account. Anyone who would otherwise be told
"her data stays on the VM" must be told this instead.

**Extraction is unavailable.** SearXNG reports `supports_extract() == False`
and no other provider is configured, so `web_extract` fails closed. Stead can
read search results; it cannot fetch an arbitrary page.

**Availability is gated at the registry.** `check_web_api_key()` in Hermes
drops both web tools whenever no backend resolves. With `SEARXNG_URL` unset,
Stead has no web tools at all — that is the state this repository ships in, and
the agent correctly reports having no search capability.

**Searched claims are not household facts.** See **Fact provenance**.

## Fact provenance

`facts.source` is `'stated'` or `'web'`. Unlike `provenance`, which is free
text the model writes, `source` is set by the path that stored the row:
`confirm_fact` always writes `'stated'`, and only an approved `propose_fact`
writes `'web'` together with the originating URL.

- A proposed fact stores nothing until `approve_proposal` is called.
- Approval is refused if the fact was written or removed after the proposal was
  made. Staleness is detected against the audit log rather than by comparing
  values, so a change she later undid still invalidates the approval.
- Corroboration does not relabel. If a search agrees with a value she stated,
  the row keeps `source = 'stated'` and her original provenance string rather
  than being re-attributed to the web.

**Not enforced in code.** Nothing prevents the model calling `confirm_fact`
directly with something it read on the web. The skill forbids it; the schema
cannot detect it. This is the same trust level as the existing
pattern-is-a-hypothesis rule, and it is stated here rather than implied.

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

It is not connected to email, calendar, banking or shops. It cannot make a
payment, submit a form, or send a message anywhere except this Telegram chat.
Approving a reminder schedules a message; it does not perform an action in the
world.

It **can** search the web and read the results. It cannot fetch an arbitrary
page, and it cannot store what it finds without Kerstin approving it first. The
identity file and the skill both state this, and `record_outcome` requires
stated evidence rather than an assumption.

## Access

Only Telegram IDs in `STEAD_ALLOWED_TELEGRAM_IDS` may interact. This is
Kerstin's numeric ID only.

**Not yet verified live.** Unauthorised-user rejection has not been tested
against the real bot, because no bot token is configured yet. It must be tested
before Kerstin is invited — see `HANDOFF.md`.

## Data

The database holds household facts, members, events, goals, tasks, reminders,
outcomes and an audit log. Each fact carries the source that produced it. It
does not hold raw transcripts, and it does not store search results — only a
fact Kerstin approved, with its URL. Delete it with `scripts/reset.sh`, which
demands the exact file path plus typed confirmation and refuses directories.

`reset.sh` erases the database and nothing else. Search queries are not written
to the database, and at the gateway's normal verbosity they are not written to
any log either — which matters, because journald has no per-unit deletion and a
query recorded there could not be erased without destroying Polaris's logs too.
That is why the control is "never log the query", not "delete it afterwards".

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

## Known residual risk: web provider credentials outside HERMES_HOME

`agent/web_search_provider.get_provider_env()` resolves a provider credential
from `os.environ` first and then from `~/.hermes/.env` — Polaris's environment
file, outside Stead's `HERMES_HOME`. It is the same shape of hole as the codex
credential above, applied to search backends.

`~/.hermes/.env` holds no search-provider key today; this was checked by name,
without reading any value. But nothing stops one being added later, and if a
`FIRECRAWL_API_KEY` or similar appeared there, `check_web_api_key()` would light
up that backend inside the Stead profile without any change to this repository.

Mitigated within the Stead boundary:

- `web.backend` is pinned to `searxng`, which is selected explicitly rather than
  by the legacy preference order.
- `stead-launch.sh` scrubs `SEARXNG_URL` from the ambient environment before
  sourcing `$STEAD_DEMO_HOME/.env`, so the endpoint cannot be injected by
  whatever started the service.

**Residual:** a credential placed in Polaris's env file could still be resolved
if the pin were removed. Verification asserts the pin.

## Known residual risk: query logging at raised verbosity

`tools/web_tools.py` logs the full search query at `INFO`, and the SearXNG
provider issues a `GET` with the query in the URL, which `httpx` also logs at
`INFO`. The gateway runs at verbosity 0, which `gateway/run.py` maps to
`WARNING`, so neither line is emitted and no query has ever reached the journal.

**Residual:** starting the gateway with `-v` raises the root logger to `INFO`
and every household query begins landing in journald, where it cannot be
selectively erased. Do not run the Stead gateway verbosely against real
household data. This is Hermes-level behaviour; this project can pin the
verbosity it starts with, not the level a future operator chooses.
