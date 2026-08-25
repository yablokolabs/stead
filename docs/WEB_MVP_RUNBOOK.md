# Web MVP runbook

Everything needed to rebuild the web path from nothing, and everything that
broke while building it the first time.

`docs/CLOUDFLARE_MVP.md` explains *why* the architecture is shaped this way.
This file is operational: what exists, how to recreate it, and which failures
look like something they are not.

Nothing here is a secret. Secrets live in exactly three places, listed under
**Secrets** below, and none of them is this repository.

---

## Inventory

Everything the web path depends on, as of 2026-08-17.

| Thing | Identifier | Where it lives |
|---|---|---|
| Supabase project | `lzzrpzdjzybpvykspxiy` (`Stead`, eu-west-2) | supabase.com |
| Supabase URL | `https://lzzrpzdjzybpvykspxiy.supabase.co` | |
| Publishable key | `sb_publishable_6-xt4uk7yEbzZJV6Gq_IsQ_g88NqBmE` | public by design; ships in the browser bundle |
| Cloudflare account | `c3c8915ba93e80b5f028d3a4c89dba3e` | dash.cloudflare.com |
| Worker | `stead-gateway` | `https://stead-gateway.young-disk-9d1c.workers.dev` |
| n8n instance | `yablokolabs.app.n8n.cloud` | n8n Cloud |
| n8n workflow | `Stead Web`, id `7timLWnuspV2O1mb` | source of truth: `docs/n8n/stead-web.workflow.ts` |
| Agent model | Google Gemini `models/gemini-3.1-flash-lite` | credential `Google Gemini(PaLM) Api account` |
| Transcription | Sarvam `saarika:v2.5` | credential `Sarvam` (Header Auth, `api-subscription-key`) |
| n8n webhook | `https://yablokolabs.app.n8n.cloud/webhook/stead-web` | |
| Pages project | `stead-preview` | `https://stead-preview.pages.dev` — **direct upload, not Git-connected** |
| Custom domain | **not configured** | |

### The `Stead Telegram` workflow

Separate from the Python/Hermes preview, and separate from the web path, but it
shares this instance's credentials — which is how it got taken down twice in one
session. It is a **test harness, not a user surface**, so it is allowed to sound
different from the web app.

| Node | Provider |
|---|---|
| `Transcribe Audio` | Sarvam `saarika:v2.5`, HTTP Request, `Sarvam` credential |
| `Voice LLM`, `Text LLM` | Gemini `models/gemini-3.1-flash-lite` |
| `Generate Speech` | Sarvam `bulbul:v3`, speaker `ratan`, `en-IN` |
| `Decode Reply Audio` | `convertToFile` on `audios[0]` — Sarvam returns base64, Telegram needs binary |

`Stead (Voice)` reads `{{ $json.transcript }}`, Sarvam's field, not Whisper's
`text`.

**Its voice is not British.** Sarvam's TTS accepts only Indian locales — `en-IN`
is the sole English option — so `ratan` is male but Indian-accented.
`ARCHITECTURE.md` records the British voice in the Hermes preview as Edge's
`en-GB-RyanNeural`; Sarvam's `ratan` was registered there but never selected.
The web app avoids this entirely by speaking in the browser, where `en-GB` is
honoured.

The Telegram/Hermes *Python* preview shares none of this. See the top of
`README.md`.

---

## Rebuild from zero

In this order. Each step depends on the one before it.

### 1. Supabase

The project already exists; these are the settings that matter, and what to set
if you are recreating it.

**Auth providers.** Only `email` is enabled — every OAuth provider, phone,
passkeys and anonymous sign-in are off. The app implements email + password
sign-in. Nothing in the code assumes more.

**JWT signing keys must be asymmetric.** The Worker verifies tokens against the
project's published JWKS:

```bash
curl -s https://lzzrpzdjzybpvykspxiy.supabase.co/auth/v1/.well-known/jwks.json
```

This must return a key with `"alg":"ES256"` (or RS256). A project still on the
legacy HS256 shared secret publishes no usable key and **every request will
401**. Fix it by migrating the project to JWT signing keys in the dashboard —
never by weakening the Worker.

**Confirm what is enabled** at any time, without credentials:

