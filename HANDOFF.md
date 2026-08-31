# Handoff checklist

Work through this before inviting Kerstin. Nothing below may be assumed — each
line is either observed or not done.

## Offline — done and verified

- [x] Polaris healthy — `verify.sh` asserts on every run: service still active,
      Stead is a separate unit, Stead does not reference Polaris's `HERMES_HOME`,
      and the default profile still has `terminal` enabled
- [x] Polaris unit file byte-identical and PID 22680 stable across the whole
      build — confirmed by sha256 comparison during implementation.
      **Not re-asserted by `verify.sh`**; re-check manually if it matters:
      `systemctl --user show -p MainPID --value hermes-gateway.service`
- [x] Profile `stead-kerstin-demo` with its own config, env, memory, sessions,
      cron and skills
- [x] Sticky default not switched (`~/.hermes/active_profile` absent)
- [x] Service `hermes-gateway-stead-kerstin-demo.service` — distinct unit,
      `--profile stead-kerstin-demo`, `Restart=always`, no secrets in the unit
- [x] Forbidden toolsets disabled on `cli` and `telegram`, including `cronjob` —
      raw scheduling is replaced by `schedule_approved_reminder(ref)`
- [x] 24 Stead MCP tools registered in the Stead profile only
- [x] Ambient provider credentials scrubbed by the launcher; `copilot` and
      `qwen-oauth` suppressed in the Stead profile
- [ ] `openai-codex` remains resolvable from outside the profile — mitigated by
      provider pinning and zero fallbacks, **not fully closed**
      (see `SECURITY.md`, residual risk)
- [x] 131 tests passing
- [x] 68 verification checks passing (75 with web search on)
- [x] `hermes doctor` clean for the Stead profile
- [x] No secret, `.env`, database or venv tracked by git

## Before starting the gateway — yours to do

- [ ] Create the bot with BotFather; display name **Stead Preview**
- [ ] Get Kerstin's numeric Telegram ID (she can get it from `@userinfobot`)
- [ ] Create a Stead-owned application API key — **not** a personal
      subscription credential, not another agent's OAuth
- [ ] Enter all values in `~/.stead-demo/.env` (see the procedure in the final
      report — do not paste them into a chat), including one allowlisted
      `STEAD_TELEGRAM_CHAT_ID` for proactive reminder delivery
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
- It knows what she tells it here, plus what it looks up — no email, calendar,
  banking or shops
- It can search the web; it cannot open a page she links, and anything it finds
  stays a suggestion until she confirms it
- It cannot act in the outside world; it plans, tracks and reminds
- Reminders arrive in this Telegram chat only
- At most two proactive messages a day unless she asks
- Her household data is a local database on one VM and can be erased on request.
  Its searches go out through a search service, like anyone else's — it is told
  to keep her personal details out of them
