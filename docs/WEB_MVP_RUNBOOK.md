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
| n8n webhook | `https://yablokolabs.app.n8n.cloud/webhook/stead-web` | |
| Pages project | **not created yet** | |
| Custom domain | **not configured** | |

The Telegram/Hermes preview shares none of this. See the top of `README.md`.

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

**Create the OpenAI credential** if it does not exist. The current workflows use
`n8n free OpenAI API credits`, which is a finite trial pool — swap it for a real
OpenAI credential before anything depends on it.

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
`publish_workflow`. Attach the Header Auth credential to the Webhook node and
the OpenAI credential to `Transcribe Voice Note`, `Stead Web Model` and
`Generate Speech`.

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

**Create it through the dashboard, connected to Git.** A project created by
`wrangler pages deploy` is a *direct upload* project and can never afterwards be
connected to a repository — converting means deleting and recreating it.

Workers & Pages → Create → Pages → Connect to Git → `yablokolabs/stead-preview`:

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
- **The web voice is not British.** `alloy` on OpenAI `tts-1`, against an
  `ARCHITECTURE.md` that argues at length for `en-GB-RyanNeural`. One node
  points at Azure Speech when that matters.
- **Household audio and context reach OpenAI**, a vendor `SECURITY.md` does not
  list among its egress edges.