```bash
curl -s https://lzzrpzdjzybpvykspxiy.supabase.co/auth/v1/settings \
  -H "apikey: sb_publishable_6-xt4uk7yEbzZJV6Gq_IsQ_g88NqBmE"
```

**Create a household user.** Dashboard → Authentication → Users → Add user →
**tick "Auto Confirm User"**. That tick matters: the project has
`mailer_autoconfirm: false`, so a user created through the public signup API
sits unconfirmed waiting for an email that Supabase's built-in SMTP only
delivers to project team members. The dashboard skips it.

**Turn off open signup.** `disable_signup` is currently `false`, which means
anyone holding the publishable key — which ships in the browser bundle by
design — can register against an invite-only beta. Dashboard → Authentication →
Sign In / Providers → disable new signups.

**Keys.** The browser gets the *publishable* key (`sb_publishable_…`) and
nothing else. The service-role key is never used by any part of this system:
not the frontend, not the Worker, not n8n. If you find yourself reaching for
it, something is wrong with the design rather than with your permissions.

### 2. n8n

**Create two credentials.** Neither workflow uses OpenAI any more — see the
field notes for why it was abandoned twice in one afternoon.

- **Google Gemini** (`googlePalmApi`) — the agent on both workflows.
- **Sarvam** — a **Header Auth** credential, name `api-subscription-key`, value
  a Sarvam API key. Drives transcription on both workflows and synthesis on
  Telegram.

Use a Sarvam key **issued for Stead**, not Polaris's from `~/.hermes/.env`.
Sarvam rate-limits per key, so a shared one means Stead's traffic can silence
Polaris, and revoking either revokes both.

**Create the Header Auth credential.** This is the shared secret between the
Worker and n8n, and it is the single most error-prone step in the whole build.

1. Generate a value. Keep it short enough to type by hand:
   ```bash
   echo "stead-$(openssl rand -hex 8)"
   ```
2. n8n → Credentials → New → **Header Auth**
3. **Name** field: `X-Stead-Webhook-Secret` — this is the HTTP header name, not
   a label. Type it. Do not paste.
4. **Value** field: the generated string. Type it. Do not paste.
5. Save.

Read **Field notes → The webhook 403** before you skip that "type it" advice.
It cost four round trips.

**Create the workflow** from `docs/n8n/stead-web.workflow.ts` using the n8n MCP
tools — `validate_workflow`, then `create_workflow_from_code`, then
`publish_workflow`. Then attach credentials by hand, because
`create_workflow_from_code` skips HTTP Request nodes entirely:

- webhook secret → `Stead Web Webhook`
- `Sarvam` → `Transcribe Voice Note`
- Gemini → `Stead Web Model`

**Activate it**, then copy the *production* webhook URL (`/webhook/…`, not
`/webhook-test/…`).

**Verify before moving on.** All three must hold:

```bash
S='<the secret>'
U=https://yablokolabs.app.n8n.cloud/webhook/stead-web

# refused with no header
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$U" \
  -H 'Content-Type: application/json' -d '{"message":"probe"}'          # 403

# refused with a wrong value
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$U" \
  -H 'Content-Type: application/json' \
  -H 'X-Stead-Webhook-Secret: nope' -d '{"message":"probe"}'            # 403

# accepted
curl -s -X POST "$U" -H 'Content-Type: application/json' \
  -H "X-Stead-Webhook-Secret: $S" \
  -d '{"message":"Reply with exactly one word: pong","user":{"id":"probe"},"channel":"web","request_id":"rb-1"}'
# {"reply":"pong"}
```

A GET to that URL returning `{"code":404,"message":"This webhook is not
registered for GET requests"}` is **correct** — the route is POST-only.

### 3. Cloudflare Worker

```bash
cd worker
npm install
npx wrangler login --device --browser false \
  --scopes account:read user:read workers:write workers_scripts:write \
           workers_routes:write workers_tail:read pages:write zone:read
```

`--device` matters on a headless VM: the default flow needs a callback on
`localhost:8976` that your browser cannot reach over SSH. See **Field notes →
wrangler login**.

Set `ALLOWED_ORIGIN` in `wrangler.jsonc` to the origins Pages serves from, then:

