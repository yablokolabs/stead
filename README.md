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
  server.py         the 21 MCP tools
  scheduler.py      the only component permitted to touch cron
  schema.sql        idempotent schema
identity/SOUL.md    who Stead is
skills/             stead-household-chief-of-staff — the operating loop
scripts/            setup, verify, check-secrets, reset
tests/              79 tests — store, MCP, scheduler gate, credential isolation
```

State lives in `$STEAD_DEMO_HOME` (default `~/.stead-demo`), mode 700, outside
git. The database is mode 600.

## Setup

```bash
./scripts/setup.sh            # idempotent; installs identity, skill, schema
$EDITOR ~/.stead-demo/.env    # fill in — see .env.example
./scripts/check-secrets.sh    # reports PRESENT/MISSING, never values
./scripts/verify.sh           # 57 offline checks
```

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
- `HANDOFF.md` — the checklist before showing Kerstin
