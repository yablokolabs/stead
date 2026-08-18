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
                 ├─ MCP: stead (stdio)
                 │    └─ stead_mcp.server → stead_mcp.store
                 │         └─ ~/.stead-demo/stead.sqlite   (mode 600)
                 ├─ plugin: stead_voice
                 │    ├─ STT  sarvam  → MCP: sarvam (stdio)               ──→ out
                 │    └─ TTS  sarvam  (registered, not selected)
                 ├─ model: application API key from ~/.stead-demo/.env   ──→ out
                 ├─ TTS: Hermes built-in `edge`, en-GB-RyanNeural        ──→ out
                 └─ web: SearXNG on 127.0.0.1                            ──→ out
                      └─ upstream search engines
```

Four edges leave this machine: the model API, web search, and — only when a
voice note is involved — Sarvam and Microsoft's Edge voice endpoint. Everything
else is local. The SearXNG hop is local, but SearXNG is a proxy — the query
itself reaches upstream engines. See `SECURITY.md`.

## Voice is an input modality, not a second agent

A Telegram voice note is transcribed and handed to the same turn a typed
message would have produced. Identity, household context, memory, tools and the
approval rules are therefore shared by construction rather than by being kept in
sync — nothing in `stead_voice/` knows what a calendar or a reminder is, and a
spoken "cancel the dentist" meets the same confirmation gate a typed one does.

```
voice note ─→ Sarvam STT ─→ transcript ─┐
                                        ├─→ the ordinary Stead turn ─→ text ─→ Edge TTS ─→ voice note
typed message ──────────────────────────┘                                └─→ text (typed input)
```

Both legs are Hermes provider interfaces (`stt.provider`, `tts.provider`), so a
different engine — including the realtime streaming path Stead will need as a
mobile app — is a provider swap, not a change to Stead.

## Stead hears in one voice and speaks in another

Sarvam transcribes; Edge speaks. That split is not aesthetic. Sarvam's Bulbul
model supports eleven Indic locales, and its English is `en-IN` — Sarvam
confirmed by email on 2026-08-11 that no British-accent voice exists in their
catalogue and that no accent parameter would produce one. Stead is a British
household's assistant, so the spoken half uses Hermes' built-in `edge` provider,
where `en-GB-RyanNeural` is a genuine British male voice.

The Sarvam TTS provider is written, tested and registered anyway. Switching is
`tts.provider: sarvam`, and the default speaker is already `ratan` — the voice
Sarvam named. The cost of keeping it is one unused registration; the cost of
not having it would be rediscovering the API's shape later.

Two things worth knowing about the Edge voice. It needs no key or account, but
it is the endpoint Edge's Read Aloud feature uses, reached through a client
token baked into the `edge-tts` library — not a supported commercial API with an
SLA. It is right for a private preview and wrong for a shipped product; a mobile
Stead should move to a licensed voice (Azure Speech carries the same
`en-GB-Ryan`). Sarvam's own TTS is unaffected by that concern and stays a
one-line fallback.

**The web MVP reaches the same conclusion by a different route.** It hears
through Sarvam, as this profile does, but speaks in the browser:
`speechSynthesis` with `lang: en-GB`, so the device picks a British voice and no
audio crosses the network at all. It briefly used OpenAI `tts-1` with the voice
`alloy`, which was neither British nor free; moving synthesis into the browser
removed about three seconds a turn and the divergence at once.

Sarvam turned out to be the better ear as well as the cheaper one. Given a sine
tone containing no speech, Whisper returned the word "You" in 1,851 ms; Sarvam
returned nothing in 1,182 ms. Inventing filler is worse than useless when the
transcript is handed straight to an agent.

The n8n `Stead Telegram` workflow is the exception: it has no browser, so it
synthesises with Sarvam `bulbul:v3`, speaker `ratan` — male, but `en-IN`, since
Sarvam still has no British voice. That workflow is a test harness rather than a
user surface, so the divergence is accepted there. See
`docs/CLOUDFLARE_MVP.md`.

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

Every fact also records **where it came from**. `source` is `'stated'` or
`'web'`, and unlike the free-text `provenance` string it is set by the code
path that stored the row, not by the model. Search results reach the database
only via `propose_fact` → `approve_proposal`; nothing a search returns is
written without Kerstin approving it.

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
