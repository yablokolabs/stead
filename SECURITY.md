# Security

## Boundaries

**Polaris is out of scope, permanently.** The Stead runtime never reads,
writes, stops or restarts the `default` profile, `hermes-gateway.service`, or
any Polaris secret. On a host where the default gateway exists, offline
verification asserts that it remains active and that Stead uses a separate unit
which does not reference Polaris's `HERMES_HOME`. A new AWS host is allowed to
have no default gateway at all.

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

### The env file is parsed, never executed

`stead-launch.sh` reads the env file line by line and exports each assignment as
a literal string. It does not `source` it.

The distinction is not cosmetic. `source` executes the file, so every line of it
runs with the launcher's privileges *before* any credential check happens — and
the launcher runs as the user that owns the Telegram token, the model key and
the household database. A file whose only job is to carry seven assignments
should never be a code path.

On 2026-08-04 the live `~/.stead-demo/.env` was found to have grown from 784
bytes to 18,403: a 345-line vendor onboarding document had been appended to it,
written in the second person to AI agents, carrying an install command and an
OAuth authorization flow. Its origin was not established. Under `source` that
content would have executed, including its `$(...)` substitutions; in practice
`set -e` would have aborted the launcher on the first line of prose, so the
first symptom would have been a gateway that refused to restart.

The launcher now stops with `exit 78` and names the offending line number —
never its contents, which may themselves be a credential. Values containing
`$(...)` or backticks are exported as those literal characters. Quotes are
stripped, because `source` stripped them and valid files rely on it.

This closes execution, not writing. Anything able to write to a mode-`600` file
in a mode-`700` directory already holds the user's own privileges; the gate
ensures such a file cannot escalate itself into a running process, and that the
tampering is reported rather than silently absorbed. It is worth reading
alongside **Known residual risk: a second env file the gate never sees** below,
which anticipated a `FIRECRAWL_API_KEY` arriving through a path this project
does not inspect.

## VM migration boundary

The Git repository owns code, the locked Python dependency graph, generated
profile configuration, identity, skills, and host bootstrap logic. It never
owns live credentials, Telegram identifiers, household SQLite data, memories,
sessions, or cron state.

`scripts/export-state.sh` moves those private surfaces into one mode-`0600`
archive outside the checkout. The exporter briefly stops only Stead, snapshots
SQLite through the backup API, excludes `auth.json`, generated config, caches,
logs, binaries, and transient locks, and writes a per-file SHA-256 manifest.
Restore rejects loose archive permissions, links, path traversal, duplicate or
unexpected files, oversized archives, checksum changes, and corrupt SQLite
databases. It stages and validates every managed state surface before replacing
anything, then commits them as one rollback-protected transaction. Tracked
config is always regenerated with new-host paths after restore.

The bundle is sensitive and not encrypted by the repository. It must travel
over an encrypted private channel or encrypted object storage. A Git clone
without that bundle creates a clean Stead instance; it cannot recover the old
household or credentials. See `MIGRATION.md`.

## Tool restriction

Enabled for `stead-kerstin-demo` on both `cli` and `telegram`: `clarify`,
`memory`, `session_search`, `web`, plus the 22 Stead MCP tools.

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

`setup-searxng.sh --start` will not blindly start a pre-existing container. It
first verifies the pinned image digest, exact loopback port binding, expected
configuration mount, base URL, non-host networking, and restart policy.

**SearXNG is a metasearch proxy, not a local index.** It forwards each query to
upstream engines and aggregates their results. Query text does leave this
machine. What is avoided is a vendor relationship that could link a query to
Kerstin's identity or to a paid account. Anyone who would otherwise be told
"her data stays on the VM" must be told this instead.

**Extraction is a separate backend, and a separate boundary.** SearXNG reports
`supports_extract() == False`, so page reading is configured independently as
`web.extract_backend: firecrawl` and needs `FIRECRAWL_API_KEY` in
`$STEAD_DEMO_HOME/.env`. Unset the key and `web_extract` fails closed again,
leaving search intact.

The split is the point. Queries are the sensitive half — they describe what the
household wants to know — and they stay on the local proxy. Firecrawl sees only
URLs Stead chose to open, never a query. Setting `web.backend: firecrawl`
instead would hand it both, which is why the two are configured separately even
though one value would be shorter.

This is still a vendor relationship, unlike SearXNG: Firecrawl can associate the
pages Stead opens with the key holder. Anyone told "no search vendor sees
anything" would be told wrongly — the accurate statement is that no vendor sees
her queries, and one vendor sees the pages that were worth opening. The skill
forbids opening a link that is itself household detail, such as a personalised
school portal or a patient record, but that is a behavioural rule and not an
enforced boundary.

**Availability is gated at the registry.** `check_web_api_key()` in Hermes
drops both web tools whenever no backend resolves. With `SEARXNG_URL` unset,
Stead has no web tools at all — that is the state this repository ships in, and
the agent correctly reports having no search capability.