```bash
printf '%s' '<the n8n webhook URL>' | npx wrangler secret put N8N_WEBHOOK_URL
printf '%s' '<the header auth value>' | npx wrangler secret put N8N_WEBHOOK_SECRET
npx wrangler deploy
```

**Use `printf '%s'`, never `echo`.** `echo` appends a newline, the newline
becomes part of the secret, and n8n rejects every request with an error
indistinguishable from a wrong header name.

Verify:

```bash
U=https://stead-gateway.young-disk-9d1c.workers.dev
curl -s "$U/health"                                                    # {"status":"ok",...}
curl -s -X POST "$U/api/stead" -H 'Content-Type: application/json' \
  -H 'Origin: <an allowed origin>' -d '{"message":"hi"}'               # {"error":"unauthorized"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$U/api/stead" \
  -H 'Content-Type: application/json' -H 'Origin: https://evil.example.com' \
  -d '{"message":"hi"}'                                                # 403
```

### 4. Cloudflare Pages

**The live project is direct upload, not Git-connected.** That was a deliberate
trade to get a testable URL onto a phone quickly, and it is one-way: a project
created by `wrangler pages deploy` can never afterwards be connected to a
repository. Converting means deleting `stead-preview` and recreating it through
the dashboard.

To deploy the current build:

```bash
cd web && npm run build          # bakes VITE_* from .env.local
cd ../worker
npx wrangler pages deploy ../web/dist --project-name stead-preview \
  --branch main --commit-dirty=true
```

The `VITE_*` values are compiled into the bundle at build time, so **the
deploying machine's `web/.env.local` decides what the live site talks to**.
There are no Pages environment variables on a direct-upload project — check
`web/.env.local` before every deploy, or verify afterwards:

```bash
curl -s https://stead-preview.pages.dev/assets/index-*.js \
  | grep -oE 'https://stead-gateway[a-z0-9.-]*workers\.dev'
```

A fresh `pages.dev` hostname returns **522 for the first minute or so** while
the edge provisions, even though asset paths already answer 200. That is not a
failed deploy.

Every deploy prints a warning about `pages_build_output_dir` missing from
`wrangler.jsonc`. Ignore it: that field is for Pages projects that keep their
own config file, and this repo's `wrangler.jsonc` belongs to the **Worker**.
Adding it would point Pages at the Worker's configuration.

**If you want Git integration instead**, delete the project and recreate it
through the dashboard: Workers & Pages → Create → Pages → Connect to Git →
`yablokolabs/stead-preview`:

| Setting | Value |
|---|---|
| Production branch | `main` |
| Root directory | `web` |
| Build command | `npm run build` |
| Build output directory | `dist` |

Then Settings → Environment variables, for **both** Production and Preview:

```
VITE_SUPABASE_URL              https://lzzrpzdjzybpvykspxiy.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY  sb_publishable_6-xt4uk7yEbzZJV6Gq_IsQ_g88NqBmE
VITE_STEAD_API_URL             https://stead-gateway.young-disk-9d1c.workers.dev
```

These are read at **build** time, so changing one needs a redeploy, not a
reload. They also determine the generated CSP — see `vite.config.ts`.

Finally, put the resulting `*.pages.dev` origin into the Worker's
`ALLOWED_ORIGIN` and redeploy the Worker. The two are circular on first setup:
deploy the Worker first with a placeholder, configure Pages, then come back.

### 5. Local development

```bash
cd worker && npm install && cp .dev.vars.example .dev.vars   # then fill it in
npx wrangler dev                                             # :8788

cd web && npm install && cp .env.example .env.local          # then fill it in
npm run dev                                                  # :5173
```

Reach it at **`http://localhost:5173`**, forwarding that port if you are on a
remote VM. Not an IP address: browsers only grant microphone access in a secure
context, `http://localhost` qualifies and `http://10.0.0.4:5173` does not, so
voice fails with a permissions error that looks like an application bug.

To point a local frontend at the *deployed* Worker instead, add
`http://localhost:5173` to `ALLOWED_ORIGIN` and redeploy. Take it out again
before real households use the system.

---

## Secrets

