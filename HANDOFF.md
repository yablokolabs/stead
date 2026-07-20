# Handoff checklist

Work through this before inviting Kerstin. Nothing below may be assumed — each
line is either observed or not done.

## Offline — done and verified

- [x] Polaris unchanged: unit active, unit file byte-identical, PID stable,
      default profile's tools untouched
- [x] Profile `stead-kerstin-demo` with its own config, env, memory, sessions,
      cron and skills
- [x] Sticky default not switched (`~/.hermes/active_profile` absent)
- [x] Service `hermes-gateway-stead-kerstin-demo.service` — distinct unit,
      `--profile stead-kerstin-demo`, `Restart=always`, no secrets in the unit
- [x] Forbidden toolsets disabled on `cli` and `telegram`
- [x] 20 Stead MCP tools registered in the Stead profile only
- [x] 33 tests passing
- [x] 42 verification checks passing
- [x] `hermes doctor` clean for the Stead profile
- [x] No secret, `.env`, database or venv tracked by git

## Before starting the gateway — yours to do

- [ ] Create the bot with BotFather; display name **Stead Preview**
- [ ] Get Kerstin's numeric Telegram ID (she can get it from `@userinfobot`)
- [ ] Create a Stead-owned application API key — **not** a personal
      subscription credential, not another agent's OAuth
- [ ] Enter all values in `~/.stead-demo/.env` (see the procedure in the final
      report — do not paste them into a chat)
- [ ] `./scripts/check-secrets.sh` reports `SECRET GATE: READY`
- [ ] `./scripts/verify.sh` still passes

## After starting — must be tested live, not assumed

- [ ] Gateway connects to Telegram
- [ ] Kerstin's ID can talk to Stead
- [ ] **An unlisted Telegram ID is refused** — test from a second account
- [ ] Welcome message reads as Stead, mentions the preview, promises no external
      changes without asking
- [ ] Journey 1: dinner — event detail not turned into a household preference
- [ ] Journey 2: school message — asks which child, three tasks, completions stick
- [ ] Journey 3: new session recalls facts; scoped correction doesn't flatten
      the general preference
- [ ] Journey 4: briefing is ≤ 4 items and excludes completed work
- [ ] Compressed-time reminder (3–5 min) arrives exactly once
- [ ] A completed task produces no further reminder
- [ ] `journalctl` for the Stead unit contains no secret and no message bodies
- [ ] Controlled restart: `systemctl --user restart` → service recovers, state
      intact
- [ ] Polaris still healthy after all of the above

## Tell Kerstin

- Early private preview; it will make mistakes
- It knows only what she tells it here — no email, calendar, banking or shops
- It cannot act in the outside world; it plans, tracks and reminds
- Reminders arrive in this Telegram chat only
- At most two proactive messages a day unless she asks
- Her data is a local database on one VM and can be erased on request
