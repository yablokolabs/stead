# Troubleshooting

Always scope commands to Stead. A bare `hermes gateway ...` targets the
`default` profile — that is Polaris. Use the `stead-kerstin-demo` wrapper or the
full unit name.

## Diagnose first

```bash
./scripts/verify.sh                  # 59 offline checks (63 with web search on)
./scripts/check-secrets.sh           # PRESENT/MISSING, never values
systemctl --user status hermes-gateway-stead-kerstin-demo
journalctl --user -u hermes-gateway-stead-kerstin-demo -n 50 --no-pager
stead-kerstin-demo doctor
```

## The service will not start

**Check the secret gate first.** A missing token or API key is the usual cause.
`check-secrets.sh` must say `SECRET GATE: READY`.

**`STEAD_MODEL_NAME` not in catalogue** — `check-secrets.sh` validates the model
id against the installed Hermes build. A model that exists at the vendor but not
in this Hermes version will fail here. Use one it lists.

**Wrong env file** — secrets go in `~/.stead-demo/.env`, not the repo, not the
profile directory, not the unit.

## The bot does not answer

1. Is the service active? `systemctl --user is-active hermes-gateway-stead-kerstin-demo`
2. Is your Telegram ID in `STEAD_ALLOWED_TELEGRAM_IDS`? Silence is the correct
   behaviour for an unlisted ID.
3. Is the token actually the Stead Preview bot's, and not another bot's? Two
   gateways polling one token fight over it and both behave erratically.

## It answers as Hermes, or mentions MCP/SQLite

`SOUL.md` was not installed, or was overwritten. Re-run `./scripts/setup.sh`
and start a new session — identity is loaded at session start.

## It has no idea what happened yesterday

Sessions are fresh; the household context is not. If it cannot recall confirmed
facts, it is failing to call `read_household_context`. Confirm the MCP server is
reachable:

```bash
stead-kerstin-demo mcp list
stead-kerstin-demo mcp test stead
```

If the server fails to start, run it directly to see the error:

```bash
STEAD_DEMO_HOME=~/.stead-demo PYTHONPATH=. .venv/bin/python -m stead_mcp.server
```

## A correction wiped out unrelated preferences

The correction was applied without a scope. `correct_fact` is per-scope by
design. Re-state both preferences explicitly and check with
`read_household_context` that two rows exist with different scopes.

## A reminder fired twice

`mark_delivered` was not called after delivery. `due_reminders` filters on
`delivered_at IS NULL`, so an undelivered-marked reminder stays due. Check the
audit log for a `reminder_delivered` entry.

## A completed task keeps being raised

Confirm it is actually resolved — `stead-kerstin-demo` → ask it to list tasks,
or inspect directly. `due_reminders` only returns reminders whose task is
`open`. If the task is `open`, `complete_task` never ran.

## A reminder is an hour off

`fire_at` was passed without a timezone offset. It must be full ISO 8601 with
`+01:00` (BST) or `+00:00` (GMT). Naive timestamps drift across the transition.

## Did I break Polaris?

```bash
systemctl --user is-active hermes-gateway.service     # expect: active
hermes profile list                                    # expect: ◆default, gateway running
hermes tools list --platform cli | grep terminal       # expect: ✓ enabled
```

Nothing in this project writes to `~/.hermes` outside `profiles/stead-kerstin-demo/`.
If Polaris looks wrong, stop and investigate before changing anything — do not
"fix" it from here.

## Start over cleanly

```bash
./scripts/reset.sh ~/.stead-demo/stead.sqlite   # data only
./scripts/setup.sh                              # recreate empty schema
```

To remove Stead entirely:

```bash
systemctl --user stop hermes-gateway-stead-kerstin-demo
stead-kerstin-demo gateway uninstall
hermes profile delete stead-kerstin-demo
rm -rf ~/.stead-demo
```

None of that touches Polaris.

## The launcher was bypassed after a restart

Hermes rewrites its own systemd unit on gateway startup
(`refresh_systemd_unit_if_needed`, `hermes_cli/gateway.py:2698`). Any `ExecStart`
edit made directly to `hermes-gateway-stead-kerstin-demo.service` is reverted
within a second of the service starting.

The launcher is therefore enforced by a drop-in, which the self-heal does not
touch:

```
~/.config/systemd/user/hermes-gateway-stead-kerstin-demo.service.d/override.conf
```

A copy is version-controlled at `systemd/override.conf`. If it is lost, the
gateway silently stops scrubbing ambient credentials. Confirm with:

```bash
systemctl --user show -p ExecStart --value \
  hermes-gateway-stead-kerstin-demo.service | grep stead-launch.sh
journalctl --user -u hermes-gateway-stead-kerstin-demo -n 20 | grep 'stead-launch:'
```

Expect a `stead-launch:` line on every start. Its absence means the drop-in is
missing — restore it and `systemctl --user daemon-reload`.
