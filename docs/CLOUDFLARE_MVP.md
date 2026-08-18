# Cloudflare web MVP

The delivery path for Stead on the web and, once installed to a home screen, on
a phone. It runs alongside the Telegram/Hermes preview and shares nothing with
it: no code, no database, no credentials, no service.

This file explains **why** the pieces are shaped this way.
`docs/WEB_MVP_RUNBOOK.md` is the operational companion: what exists, how to
rebuild it from nothing, and the failures that look like something they are
not. Start there if you are recovering rather than reading.

```
                    Internet
                       │
                       ▼
              Cloudflare DNS
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
   Cloudflare Pages          Cloudflare Worker
   web/  → dist/             worker/  stead-gateway
   Stead PWA                        │
          │                  verifies the Supabase JWT
          │                  validates the request
          │                  supplies trusted identity
          │                  holds the n8n webhook
          │                         │
          └────────────┬────────────┘
                       │
                       ▼
                   n8n Cloud
                   Stead agent — Gemini
                   speech in — Sarvam
```

Supabase is the identity provider and nothing else: the Worker verifies its
tokens, and the browser signs in against it. Nothing writes household data to
it yet — conversation memory lives in n8n, keyed on the verified user id.

**Nothing in the deployed system touches any VM.** It is n8n Cloud, Supabase
and Cloudflare. That rules out the local SearXNG the Python preview relies on,
which is why the agent has no web search — see below.

## Why a Worker sits in the middle

The browser signs in to Supabase directly and receives an access token. It never
receives anything else — not the n8n webhook URL, not the webhook secret, not a
service-role key.

Identity is the reason the Worker exists:

```
Supabase login
      ↓  access JWT
Authorization: Bearer <JWT>
      ↓
Cloudflare Worker
      ↓  verifies the signature against the project's JWKS
      ↓  reads sub / email from the verified payload
X-Stead-User-Id: <verified id>
      ↓
n8n
```

The Worker reads exactly one field out of the request body: `message`. Any
`user`, `user_id` or `household_id` the browser sends is discarded, not
sanitised — identity comes from the verified token or it does not exist. A test
sends attacker-controlled identity alongside a valid token and asserts that what
reaches n8n is the token's subject.

This mirrors the rule the Telegram preview enforces in SQL: `SteadStore` binds
`household_id` at construction so no tool can redirect it. Same principle, a
different layer.

## How the token is verified

Against the project's published JWKS, cryptographically, in the Worker:

```ts
jwtVerify(token, remoteJwks, {
  issuer:    `${SUPABASE_URL}/auth/v1`,
  audience:  'authenticated',
  algorithms: ['ES256', 'RS256'],
  requiredClaims: ['sub', 'exp'],
})
```

Three consequences worth stating:

- **No service-role key.** The Worker holds no Supabase credential at all. The
  JWKS endpoint is public and needs no `apikey` header.
- **No round-trip.** `getUser(jwt)` would mean an HTTPS call to Supabase on
  every message, making Stead unavailable whenever Supabase auth is slow. Keys
  are fetched once per isolate and cached by `jose`.
- **`algorithms` is pinned.** Without it, a token presented as HS256 could be
  offered for verification against a published key — the classic algorithm
  confusion forgery. A test publishes an Ed25519 key under a matching `kid` and
  asserts the token is still refused, so the pin is covered by something that
  fails when the pin is removed.

This requires the Supabase project to use **asymmetric JWT signing keys**. This
one does: its JWKS publishes a single ES256 key. A project still on the legacy
HS256 shared secret publishes no usable key and every request would 401 — the
fix there is to migrate the project to signing keys in the dashboard, not to
weaken this.

`SUPABASE_PUBLISHABLE_KEY` is deliberately **not** a Worker variable. The Worker
never calls Supabase's REST API, so it would be an unused credential-shaped
value sitting in configuration inviting someone to trust it later. It belongs in
the browser, where it is `VITE_SUPABASE_PUBLISHABLE_KEY`.

## Layout

```
web/                     React + TypeScript + Vite → Cloudflare Pages
  src/lib/env.ts         validated configuration, one error listing everything missing
  src/lib/supabase.ts    the browser client; publishable key only
  src/lib/api.ts         the only thing the app talks to is the Worker
  src/auth/              session restore, sign in, sign out, token retrieval
  src/components/        SignIn, Home
  public/                manifest, icons
  vite.config.ts         also generates dist/_headers

worker/                  Cloudflare Worker → stead-gateway
  src/index.ts           routes, validation, logging, n8n forwarding
  src/auth.ts            JWKS verification → trusted user
  src/cors.ts            origin allowlist
  wrangler.jsonc         non-secret config; secrets are never here
  .dev.vars.example      local values, placeholders only
```