| Secret | Lives in | Set with |
|---|---|---|
| `N8N_WEBHOOK_URL` | Cloudflare Worker secrets | `wrangler secret put` |
| `N8N_WEBHOOK_SECRET` | Cloudflare Worker secrets **and** the n8n Header Auth credential — the two must match exactly | `wrangler secret put` / n8n UI |
| OpenAI API key | n8n credential | n8n UI |

Not secret, and deliberately public: the Supabase URL, the Supabase publishable
key, the Worker URL. The Supabase service-role key is not used anywhere.

`scripts/verify.sh` asserts that no webhook URL reaches Worker sources and that
no n8n hostname or secret name reaches browser code.

**Rotating the webhook secret** — both sides, or nothing works:

```bash
NEW="stead-$(openssl rand -hex 8)"
echo "$NEW"                                    # type this into the n8n credential
cd worker && printf '%s' "$NEW" | npx wrangler secret put N8N_WEBHOOK_SECRET
```

---

## Field notes

Every one of these cost real time. They are recorded because none of them is
guessable from the code, and most produce a symptom that points somewhere else.

### The webhook 403

**Symptom.** The browser shows "Stead is not reachable at the moment", the
Worker returns `502 {"error":"agent_unavailable"}`, and **n8n shows no execution
at all.**

**Why that combination is the diagnosis.** A 502 means the Worker got past
authentication, validation and configuration and actually called n8n. No
execution record means n8n rejected the request at the Header Auth layer, which
runs before the workflow starts. So the secret or the header name is wrong.

**What actually went wrong, twice.** First the credential's header *name* field
contained a leading space — `" X-Stead-Webhook-Secret"` — copied out of a chat
message. HTTP header names cannot contain spaces, so no request could ever
match. Then the *value* carried a stray character from a second paste.

n8n gives the same error, `Authorization data is wrong!`, whether the name is
wrong, the value is wrong, or the header is absent entirely. There is no way to
tell them apart from the response.

**How to tell them apart anyway.** n8n echoes the configured header name back
through the API, with quotes, which makes whitespace visible:

```
get_workflow_details(detailLevel: 'execution')
  → 'requires a header with name " X-Stead-Webhook-Secret"'
```

**Prevention.** Type both fields by hand. Keep the secret short enough that
typing is reasonable. Use `printf '%s'`, never `echo`, when setting the Worker
side.

### Whisper rejects every real recording

**Symptom.** Text works. Voice returns 502. The n8n execution fails at
`Transcribe Voice Note` with:

```
Invalid file format. Supported formats:
['flac','m4a','mp3','mp4','mpeg','mpga','oga','ogg','wav','webm']
```

— while sending `webm`, which is in that list.

**Cause.** OpenAI infers the container from the **filename**, not the MIME type.
The decoded file was named `voice-note`, with no extension.

**Fix.** `Decode Audio` now derives the extension from `audio_mime`. Chrome
sends `audio/webm`, Safari and every iOS browser send `audio/mp4`; both must be
labelled correctly or half your households cannot speak.

**Why testing missed it.** A synthetic WAV probe passed, because Whisper coped
with that one. Only a real browser recording exposed it.

### The spoken reply arrives as the string "filesystem-v2"

**Symptom.** Voice returns 200, the text reply is right, and `audio_base64`
decodes to nine bytes.

**Cause.** The instance has `binaryMode: "separate"` — n8n stores binary data on
the filesystem, and `binary.data.data` then holds the *storage backend id*
rather than the payload.

**Fix.** The Code node reads bytes through
`await this.helpers.getBinaryDataBuffer(0, 'data')` and encodes them itself.
Never read `binary.<prop>.data` directly.

### The gateway silently discards every spoken reply

**Cause.** OpenAI's text-to-speech node labels its output `audio/mp3`, which is
not the registered media type for MP3 (`audio/mpeg` is). The Worker's allowlist
had only `audio/mpeg`, so it dropped the audio — the defensive check firing
correctly on legitimate data.

**Fix.** `audio/mp3` is in the allowlist, with a test for both spellings.

### `builtInTools` does nothing on its own

Web search requires **both** `builtInTools.webSearch` and
`responsesApiEnabled: true` on the model node. Set only the first and the
capability silently does not exist while the prompt still promises it.

