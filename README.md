# Stead Preview

A private household chief-of-staff agent for one invited tester, running as an
isolated Hermes profile on this VM.

Stead learns household facts Kerstin explicitly shares, maintains goals and
tasks, proposes reminders, asks before scheduling them, follows up, and stays
quiet when there is nothing worth saying.

This is an early private preview, not a finished product.

## Isolation

Stead runs in its own Hermes profile with its own service. The existing Polaris
bot on the `default` profile is untouched.

| | Polaris | Stead |
|---|---|---|
| Profile | `default` | `stead-kerstin-demo` |
| HERMES_HOME | `~/.hermes` | `~/.hermes/profiles/stead-kerstin-demo` |
| Service | `hermes-gateway.service` | `hermes-gateway-stead-kerstin-demo.service` |
| Bot token | its own | its own |
| Model credential | its own | its own application API key |

Config, memory, sessions, cron, skills and the Telegram token are not shared.

**One documented exception.** The Stead profile can still resolve an
`openai-codex` OAuth token that lives outside its `HERMES_HOME` — the provider
Polaris uses. Closing it at source would mean modifying `~/.hermes`, which is
out of scope for this project. Stead never selects that provider: the model
provider is pinned, no fallback providers are configured, and the launcher fails
closed rather than reaching for another credential. See the residual-risk
section in `SECURITY.md`.

## Layout

```
stead_mcp/          SQLite-backed household state, exposed over MCP
  store.py          the store — household bound at construction
  server.py         the 22 MCP tools
  scheduler.py      the only component permitted to touch cron
  schema.sql        idempotent schema
stead_voice/        speech providers, loaded by Hermes as a plugin
  mcp_client.py     one short-lived Sarvam MCP process per call
  stt.py            transcription — Sarvam Saaras
  tts.py            synthesis — Sarvam Bulbul (registered, not selected)
identity/SOUL.md    who Stead is
skills/             stead-household-chief-of-staff — the operating loop
scripts/            bootstrap, setup, verify, backup/restore, reset, SearXNG
tests/              167 tests — store, MCP, scheduler, credentials, migration
                    isolation, fact provenance, speech
```

State lives in `$STEAD_DEMO_HOME` (default `~/.stead-demo`), mode 700, outside
git. The database is mode 600. The repository is sufficient to recreate the
software and host configuration; credentials and household history move in a
separate private bundle described in `MIGRATION.md`.

## Fresh-VM setup

Install the current Hermes Agent release and clone this repository, then:

```bash
./scripts/bootstrap-vm.sh --no-start
$EDITOR ~/.stead-demo/.env    # fill in from .env.example
./scripts/setup.sh --start
./scripts/verify.sh
```

The bootstrap creates `.venv` from the committed `uv.lock`, creates the
isolated profile, renders new-host paths, installs the user systemd service and
credential-enforcing drop-in, persists custom private-state/Hermes paths across
service restarts, and migrates the database.

For an old-VM to new-AWS migration, export private state first and pass the
bundle to bootstrap:

```bash
./scripts/export-state.sh
# privately copy the printed mode-600 bundle to the new VM
./scripts/bootstrap-vm.sh --restore /private/path/stead-private-*.tar.gz
```

See `MIGRATION.md` before deleting the old VM. Secrets and household data must
never be committed to this repository.

Individual setup commands remain idempotent:

```bash
./scripts/setup.sh            # identity, skill, config, schema, user service
./scripts/setup-searxng.sh    # optional; writes SearXNG config, starts nothing
$EDITOR ~/.stead-demo/.env    # fill in — see .env.example
./scripts/check-secrets.sh    # reports PRESENT/MISSING, never values
./scripts/verify.sh           # offline deployment and test verification
```

Web search is optional. Without `SEARXNG_URL`, Hermes drops `web_search` and
`web_extract` from the registry and Stead has no search capability at all — it
will say so accurately if asked. See the **Web search** section of
`SECURITY.md` before enabling it.

The gateway must not be started until `check-secrets.sh` reports
`SECRET GATE: READY`.

## Running

```bash
systemctl --user start   hermes-gateway-stead-kerstin-demo
systemctl --user stop    hermes-gateway-stead-kerstin-demo
systemctl --user status  hermes-gateway-stead-kerstin-demo
journalctl --user -u hermes-gateway-stead-kerstin-demo -f
```

Never run bare `hermes gateway ...` for Stead — that targets the default
profile, which is Polaris. Use the `stead-kerstin-demo` wrapper or the unit
name above.

## Talking to Stead

Send a Telegram voice note and Stead answers with one — a voice bubble carrying
the text as its caption. Type instead and the reply is text. Both arrive in the
same conversation, so a fact given by voice can be asked about by text and back
again without losing the thread; voice is transcribed into the ordinary turn
before Stead sees it.

Requires `SARVAM_API_KEY` in the protected env file, and `ffmpeg` on PATH.
Without the key, voice notes fail with a short apology and typed messages carry
on working.

Stead hears through Sarvam and speaks through Microsoft Edge's
`en-GB-RyanNeural`. Sarvam has no British voice, so the two halves use different
providers — see `ARCHITECTURE.md` for why, and for what to change if Indian
English is acceptable. The other British male is `en-GB-ThomasNeural`.

Per-chat overrides, if the default is ever wrong for someone:

```
/voice off      never answer aloud in this chat
/voice on       answer aloud when spoken to
/voice tts      answer aloud to everything, including typed messages
```

## Model credential

Stead uses a **Stead-owned application API key**, set in the protected env file
as `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` depending on `STEAD_MODEL_PROVIDER`.

It does **not** use Claude Code authentication, a personal subscription, or any
other agent's OAuth credential. `STEAD_MODEL_NAME` is validated against the
installed Hermes catalogue before the gate opens.

## Reset

```bash
./scripts/reset.sh ~/.stead-demo/stead.sqlite
```

Requires that exact file path and a typed confirmation. Refuses directories and
any other path. Deletes demo data only — never the profile or service.

## Documentation

- `ARCHITECTURE.md` — how the pieces fit
- `SECURITY.md` — the boundaries and why
- `DEMO_SCRIPT.md` — the four demonstration journeys
- `TROUBLESHOOTING.md` — when something misbehaves
- `MIGRATION.md` — secure backup and clean AWS restore
- `HANDOFF.md` — the checklist before showing Kerstin