## Local development

Two terminals. Node 22 or newer; this was built on Node 26.7.

**Worker** — start it first, so the app has something to call:

```bash
cd worker
npm install
cp .dev.vars.example .dev.vars     # then edit; see below
npx wrangler dev                   # http://localhost:8788
```

`wrangler dev` binds **8788**, pinned in `wrangler.jsonc`. It is not wrangler's
8787 default because the `headroom` Sarvam proxy already holds that port on the
preview VM.

Until `N8N_WEBHOOK_URL` is set, `POST /api/stead` authenticates the user and
then returns `500 {"error":"server_misconfigured"}`. That is the intended
behaviour, not a bug: everything up to the n8n hop is exercisable without n8n
existing.

**Web app:**

```bash
cd web
npm install
cp .env.example .env.local         # then edit; see below
npm run dev                        # http://localhost:5173
```

Checks:

```bash
cd worker && npx vitest run && npx tsc --noEmit
cd web    && npx vitest run && npm run build
```

`npm run build` runs `tsc --noEmit` before `vite build`, so a type error fails
the build rather than shipping.

To serve the production build with the real security headers applied — Vite's
own dev and preview servers ignore `_headers`, only Pages honours it:

```bash
cd web && npm run build
cd ../worker && npx wrangler pages dev ../web/dist --port 8790
```

## Configuration

### Browser (`web/.env.local`, and Pages build variables)

Everything here is compiled into the bundle and is **public**.

| Variable | Example | Notes |
|---|---|---|
| `VITE_SUPABASE_URL` | `https://lzzrpzdjzybpvykspxiy.supabase.co` | |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_…` | Publishable or legacy anon key. **Never** the service-role key. |
| `VITE_STEAD_API_URL` | `http://localhost:8788` | Worker origin. Production: the Worker's route. |

A missing variable renders an on-screen explanation naming it, rather than a
blank page.

### Worker variables (`worker/wrangler.jsonc`, not secret)

| Variable | Notes |
|---|---|
| `SUPABASE_URL` | Which project's JWKS and issuer to trust. |
| `ALLOWED_ORIGIN` | Comma-separated browser origins. Holds the **production** value; `.dev.vars` overrides it locally. |
| `N8N_TIMEOUT_MS` | Defaults to 60000 if unset or unparseable. |

There is one environment on purpose. A second `env` block in `wrangler.jsonc`
would mean two deployed Workers and a `--env` flag that is easy to forget.

### Worker secrets (never in git, never in `wrangler.jsonc`)

| Secret | Why it is secret |
|---|---|
| `N8N_WEBHOOK_URL` | Anyone holding it can invoke the Stead agent directly, bypassing the identity check entirely. |
| `N8N_WEBHOOK_SECRET` | The proof to n8n that a request came through this gateway. |

```bash
cd worker
npx wrangler secret put N8N_WEBHOOK_URL
npx wrangler secret put N8N_WEBHOOK_SECRET
npx wrangler secret list
```

Wrangler has no configuration field for declaring the *names* of required
secrets, so they are documented here and in `.dev.vars.example` instead. Writing
the values into `wrangler.jsonc` would commit them.

## The API

### `GET /health`

Unauthenticated, no data, `Access-Control-Allow-Origin: *`. The only wildcard in
the Worker.

```json
{ "status": "ok", "service": "stead-gateway" }
```

### `POST /api/stead`

```http
POST /api/stead
Authorization: Bearer <supabase access token>
Content-Type: application/json

{ "message": "What's happening tomorrow?" }
```

Rejections, in the order they are applied:

| Condition | Status | Body |
|---|---|---|
| `Origin` present and not allowlisted | 403 | `{"error":"unauthorized"}` |
| Missing, malformed, expired, forged or non-user token | 401 | `{"error":"unauthorized"}` |
| Content-Type not JSON | 415 | `{"error":"invalid_request"}` |
| Body not JSON, or `message` absent / not a string / empty | 400 | `{"error":"invalid_request"}` |
| Body over 16 KB, or `message` over 4000 characters | 413 | `{"error":"payload_too_large"}` |
| `N8N_WEBHOOK_URL` unset | 500 | `{"error":"server_misconfigured"}` |
| n8n unreachable, timed out, errored, or unreadable | 502 | `{"error":"agent_unavailable"}` |