**Searched claims are not household facts.** See **Fact provenance**.

## Voice

Stead can be spoken to. This is the most sensitive egress in the system: not a
query about the household but the recorded voices of the people in it, and it is
worth stating plainly rather than folding into "the model API".

**Two vendors, only on a voice turn.** Inbound audio goes to Sarvam for
transcription; the reply is synthesized by Microsoft's Edge voice endpoint. A
typed conversation reaches neither. Nothing about voice changes what Stead knows
or is allowed to do — a spoken instruction becomes an ordinary turn and meets the
same approval gates, because `stead_voice/` transcribes and nothing else.

**Sarvam is an account relationship, unlike SearXNG.** Requests carry
`SARVAM_API_KEY`, so Sarvam can associate household audio with the key holder.
This is the opposite of the web-search posture, where the whole point was to
avoid a vendor tie. Anyone told "her voice is not linked to an account" would be
told wrongly. The key is Stead-owned, read only from `$STEAD_DEMO_HOME/.env`,
and the launcher scrubs any ambient `SARVAM_API_KEY` before that file is read,
so a key belonging to Polaris or a shell cannot be used by mistake.

**Microsoft is not an account relationship, and not a supported one either.**
The Edge voice needs no key: `edge-tts` reaches the endpoint behind Edge's Read
Aloud feature using a client token compiled into the library. Only Stead's reply
text is sent, never household audio or the user's identity. It is also not a
licensed API — no SLA, no terms covering production use, and Microsoft may
change or rate-limit it without notice. Acceptable for a private preview;
replace it with a licensed voice before Stead ships to anyone.

**Recordings are not kept indefinitely, but they are kept.** Hermes caches each
inbound voice note under `<profile>/cache/audio/` and prunes that directory
hourly, deleting anything older than 24 hours
(`cleanup_audio_cache`, `gateway/platforms/base.py`). So a recording of a
household member exists on disk for up to a day. Synthesis is stricter: the
Sarvam TTS provider writes into a per-call directory it removes on every exit
path, success or failure, so two people's audio never share a directory and
nothing survives the turn.

**Transcripts are household content.** They enter memory and session history on
the same terms as typed messages. Speech events are logged with durations and
character counts only — never the transcript itself.

**Failure is quiet, not silent.** With no `SARVAM_API_KEY` the voice path
reports itself unavailable and typed messages are unaffected. If synthesis
fails after Stead has already answered, the answer is still delivered as text
rather than discarded.

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
- **Destination** comes from the single protected `STEAD_TELEGRAM_CHAT_ID`,
  which the launcher verifies is numeric and present in
  `STEAD_ALLOWED_TELEGRAM_IDS`. The tool has no chat-id parameter; an absent,
  malformed or non-allowlisted destination fails closed.
  Hermes filters non-baseline variables from stdio MCP children, so the trusted
  scheduler reads only those two values from the owner-only mode-`600` Stead
  env file; it does not copy private IDs or credentials into profile config.
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

Only Telegram IDs in `STEAD_ALLOWED_TELEGRAM_IDS` may interact. Proactive
reminders are narrower: they always go to the one allowlisted destination in
`STEAD_TELEGRAM_CHAT_ID`.

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

## Known residual risk: a second env file the gate never sees

`agent/web_search_provider.get_provider_env()` resolves a provider credential
from `os.environ` first, then from `get_hermes_home() / ".env"`. Because the
launcher exports `HERMES_HOME` as the Stead profile, that fallback is
`~/.hermes/profiles/stead-kerstin-demo/.env` — **inside** the boundary, not
Polaris's file. It is not a leak across profiles.

It is, however, a second credential source that nothing in this project reads
or asserts. `check-secrets.sh` inspects `$STEAD_DEMO_HOME/.env` only, and the
launcher's ambient scrub happens before Hermes ever consults the profile file,
so the scrub does not close that path. A `FIRECRAWL_API_KEY` written into the
profile `.env` would light up that backend with nothing here changing, and the
secret gate would still report `READY`.

The same applies to `SEARXNG_URL`: `plugins/web/searxng/provider.py` resolves it
through the same helper, so it can come from the profile `.env` rather than the
protected one, and the scrub in `stead-launch.sh` does not prevent that.

Mitigated within the Stead boundary:

- `web.backend` is pinned to `searxng`, chosen explicitly rather than by the
  fallback preference order, so an unconfigured backend is not silently
  selected. `verify.sh` asserts the pin whether or not search is switched on.
- `stead-launch.sh` scrubs the search-provider variables from the ambient
  environment, which closes injection by whatever started the service.

**Residual:** the profile's own `.env` is outside the secret gate's view.
Anyone with write access to the profile directory can add a credential the gate
will not report. That is the same access level needed to change the pin itself,
so it widens no privilege — but the gate's `READY` should not be read as "these
are the only credentials in play".

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
