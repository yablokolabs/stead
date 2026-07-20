# Architecture

```
Kerstin
  └─ Telegram — private "Stead Preview" bot, allow-listed to her ID
       └─ hermes-gateway-stead-kerstin-demo.service   (systemd --user)
            └─ Hermes profile  stead-kerstin-demo
                 ├─ SOUL.md ................ Stead's identity
                 ├─ skills/ ................ stead-household-chief-of-staff
                 ├─ memory ................. a few communication preferences
                 ├─ cron ................... approved reminders, check-ins
                 └─ MCP: stead (stdio)
                      └─ stead_mcp.server → stead_mcp.store
                           └─ ~/.stead-demo/stead.sqlite   (mode 600)
                 └─ model: application API key from ~/.stead-demo/.env
```

## Why a Hermes profile

A named Hermes profile is a full `HERMES_HOME`. `~/.hermes/profiles/stead-kerstin-demo/`
has its own `config.yaml`, `.env`, `auth.json`, `memories/`, `sessions/`,
`cron/` and `skills/`. Isolation from Polaris is structural rather than
conventional — there is no shared mutable path to get wrong.

Per-profile services follow the `hermes-gateway-<profile>.service` convention,
so the Stead unit and Polaris's `hermes-gateway.service` are separate units with
separate PIDs, logs and lifecycles.

## Two memory layers

**Hermes memory** holds a small number of high-value communication preferences —
how Kerstin likes to be spoken to. It is bounded on purpose.

**The SQLite store** holds structured household state: facts, members, events,
goals, tasks, proposals, reminders, outcomes, audit events. This is what gets
retrieved at the start of every turn and every scheduled run.

Conversations are not archived wholesale. Only confirmed facts and operational
state are written, each with a timestamp and a provenance string.

## Facts are scoped

A fact is keyed on `(household, name, scope)`. `reminder_timing` in scope
`school` and in scope `general` are different facts.

This exists because of a specific failure: Kerstin says "morning reminders work
better for school items", and a naive implementation overwrites every reminder
preference she has. Scoping makes `correct_fact` surgical. Corrections upsert in
place, so a correction never leaves a contradictory duplicate behind.

## Approval is enforced in SQL, not in the prompt

`propose_reminder` writes a row to `proposals` with status `pending` and returns
a six-character reference. It creates nothing in `reminders`.

`approve_proposal(ref)` is the only path that inserts into `reminders`. A
rejected proposal can never be approved — `_pending()` raises `ApprovalRequired`
because its status is no longer `pending`.

This matters because prompt instructions can be talked around and SQL cannot. If
the model decided to skip asking, there is still no way for a reminder to exist
without an approval row. Tests assert the gate at the tool boundary.

An approval is permission to schedule. It is never evidence that an external
action succeeded — nothing here can make a payment or submit a form.

## Suppression

`due_reminders(now)` joins to `tasks` and returns a reminder only when:

- its proposal is `approved`, and
- `delivered_at IS NULL`, and
- the task is still `open` — not `complete`, not `dismissed`.

So completing a task silently retires its reminders, and `mark_delivered`
prevents a second send. The skill instructs Stead to trust this and not add a
"just checking" of its own.

## Scheduled runs start blind

Hermes cron begins a fresh session with no history. Every scheduled job
therefore loads the skill and calls `read_household_context` before doing
anything. If `due_reminders` comes back empty, the run sends nothing — silence
is the correct output, not a failure.

Times are `Europe/London`. `fire_at` is always a full ISO 8601 timestamp with an
offset, parsed into an aware datetime, so BST/GMT transitions don't shift a
reminder by an hour.

## Tool schemas

Every MCP tool takes flat scalars only — no nested objects, no `anyOf`/`oneOf`,
no `$ref`. Anthropic tolerates richer schemas; Gemini's function-calling subset
does not. Keeping to the intersection means switching provider is an `.env`
change rather than a rewrite. A test asserts this and will fail if someone adds
a structured parameter.

## The household cannot be redirected

`SteadStore` binds `household_id` at construction from configuration. No tool
accepts a household identifier, and a test walks every published schema to prove
no parameter contains "household". A compromised or confused model can read and
write the demo household, and nothing else.

All SQL is parameterised. A test stores `'; DROP TABLE tasks; --` as a fact name
and asserts it round-trips as data with the schema intact.