Authentication is checked **before** validation and before the configuration
check, so an anonymous caller learns nothing about how this deployment is set
up.

Success is `200 {"reply": "…"}`. Nothing n8n returns is passed through verbatim.

### `POST /api/stead/voice`

Push to talk. The body **is** the recording — not base64 in JSON, because
encoding would add a third to every upload and a phone on mobile data is the
case that matters. The Worker base64-encodes it once, server-side, for n8n.

```http
POST /api/stead/voice
Authorization: Bearer <supabase access token>
Content-Type: audio/webm;codecs=opus

<raw audio bytes>
```

Accepted containers: `audio/webm`, `audio/ogg`, `audio/mp4`, `audio/mpeg`,
`audio/wav`, `audio/x-m4a`, `audio/m4a`, `audio/flac`. Codec parameters are
stripped. Anything else is `415`.

This list is not decoration. Chrome and Firefox record WebM/Opus; Safari —
which is every browser on iOS, including the home-screen PWA — records MP4.
Both must work or half the households cannot speak to Stead.

Limits: 4 MB, roughly eight minutes of Opus. Empty bodies are `400`.

Success is `200`:

```json
{
  "reply": "Nothing in the diary tomorrow.",
  "transcript": "Anything on tomorrow?"
}
```

**The reply is spoken by the browser, not the server.** `speechSynthesis`
starts immediately, costs nothing, and honours `lang: en-GB`. Server-side
synthesis was removed: it added ~3 s per turn and a base64 payload a third
larger than the audio, and nothing could play until the whole file arrived.

The gateway still *accepts* `audio_base64` / `audio_mime` in case the agent
sends audio one day, and the frontend prefers it when present. The media type
is re-checked against the same allowlist on the way out: it becomes a `Blob`
type in the browser, and upstream is not the authority on what this gateway
hands a household's device.

## The n8n contract

The workflow is **`Stead Web`** (17 nodes). `Stead Telegram` is untouched and
shares nothing with it beyond the OpenAI credential.

```
Webhook (Header Auth) ─▶ Normalise ─▶ Spoken?
    ├─ yes: Decode Audio ─▶ Sarvam STT ─▶ Heard Anything?
    │                                       ├─ no  ─▶ "I didn't catch that"
    │                                       └─ yes ─▶ Prompt From Speech ─┐
    └─ no:  Prompt From Text ──────────────────────────────────────────────┤
                                                                           ▼
                                                    Stead Web Agent (Gemini flash-lite)
                                                    memory keyed on the Supabase user id
                                                                           │
                                                                    Spoken Turn?
                                                   ├─ yes ─▶ Voice Reply ─▶ Respond
                                                   └─ no  ─▶ Text Reply  ─▶ Respond

No text-to-speech in the workflow at all — the browser speaks. See below.
```

What the Worker sends:

```http
POST <N8N_WEBHOOK_URL>
Content-Type: application/json
X-Stead-Webhook-Secret: <N8N_WEBHOOK_SECRET>
X-Stead-User-Id: <verified Supabase user id>
X-Stead-User-Email: <verified email, omitted if the token has none>
X-Stead-Request-Id: <uuid, also in the body and in the Worker log line>

{
  "message": "What's happening tomorrow?",
  "user": { "id": "…", "email": "…" },
  "channel": "web",
  "request_id": "…"
}
```

A spoken turn replaces `message` with `audio_base64` and `audio_mime`. Exactly
one of the two is ever present.

**The webhook rejects any request whose `X-Stead-Webhook-Secret` does not
match**, using the Webhook node's own Header Auth rather than a comparison node,
so the check happens before the workflow runs and the value never appears in the
workflow JSON. The webhook URL is reachable by anyone who learns it; the header
is what makes the gateway the only way in. Trust `X-Stead-User-Id` and the
`user` object only because that check passed.

**Memory is keyed on the verified Supabase user id**, not on a chat id as the
Telegram workflow does. That key is the identity boundary reaching its
destination; pointing it at anything the browser can influence would let one
household read another.

What the Worker accepts back — `{"reply": "…"}` is the contract; the rest are
tolerated because they are what n8n tends to produce:

| Shape | Result |
|---|---|
| `{"reply": "…"}` | used |
| `{"reply": …, "transcript": …, "audio_base64": …, "audio_mime": …}` | used, for a spoken turn |
| `{"output": "…"}` | used — the AI Agent node's own field name |
| `[{"reply": "…"}]` | used — n8n item arrays are unwrapped |
| a bare JSON string | used |
| anything else, or an empty body | `502 agent_unavailable` |

### The agent's real toolset, and one shared prompt

`Stead Web` and both agents in `Stead Telegram` run the **same system prompt**,
byte for byte. Keeping them identical is deliberate: two prompts drift, and the
half that drifts is the half nobody demos.

What the agent actually has:

| | |
|---|---|
| Conversation memory | yes — 20 turns, keyed on the verified Supabase user id (web) or the chat id (Telegram) |
| The current date and time | yes — injected by expression, `Europe/London` |
| Web search | yes — Firecrawl `/v2/search`, as an agent tool |
| Gmail, Calendar, any action | **no** |

The prompt states exactly that and forbids the failure it used to invite:
describing what an inbox or diary contains, or claiming something was added,
moved, cancelled, booked or sent.

**The date is not a lookup.** An earlier version of this prompt made Stead
answer "I cannot access the web to look up the date". The model has no clock;
n8n does. The system message is an *expression* carrying `$now`, so Stead knows
what day it is and can do arithmetic on it — while still refusing the weather,
which genuinely is a lookup.

**Search reaches a vendor, and that was a decision.** The deployed system
touches nothing on any VM, so the `SECURITY.md` design — queries confined to a
SearXNG on loopback — has nowhere to run. n8n's `toolSearXng` node would work
but only against a publicly reachable instance, and public SearXNG instances
disable the JSON API it needs. So search is Firecrawl, and household queries
now reach an account in the key holder's name. `SECURITY.md` carries the full
trade-off; read it before widening what Stead is allowed to look up.

Two n8n traps cost a broken deployment here. `toolHttpRequest` lists
`httpBearerAuth` in its type definition and **rejects it at runtime** — the
credential has to be Header Auth carrying `Authorization: Bearer …`. And a
misconfigured tool fails at config time, which fails the *entire run*: typed
questions that would never have searched returned empty until it was reverted.
Attach a tool, prove it harmless, and only then let the prompt advertise it.

### Where speech happens

| | Telegram preview (Python) | Web MVP | `Stead Telegram` (n8n) |
|---|---|---|---|
| Hears | Sarvam Saaras | Sarvam `saarika:v2.5` | Sarvam `saarika:v2.5` |
| Speaks | Edge `en-GB-RyanNeural` | the browser, `lang: en-GB` | Sarvam `bulbul:v3`, `ratan` |

`ARCHITECTURE.md` explains the British-voice requirement at length. The web MVP
honours it the cheapest possible way: `speechSynthesis` obeys `lang: en-GB`, so
the device picks a British voice, no audio crosses the network, and nothing is
billed. Voices are ranked — British over other English, `Natural`/`Neural`
names above the rest, male over female, since the documented choice was
`en-GB-Ryan`.

`Stead Telegram` cannot do that: there is no browser, so it must synthesise
server-side. Sarvam's TTS accepts only Indian locales — `en-IN` is its sole
English — so `ratan` is male but not British. Accepted, because that workflow is
a test harness rather than a user surface.

**Transcription is Sarvam on both.** It is faster than Whisper and more honest:
given a sine tone containing no speech, Whisper returned the word "You" in
1,851 ms and Sarvam returned nothing in 1,182 ms. An empty transcript is then
caught by `Heard Anything?` rather than handed to a model that will fill the
gap.

No OpenAI anywhere. It was tried and abandoned twice in one afternoon — see the
runbook's field notes on the free-credit pool and on keys that are not balances.

## Deployment

### Pages, connected to Git

Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git →
`yablokolabs/stead-preview`:

| Setting | Value |
|---|---|
| Production branch | `main` |
| Root directory | `web` |
| Build command | `npm run build` |
| Build output directory | `dist` |

Then add the three `VITE_*` variables under Settings → Environment variables,
for both Production and Preview. They are read at **build** time, so changing
one requires a redeploy, not just a reload.

Set `VITE_STEAD_API_URL` to the deployed Worker's origin.