The workflow-creation API also did not persist `responsesApiEnabled` from the
initial create; it had to be set again afterwards. Verify with
`validate_workflow` — it warns when `builtInTools` is present without it.

### Your OpenAI calls do not go to OpenAI

The `n8n free OpenAI API credits` credential is **managed**, and every request
made with it is routed through n8n's own shared proxy:

```
baseURL: https://ai-assistant.n8n.io/v1/ai-credits/proxy/v1
```

This one fact explains three symptoms that look unrelated:

- **Wild latency variance.** Identical requests measured 2.2 s, 9.1 s and
  14.3 s. That is a shared proxy under load, not OpenAI being slow.
- **Intermittent `500 The server had an error processing your request`**,
  surfacing to the browser as `agent_unavailable`.
- **The transcription node cannot be replaced.** Swapping it for a direct HTTP
  Request to `api.openai.com` fails with `401 Your authentication token is not
  from a valid issuer` — the managed credential issues an n8n proxy token,
  which the real API rejects. That swap is the only way to reach a faster
  transcription model or to pass Whisper a `prompt`.

**A real OpenAI API key fixes all three**, and is the single highest-value
change left. Create one, add it as a normal `openAiApi` credential, and repoint
`Transcribe Voice Note`, `Stead Web Model` and any future speech node at it.

### The free credits run out, and everything stops

Symptom: every request, voice and text, fails in under a second with an empty
body. The n8n execution says:

```
"It looks like you've used all your free n8n AI credits."
```

This takes **`Stead Telegram` down as well** — both workflows share the
credential, so the Telegram preview dies at the same moment as the web path.

There is no warning before it happens. Treat the free pool as a demo
convenience, not a dependency.

### An OpenAI key is not an OpenAI balance

Creating a key at platform.openai.com does not fund the account. A new account
sits at zero and every call returns:

```
"You exceeded your current quota, please check your plan and billing details."
```

The key is valid and the request reaches `api.openai.com` — the execution trace
confirms the baseURL is right — it simply has no credit. Fix it under
**Settings → Billing**, not by making another key.

### A deleted credential breaks the whole workflow

If a node references a credential that has since been deleted, `get_workflow_details`
fails outright with `Credential with ID "…" could not be found`, and every
request dies at the trigger with `No authentication data defined on node!` —
which reads like a node problem rather than a missing credential elsewhere.

It happened here because the OpenAI key was first saved as a Header Auth
credential, attached to the webhook, then deleted. Repair it with
`setNodeCredential` pointing at a credential that still exists; reads stay
broken until you do, but writes still work.

### toolHttpRequest is broken, and it makes the agent lie

`@n8n/n8n-nodes-langchain.toolHttpRequest` fails on this build at **every**
typeVersion:

```
The node "@n8n/n8n-nodes-langchain.toolHttpRequest" has a
"supplyData" method but no "execute" method
```

The wiring was correct — `ai_tool` from the tool to the agent, credential
attached, confirmed by reading the workflow's connections.

**The failure mode matters more than the failure.** Told its tool had errored,
Gemini answered the question anyway: "around sixteen degrees" on one attempt,
"nineteen degrees and cloudy" on the next. Both invented, both plausible, both
delivered in the same confident tone as a real answer. The first one was read
as evidence that search worked.

A tool that fails quietly **disables the prompt's own safeguard**: "never state
a fact you would have to look up" stops applying once the model believes it
looked. The prompt now says a failed check is not a result, and forbids giving a
number at all.

Two rules came out of this:

- **Attach a tool with the prompt still forbidding its capability**, confirm
  ordinary traffic is unaffected, and only then grant it. A misconfigured tool
  fails at *config* time, which fails the whole run — typed questions that would
  never have searched returned empty bodies.
- **Verify from the execution, not the reply.** A plausible answer is not
  evidence the tool ran. `get_workflow_execution` names the node, its status,
  and what it returned.

The working shape is a sub-workflow: an ordinary HTTP Request node, reached via
`toolWorkflow`. See `docs/n8n/stead-search.workflow.ts`.

### Search results are not snippets

Firecrawl's `description` field is scraped markdown — embedded links, images,
table markup — and a single weather search returned several thousand tokens of
it. Two problems at once: the agent received URLs it could read aloud, and one
search consumed a large share of the context window.

`Shape Results` strips markdown links, bare URLs and furniture, then truncates
to 400 characters per result. Verify it after any change: a version that
double-escaped its regexes matched nothing, returned empty snippets, and made
Stead answer "I could not find it" for everything — which looks exactly like a
search outage rather than a shaping bug.

### The model has no clock

Asked "what day is today", Stead answered that it could not access the web to
look up the date. Two different things were tangled in that: it genuinely has
no web search, and it genuinely has no clock — but the date is not a web
lookup, and n8n knows it.

The system message is now an **expression** (leading `=`) carrying
`{{ $now.setZone('Europe/London').toFormat('cccc d LLLL yyyy, HH:mm') }}`.
Verified: "It is Tuesday, 18 August 2026", and "There are three days until
Friday, August 21" — correct arithmetic, while still refusing the weather,
which really is a lookup.

Anything else the agent should know but cannot derive — the household's
members, a standing schedule — belongs in the same place.

### Whisper invents words; Sarvam does not

Given a 1.5-second 180 Hz sine tone with no speech in it whatsoever:

| | Result | Time |
|---|---|---|
| Whisper | `"You"` | 1,851 ms |
| Sarvam `saarika:v2.5` | `""` | 1,182 ms |

Whisper hallucinating short filler on non-speech is well known, and it is worse
than useless here: the agent receives a plausible word and answers it. Sarvam
returning nothing is the correct behaviour, and it is faster.

**But an empty transcript is its own hazard.** It reached the agent, which
replied with an empty string. Silence, a muted microphone and tap-record-say-
nothing all produce one. `Heard Anything?` now branches on it and answers "I
didn't catch that. Try again?" rather than passing nothing to a model that will
confidently fill the gap.

If you swap transcription providers, check what the empty case does before
checking the happy path.

### The whole speech path can avoid OpenAI entirely

After OpenAI failed twice, the working configuration is:

```
browser ──▶ Sarvam STT ──▶ Gemini agent ──▶ browser speechSynthesis
```

No OpenAI anywhere. Sarvam is a Header Auth credential (`api-subscription-key`)
on an HTTP Request node; Gemini uses the `googlePalmApi` credential; speech is
free and local to the device.

The Sarvam key here started as Polaris's, taken from `~/.hermes/.env`. A
separate key has since been issued, so the two systems no longer share one — but
note that **two Sarvam keys now exist in two places**: one in the n8n credential
store, one on the Hermes host. Rotating either does not rotate the other.

### Gemini transcription does not work as a drop-in

Swapping `Transcribe Voice Note` to `@n8n/n8n-nodes-langchain.googleGemini`
(`resource: audio, operation: transcribe`, `inputType: binary`) produced a
successful execution whose "transcript" was:

> "it looks like the audio or video file was not attached to your message"

The binary never reached it. It also took **5,951 ms** to say so, against
Whisper's 1,851 ms, and `simplify: true` did not simplify — the text arrives at
`content.parts[0].text`, not `text`.

The dangerous part is what happened next: `Prompt From Speech` produced `null`,
and the agent **answered confidently anyway** — "I'm ready to help you manage
the household" — with nothing to indicate it had heard nothing. If you retry
this, add a guard that fails the run on an empty transcript rather than passing
it to the agent.

Gemini is a good chat model here and a fast one. It is not a transcriber.

### The transcribe node has no `prompt` and no model choice

`@n8n/n8n-nodes-langchain.openAi` with `resource: audio, operation: transcribe`
exposes exactly two options:

```typescript
options?: { language?: string; temperature?: number };
```

A `prompt` set on it — to stop Whisper hearing "Hey Stead" as "He's dead" — is
silently discarded, and `validate_workflow` does not object. Always read a
node's type definition with `get_node_types` before assuming a parameter
exists; the API accepting a value is not evidence the node uses it.

The name is therefore still mis-transcribed ("Stent"), and stays that way until
the direct HTTP call above becomes possible.

### `wrangler login` times out on a headless VM