### Worker

```bash
cd worker
npm install
npx wrangler deploy
```

Before the first deploy, set `ALLOWED_ORIGIN` in `wrangler.jsonc` to the exact
origin Pages serves from — the `*.pages.dev` origin at first, the custom domain
later. Wrong value and every browser request is refused. A wildcard would let
any site drive a signed-in user's Stead.

The two are circular on first deploy: the Worker needs the Pages origin and
Pages needs the Worker origin. Deploy the Worker first with a placeholder, note
the `*.workers.dev` URL, configure Pages, then update `ALLOWED_ORIGIN` and
redeploy the Worker.

### Custom domain — not configured

Not done by this task, and no DNS record has been touched. The intended shape:

```
app.<stead-domain>   → Pages project (Custom domains)
api.<stead-domain>   → Worker (Settings → Domains & Routes)
```

Both are Cloudflare-proxied records created from their respective dashboards.
After adding them, update `ALLOWED_ORIGIN` and `VITE_STEAD_API_URL` and redeploy
both sides.

## Security notes

**CSP.** `web/dist/_headers` is generated at build time by a plugin in
`vite.config.ts`, because `connect-src` has to name the exact Supabase and
gateway origins and those differ per environment. A committed static file would
be silently wrong in two environments out of three, and a CSP that is wrong in
the permissive direction is worse than none because it still looks like
protection.

The policy permits `'self'` plus, in `connect-src`, the Supabase project origin
(`/auth/v1/*` for sign-in and token refresh) and the Worker origin. No
`unsafe-inline` anywhere: the production build emits a linked stylesheet and a
module script and no inline `<style>` or `<script>`, verified against the built
output.

Also served: `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options:
DENY`, `frame-ancestors 'none'`, `Cross-Origin-Opener-Policy`, and
`Permissions-Policy` granting `microphone=(self)` and denying everything else —
camera, geolocation, payment, USB and the motion sensors — including to any
embedded frame.

**The microphone.** It is opened on the tap that starts recording and the
media tracks are stopped the moment the recording ends or the screen unmounts,
so the browser's recording indicator is not left on in someone's kitchen. A
test fails if that release is removed.

**CORS.** Origin allowlist, no wildcard on the authenticated route, and
deliberately no `Access-Control-Allow-Credentials`: the session travels in an
Authorization header, never a cookie, so there is no ambient credential for CSRF
to ride on. A request with no `Origin` at all is served — CORS is browser-
enforced, a browser always sends `Origin` cross-origin, and refusing curl would
break debugging without closing anything.

**Logging.** One JSON line per request: request id, method, path, status,
upstream status, verified user id, duration, and the byte length of any
recording. Never the token, the message, the transcript, the recording itself,
the email, the webhook secret or the n8n URL. A test asserts each of those
absences.

**Errors.** A fixed set of codes. Upstream text, stack traces and internal URLs
never reach a response — a test has n8n return a body containing the secret, the
webhook URL and a stack frame, and asserts the client receives exactly
`{"error":"agent_unavailable"}`. The frontend independently maps codes to fixed
copy and renders nothing a server sent.

## Testing

```bash
cd worker && npx vitest run     # 69
cd web    && npx vitest run     # 43
```

The Worker tests generate a real ES256 keypair and sign real tokens; only the
two network edges (Supabase's JWKS endpoint and n8n) are stubbed. Nothing about
the authentication path is mocked, so weakening the verification fails them —
confirmed by mutation: removing the algorithm pin, the role check, or the
signature verification each breaks a different test.

`scripts/verify.sh` gained five git-hygiene checks covering `node_modules`,
`.dev.vars`, `web/.env*`, and greps asserting that no n8n hostname or secret
name appears in browser code and no webhook URL appears in Worker sources.

## Still open

- **No service worker**, so Chrome will not offer its install prompt yet. iOS
  "Add to Home Screen" works from the manifest alone. This is also the gate for
  web push later, which iOS grants only to home-screen-installed apps.
- **No realtime voice.** Push to talk is a turn: record, send, wait, hear. There
  is no barge-in and no open mic, and n8n Cloud cannot provide either — it is
  request/response. Realtime would replace the n8n hop for voice rather than
  extend it.
- **No calendar or email tools** on the agent. See below.
- No DNS changes.
- No connection between this Worker and Hermes. n8n is the new orchestration
  path; the Telegram preview keeps its own.