The default OAuth flow starts a callback listener on `localhost:8976` of the
machine running wrangler. Over SSH your browser cannot reach it, and wrangler
gives up after about two minutes with "Timed out waiting for authorization
code".

Use the device flow instead:

```bash
npx wrangler login --device --browser false --scopes <list>
```

It prints a URL and a code, has a five-minute window, and needs no callback.
`offline_access` is added by wrangler itself and is rejected if passed to
`--scopes`.

### `respondWith: 'allEntries'` is not a thing

The documented example uses it; the actual option values are
`firstIncomingItem`, `allIncomingItems`, `json`, `text`, `binary`, `noData`,
`redirect`, `jwt`. An invalid value degrades to a validation warning saying the
field "must be an n8n expression", which does not obviously mean "that value
does not exist". `validate_workflow` catches it.

### Base64 encoding breaks on real voice notes

`String.fromCharCode(...bytes)` spreads every byte into an argument list, and V8
overflows the stack between 100 kB and 200 kB of them — which a twenty-second
recording already clears. `toBase64` chunks at 32 kB.

The test that covers this originally used exactly 100,000 bytes and passed with
the chunking removed. It uses 300,000 now. Mutation testing found it; a passing
test suite did not.

### Port 8787 is taken on the preview VM

The `headroom` Sarvam proxy holds wrangler's default port. `wrangler dev` is
pinned to **8788** in `wrangler.jsonc`. Symptom if you forget: curl gets answers
from a completely different service with a plausible-looking JSON error shape.

---

## Latency

Measured on 2026-08-17, from n8n's own per-node `executionTime`. Read this
before optimising anything: the intuitive answers are wrong.

**Cloudflare Pages makes no difference to reply time.** It serves static assets.
Every message goes browser → Worker → n8n and Pages sits nowhere on that path.
It does make the *first page load* much faster, which is a different problem.

A spoken turn that triggered a web search, before tuning:

| Node | Time | Share |
|---|---:|---:|
| Stead Web Agent | 15,914 ms | 73% |
| Generate Speech (`tts-1`) | 3,050 ms | 14% |
| Transcribe (Whisper) | 1,215 ms | 6% |
| n8n overhead | ~1,600 ms | 7% |
| Decode Audio | 5 ms | — |
| **Total** | **21.8 s** | |

The agent dominates, and web search dominates the agent. Same question, only
`searchContextSize` changed:

| `searchContextSize` | Agent node | Whole text turn |
|---|---:|---:|
| `high` | 15,914 ms | ~17.5 s |
| `low` | 7,269 ms | 7.3 s |

**The search runs provider-side**, inside OpenAI's Responses API. The trace
shows `ai.agent.tool_calls.requested: 0` and `iteration.count: 0` while the
reply still quotes a live temperature — so n8n's `maxIterations` is not a lever
and never was. `searchContextSize` is the only knob.

A turn needing no search costs ~6.3 s, which is `gpt-5-mini`'s own latency.
That is the floor without changing model.

**Server-side text-to-speech has since been removed.** `tts-1` cost ~3 s per
spoken turn plus a base64 payload a third larger than the audio, and nothing
could play until the whole file arrived. The browser's own `speechSynthesis`
starts immediately and costs nothing. Measured after:

| Turn | Before any tuning | Now |
|---|---:|---:|
| Spoken | 21.8 s | **5.0 s** |
| Typed | ~17.5 s (searching) | **2.9 s** |

It also sounds *more* correct, not less: `speechSynthesis` honours
`lang: en-GB`, so most devices pick a British voice — closer to what
`ARCHITECTURE.md` argues for than `alloy` was.

`reasoningEffort` was then found to be unset, and therefore `medium`.
`gpt-5-mini` is a reasoning model, so it was thinking about "what day is
tomorrow". Setting it to `low` roughly halved the agent again:

| Turn | Original | Now |
|---|---:|---:|
| Spoken | 21.8 s | **~2.7–3.8 s** |
| Typed | ~17.5 s | **~2.2 s** |

Where the remaining time goes, measured separately:

| | Time | Share |
|---|---:|---:|
| OpenAI (via n8n's proxy) | ~2.2 s | ~85% |
| Network to/from n8n Cloud | ~0.3 s | 11% |
| The Cloudflare Worker | **~0.05 s** | 2% |
| n8n graph overhead | ~0.05 s | 2% |

The gateway contributes one fiftieth of a turn. There is nothing left to
remove from our own code.

**Sub-second is not reachable with this architecture, and that is a design
choice rather than a defect.** Whisper is a batch API: it cannot start until
the recording is complete and then takes roughly 0.4× the audio duration, so a
four-second clip costs ~1.8 s on its own. Getting under a second needs
transcription while the user speaks, generation while the model thinks, and
speech while the rest is still being written — and n8n is request/response,
with `Respond to Webhook` returning once, at the end. Streaming is where the
complexity lives, and this architecture deliberately did not buy it.

Remaining levers, largest first:

- **Show the transcript as soon as it exists** — changes nothing measurable,
  changes the experience a great deal. Needs streaming or two round trips, and
  n8n's Respond to Webhook is all-or-nothing.
- **A licensed voice.** Browser synthesis was tested and judged robotic, and no
  ranking fixes that — `speechSynthesis` plays whatever the operating system
  installed. Azure `en-GB-SoniaNeural` is the fix `ARCHITECTURE.md` already
  names, free up to 0.5M characters a month, about 3,300 replies. It would make
  the web app and the Hermes preview sound identical for the first time. Against
  it: browser synthesis is the only arrangement where a household's replies are
  never spoken by someone else's servers.
- **Turn web search off** — removes the search round trip on the turns that use
  it, at the cost of a genuinely useful capability.
- **A faster model** — moves the floor set by the agent itself.
- **Take voice out of n8n entirely** — ~1–1.5 s, and a real project rather than
  a tweak.

The OpenAI key that used to head this list is gone from the architecture
entirely. Nothing in either workflow touches OpenAI now.

## Verifying the whole path

After any change, in order. Each step isolates one hop.

```bash
# 1. Worker is up
curl -s https://stead-gateway.young-disk-9d1c.workers.dev/health

# 2. Worker refuses anonymous callers before touching n8n
curl -s -X POST https://stead-gateway.young-disk-9d1c.workers.dev/api/stead \
  -H 'Content-Type: application/json' -H 'Origin: <allowed>' -d '{"message":"hi"}'

# 3. n8n refuses anyone without the secret
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://yablokolabs.app.n8n.cloud/webhook/stead-web \
  -H 'Content-Type: application/json' -d '{"message":"probe"}'

# 4. n8n answers the Worker (text)
curl -s -X POST https://yablokolabs.app.n8n.cloud/webhook/stead-web \
  -H 'Content-Type: application/json' -H "X-Stead-Webhook-Secret: $S" \
  -d '{"message":"Reply with exactly one word: pong","user":{"id":"probe"},"channel":"web","request_id":"v"}'

# 5. and voice — build a probe file, then post it
#    (see docs/CLOUDFLARE_MVP.md for the request shape)
```

Expected: `ok`, `unauthorized`, `403`, `{"reply":"pong"}`, then a reply carrying
`audio_base64` that decodes to bytes beginning `ff f3` or `ID3`.

When something fails, read the n8n execution rather than guessing:

```
search_workflow_executions(workflowId: '7timLWnuspV2O1mb')
get_workflow_execution(..., includeData: true)
  → .data.resultData.error.description names the failing node and reason
```

No execution at all means the request never got past Header Auth.

---

## Not done yet

- **No service worker**, so Chrome will not offer an install prompt. iOS "Add to
  Home Screen" works from the manifest alone. Also the gate for web push.
- **No Pages project.**
- **No custom domain**, no DNS records touched.
- **`http://localhost:5173` is in the production `ALLOWED_ORIGIN`** for beta
  convenience. Remove it before real households.
- **The web voice is device-dependent.** The picker prefers a female voice
  (British when the device has one), but `speechSynthesis` plays whatever the
  OS installed — a device with no female English voice at all (some macOS
  builds) still sounds male. Azure `en-GB-SoniaNeural` is the fix when that
  matters.
- **Household audio and context reach OpenAI**, a vendor `SECURITY.md` does not
  list among its egress edges.
