/**
 * The gateway is exercised end to end against a real ES256 keypair: tokens are
 * genuinely signed and genuinely verified. Nothing about the authentication
 * path is mocked, so these tests fail if the verification is ever weakened.
 *
 * Only the two network edges are stubbed — Supabase's JWKS endpoint and n8n.
 */
import { SignJWT, exportJWK, generateKeyPair, type JWK } from 'jose';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetKeySets } from '../src/auth';
import worker, { type Env } from '../src/index';

const SUPABASE_URL = 'https://project.supabase.co';
const JWKS_URL = `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`;
const N8N_URL = 'https://stead.app.n8n.cloud/webhook/6f1c-secret-path';
const N8N_SECRET = 'webhook-secret-value';
const ORIGIN = 'http://localhost:5173';
const USER_ID = '11111111-2222-3333-4444-555555555555';
const USER_EMAIL = 'kerstin@example.com';

let signingKey: CryptoKey;
let strangerKey: CryptoKey;
let edwardsKey: CryptoKey;
let publicJwk: JWK;
let edwardsJwk: JWK;

beforeAll(async () => {
  const mine = await generateKeyPair('ES256', { extractable: true });
  const theirs = await generateKeyPair('ES256', { extractable: true });
  const edwards = await generateKeyPair('EdDSA', { extractable: true, crv: 'Ed25519' });
  signingKey = mine.privateKey;
  strangerKey = theirs.privateKey;
  edwardsKey = edwards.privateKey;
  publicJwk = { ...(await exportJWK(mine.publicKey)), kid: 'stead-key', alg: 'ES256', use: 'sig' };
  edwardsJwk = {
    ...(await exportJWK(edwards.publicKey)),
    kid: 'stead-key',
    alg: 'EdDSA',
    use: 'sig',
  };
});

interface TokenOptions {
  key?: CryptoKey | Uint8Array;
  alg?: string;
  issuer?: string;
  audience?: string;
  expiresIn?: string | number;
  claims?: Record<string, unknown>;
}

async function mintToken({
  key = signingKey,
  alg = 'ES256',
  issuer = `${SUPABASE_URL}/auth/v1`,
  audience = 'authenticated',
  expiresIn = '1h',
  claims = {},
}: TokenOptions = {}): Promise<string> {
  return new SignJWT({ role: 'authenticated', email: USER_EMAIL, ...claims })
    .setProtectedHeader({ alg, kid: 'stead-key' })
    .setIssuer(issuer)
    .setAudience(audience)
    .setSubject(USER_ID)
    .setIssuedAt()
    .setExpirationTime(expiresIn)
    .sign(key as CryptoKey);
}

type Upstream = (request: Request) => Response | Promise<Response>;

let n8nCalls: Request[];
let upstream: Upstream;
let logLines: string[];
/** What the stubbed Supabase project publishes. Swapped by the pinning test. */
let publishedKeys: JWK[];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  resetKeySets();
  n8nCalls = [];
  logLines = [];
  publishedKeys = [publicJwk];
  upstream = () => jsonResponse({ reply: 'Nothing tomorrow.' });

  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input as never, init);
    if (request.url === JWKS_URL) return jsonResponse({ keys: publishedKeys });
    if (request.url === N8N_URL) {
      n8nCalls.push(request.clone());
      return upstream(request);
    }
    throw new Error(`unexpected fetch to ${request.url}`);
  });

  const capture = (...args: unknown[]) => void logLines.push(args.map(String).join(' '));
  vi.spyOn(console, 'log').mockImplementation(capture);
  vi.spyOn(console, 'error').mockImplementation(capture);
});

function env(overrides: Partial<Env> = {}): Env {
  return {
    SUPABASE_URL,
    ALLOWED_ORIGIN: ORIGIN,
    N8N_WEBHOOK_URL: N8N_URL,
    N8N_WEBHOOK_SECRET: N8N_SECRET,
    ...overrides,
  };
}

interface PostOptions {
  token?: string;
  authorization?: string;
  origin?: string | null;
  contentType?: string | null;
  method?: string;
}

function post(body: unknown, options: PostOptions = {}): Request {
  const { token, authorization, origin = ORIGIN, contentType = 'application/json' } = options;
  const headers = new Headers();
  if (contentType) headers.set('Content-Type', contentType);
  if (origin) headers.set('Origin', origin);
  if (authorization) headers.set('Authorization', authorization);
  else if (token) headers.set('Authorization', `Bearer ${token}`);

  return new Request('https://gateway.example.com/api/stead', {
    method: options.method ?? 'POST',
    headers,
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

const call = (request: Request, e: Env = env()) => worker.fetch(request, e);

describe('GET /health', () => {
  it('answers without authentication', async () => {
    const response = await call(new Request('https://gateway.example.com/health'));
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ status: 'ok', service: 'stead-gateway' });
  });

  it('refuses a POST', async () => {
    const response = await call(
      new Request('https://gateway.example.com/health', { method: 'POST' }),
    );
    expect(response.status).toBe(405);
  });
});

describe('routing', () => {
  it('404s an unknown path', async () => {
    const response = await call(new Request('https://gateway.example.com/api/anything'));
    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ error: 'not_found' });
  });

  it('405s a GET on /api/stead', async () => {
    const request = new Request('https://gateway.example.com/api/stead', {
      headers: { Origin: ORIGIN },
    });
    expect((await call(request)).status).toBe(405);
  });
});

describe('authentication', () => {
  it('rejects a missing Authorization header', async () => {
    const response = await call(post({ message: 'hello' }));
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: 'unauthorized' });
    expect(n8nCalls).toHaveLength(0);
  });

  it.each([
    ['not a bearer scheme', 'Basic dXNlcjpwYXNz'],
    ['bearer with no token', 'Bearer'],
    ['bearer with junk', 'Bearer not-a-jwt'],
    ['bearer with two segments', 'Bearer aaa.bbb'],
    ['bearer with an empty token', 'Bearer '],
  ])('rejects a malformed bearer token: %s', async (_label, authorization) => {
    const response = await call(post({ message: 'hello' }, { authorization }));
    expect(response.status).toBe(401);
    expect(n8nCalls).toHaveLength(0);
  });

  it('rejects a token signed by a key the project does not publish', async () => {
    const token = await mintToken({ key: strangerKey });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(401);
    expect(n8nCalls).toHaveLength(0);
  });

  it('rejects a token signed with a symmetric secret', async () => {
    const token = await mintToken({
      key: new TextEncoder().encode('a-symmetric-secret-long-enough-for-hs256'),
      alg: 'HS256',
    });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(401);
    expect(n8nCalls).toHaveLength(0);
  });

  /**
   * Exercises the `algorithms` allowlist itself rather than the key lookup.
   *
   * The key IS published under the matching kid, so JWKS resolution succeeds
   * and the only thing left to refuse the token is the pin. Removing the pin
   * makes this test fail; the HS256 case above does not, because a symmetric
   * key can never be resolved from an EC JWK in the first place.
   */
  it('rejects an algorithm outside the allowlist even when the key resolves', async () => {
    publishedKeys = [edwardsJwk];
    const token = await mintToken({ key: edwardsKey, alg: 'EdDSA' });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(401);
    expect(n8nCalls).toHaveLength(0);
  });

  it('rejects an expired token', async () => {
    const token = await mintToken({ expiresIn: '-1h' });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(401);
  });

  it('rejects a token from another issuer', async () => {
    const token = await mintToken({ issuer: 'https://evil.supabase.co/auth/v1' });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(401);
  });

  it('rejects an anon-role token, which proves no user', async () => {
    const token = await mintToken({ claims: { role: 'anon' } });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(401);
  });

  it('authenticates before reporting configuration problems', async () => {
    const response = await call(post({ message: 'hello' }), env({ N8N_WEBHOOK_URL: undefined }));
    expect(response.status).toBe(401);
  });
});

describe('request validation', () => {
  let token: string;
  beforeEach(async () => {
    token = await mintToken();
  });

  it('rejects a non-JSON content type', async () => {
    const response = await call(post('hello', { token, contentType: 'text/plain' }));
    expect(response.status).toBe(415);
    await expect(response.json()).resolves.toEqual({ error: 'invalid_request' });
  });

  it('rejects a body that is not valid JSON', async () => {
    const response = await call(post('{"message": ', { token }));
    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({ error: 'invalid_request' });
  });

  it.each([
    ['no message key', {}],
    ['a null message', { message: null }],
    ['a numeric message', { message: 42 }],
    ['an object message', { message: { text: 'hi' } }],
    ['an empty message', { message: '' }],
    ['a whitespace-only message', { message: '   \n\t ' }],
    ['a JSON array', [{ message: 'hi' }]],
  ])('rejects %s', async (_label, body) => {
    expect((await call(post(body, { token }))).status).toBe(400);
    expect(n8nCalls).toHaveLength(0);
  });

  it('rejects a message beyond the length limit', async () => {
    const response = await call(post({ message: 'x'.repeat(4001) }, { token }));
    expect(response.status).toBe(413);
    await expect(response.json()).resolves.toEqual({ error: 'payload_too_large' });
  });

  it('rejects a body beyond the size limit', async () => {
    const response = await call(post({ message: 'x'.repeat(20_000) }, { token }));
    expect(response.status).toBe(413);
  });
});

describe('forwarding to n8n', () => {
  let token: string;
  beforeEach(async () => {
    token = await mintToken();
  });

  it('returns the agent reply', async () => {
    const response = await call(post({ message: "What's happening tomorrow?" }, { token }));
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ reply: 'Nothing tomorrow.' });
  });

  it('sends the verified identity and the webhook secret', async () => {
    await call(post({ message: 'hello' }, { token }));

    expect(n8nCalls).toHaveLength(1);
    const forwarded = n8nCalls[0]!;
    expect(forwarded.headers.get('X-Stead-User-Id')).toBe(USER_ID);
    expect(forwarded.headers.get('X-Stead-User-Email')).toBe(USER_EMAIL);
    expect(forwarded.headers.get('X-Stead-Webhook-Secret')).toBe(N8N_SECRET);
    expect(forwarded.headers.get('X-Stead-Request-Id')).toMatch(/^[0-9a-f-]{36}$/);

    await expect(forwarded.json()).resolves.toMatchObject({
      message: 'hello',
      user: { id: USER_ID, email: USER_EMAIL },
      channel: 'web',
    });
  });

  it('never forwards the caller\'s Authorization header', async () => {
    await call(post({ message: 'hello' }, { token }));
    expect(n8nCalls[0]!.headers.get('Authorization')).toBeNull();
  });

  it('ignores identity the browser tries to supply', async () => {
    await call(
      post(
        {
          message: 'hello',
          user: { id: 'attacker', email: 'attacker@example.com' },
          user_id: 'attacker',
          household_id: 'someone-elses-household',
        },
        { token },
      ),
    );

    const forwarded = n8nCalls[0]!;
    expect(forwarded.headers.get('X-Stead-User-Id')).toBe(USER_ID);
    const body = (await forwarded.json()) as Record<string, unknown>;
    expect(body.user).toEqual({ id: USER_ID, email: USER_EMAIL });
    expect(body).not.toHaveProperty('user_id');
    expect(body).not.toHaveProperty('household_id');
  });

  it('trims the message before forwarding', async () => {
    await call(post({ message: '  hello  ' }, { token }));
    await expect(n8nCalls[0]!.json()).resolves.toMatchObject({ message: 'hello' });
  });

  it.each([
    ['an AI Agent output field', { output: 'From output.' }, 'From output.'],
    ['an array of items', [{ reply: 'From an array.' }], 'From an array.'],
    ['a bare JSON string', 'Just a string.', 'Just a string.'],
  ])('accepts %s', async (_label, body, expected) => {
    upstream = () => jsonResponse(body);
    const response = await call(post({ message: 'hello' }, { token }));
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ reply: expected });
  });

  it('reports agent_unavailable when n8n is unreachable', async () => {
    upstream = () => {
      throw new TypeError('network failure');
    };
    const response = await call(post({ message: 'hello' }, { token }));
    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({ error: 'agent_unavailable' });
  });

  it('reports agent_unavailable when n8n returns an empty body', async () => {
    upstream = () => new Response('', { status: 200 });
    expect((await call(post({ message: 'hello' }, { token }))).status).toBe(502);
  });

  it('reports server_misconfigured when the webhook is not configured', async () => {
    const response = await call(
      post({ message: 'hello' }, { token }),
      env({ N8N_WEBHOOK_URL: undefined }),
    );
    expect(response.status).toBe(500);
    await expect(response.json()).resolves.toEqual({ error: 'server_misconfigured' });
  });

  it('leaks nothing when n8n fails loudly', async () => {
    upstream = () =>
      new Response(
        `Error at ${N8N_URL}\nsecret=${N8N_SECRET}\n  at Workflow.run (/opt/n8n/dist/workflow.js:42)`,
        { status: 500 },
      );

    const response = await call(post({ message: 'hello' }, { token }));
    const text = await response.text();

    expect(response.status).toBe(502);
    expect(text).toBe('{"error":"agent_unavailable"}');
    expect(text).not.toContain(N8N_SECRET);
    expect(text).not.toContain('n8n.cloud');
    expect(text).not.toContain('workflow.js');
  });
});

describe('CORS', () => {
  it('echoes an allowed origin and varies on it', async () => {
    const token = await mintToken();
    const response = await call(post({ message: 'hello' }, { token }));
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe(ORIGIN);
    expect(response.headers.get('Vary')).toBe('Origin');
    expect(response.headers.get('Access-Control-Allow-Credentials')).toBeNull();
  });

  it('refuses a disallowed origin without an allow header', async () => {
    const token = await mintToken();
    const response = await call(
      post({ message: 'hello' }, { token, origin: 'https://evil.example.com' }),
    );
    expect(response.status).toBe(403);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBeNull();
    expect(n8nCalls).toHaveLength(0);
  });

  it('answers a preflight from an allowed origin', async () => {
    const request = new Request('https://gateway.example.com/api/stead', {
      method: 'OPTIONS',
      headers: {
        Origin: ORIGIN,
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'authorization,content-type',
      },
    });
    const response = await call(request);
    expect(response.status).toBe(204);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe(ORIGIN);
    expect(response.headers.get('Access-Control-Allow-Headers')).toContain('Authorization');
    expect(response.headers.get('Access-Control-Allow-Methods')).toContain('POST');
  });

  it('refuses a preflight from a disallowed origin', async () => {
    const request = new Request('https://gateway.example.com/api/stead', {
      method: 'OPTIONS',
      headers: { Origin: 'https://evil.example.com' },
    });
    expect((await call(request)).status).toBe(403);
  });

  it('honours a multi-origin allowlist', async () => {
    const token = await mintToken();
    const preview = 'https://preview.stead-preview.pages.dev';
    const response = await call(
      post({ message: 'hello' }, { token, origin: preview }),
      env({ ALLOWED_ORIGIN: `${ORIGIN}, ${preview}` }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe(preview);
  });

  it('serves a caller that sends no Origin at all', async () => {
    const token = await mintToken();
    const response = await call(post({ message: 'hello' }, { token, origin: null }));
    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBeNull();
  });
});

describe('logging', () => {
  it('records the request without any sensitive value', async () => {
    const token = await mintToken();
    await call(post({ message: 'the boiler service is on Tuesday' }, { token }));

    const logged = logLines.join('\n');
    expect(logged).toContain(USER_ID);
    expect(logged).not.toContain(token);
    expect(logged).not.toContain(N8N_SECRET);
    expect(logged).not.toContain('boiler');
    expect(logged).not.toContain(USER_EMAIL);
    expect(logged).not.toContain('n8n.cloud');

    const entry = JSON.parse(logLines.at(-1)!) as Record<string, unknown>;
    expect(entry).toMatchObject({
      service: 'stead-gateway',
      method: 'POST',
      path: '/api/stead',
      status: 200,
      user_id: USER_ID,
    });
    expect(entry.duration_ms).toBeTypeOf('number');
  });

  it('does not attribute a user to an unauthenticated request', async () => {
    await call(post({ message: 'hello' }));
    const entry = JSON.parse(logLines.at(-1)!) as Record<string, unknown>;
    expect(entry).toMatchObject({ status: 401, user_id: null });
  });
});

describe('push to talk', () => {
  let token: string;

  beforeEach(async () => {
    token = await mintToken();
    upstream = () =>
      jsonResponse({
        reply: 'Nothing in the diary tomorrow.',
        transcript: 'Anything on tomorrow?',
        audio_base64: 'SUQzBAAAAAA',
        audio_mime: 'audio/mpeg',
      });
  });

  /** Deliberately not the Worker's own encoder — that is what is under test. */
  function decodeBase64(value: string): Uint8Array {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function recording(bytes: Uint8Array, options: PostOptions = {}): Request {
    const headers = new Headers();
    headers.set('Content-Type', options.contentType ?? 'audio/webm;codecs=opus');
    if (options.origin !== null) headers.set('Origin', options.origin ?? ORIGIN);
    if (options.token) headers.set('Authorization', `Bearer ${options.token}`);

    return new Request('https://gateway.example.com/api/stead/voice', {
      method: options.method ?? 'POST',
      headers,
      body: bytes as unknown as BodyInit,
    });
  }

  const somAudio = new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0x00, 0xff, 0x7f, 0x80]);

  it('rejects a recording from an unauthenticated caller', async () => {
    expect((await call(recording(somAudio))).status).toBe(401);
    expect(n8nCalls).toHaveLength(0);
  });

  it('forwards the recording with the verified identity', async () => {
    const response = await call(recording(somAudio, { token }));
    expect(response.status).toBe(200);

    const forwarded = n8nCalls[0]!;
    expect(forwarded.headers.get('X-Stead-User-Id')).toBe(USER_ID);
    expect(forwarded.headers.get('X-Stead-Webhook-Secret')).toBe(N8N_SECRET);

    const body = (await forwarded.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ audio_mime: 'audio/webm', channel: 'web' });
    expect(body.user).toEqual({ id: USER_ID, email: USER_EMAIL });
    expect(body).not.toHaveProperty('message');
  });

  it('encodes the audio so it survives the round trip', async () => {
    await call(recording(somAudio, { token }));
    const body = (await n8nCalls[0]!.json()) as { audio_base64: string };
    expect(decodeBase64(body.audio_base64)).toEqual(somAudio);
  });

  /**
   * 300 kB is past the point where spreading every byte into
   * `String.fromCharCode` overflows V8's stack — measured between 100 kB and
   * 200 kB — so this fails if the chunking in `toBase64` is ever removed.
   * It is also an unremarkable length for a spoken sentence.
   */
  it('encodes a recording larger than one chunk', async () => {
    const long = new Uint8Array(300_000);
    for (let i = 0; i < long.length; i += 1) long[i] = i % 256;

    await call(recording(long, { token }));
    const body = (await n8nCalls[0]!.json()) as { audio_base64: string };
    expect(decodeBase64(body.audio_base64)).toEqual(long);
  });

  it.each([
    ['Chrome and Firefox', 'audio/webm;codecs=opus', 'audio/webm'],
    ['Safari and iOS', 'audio/mp4', 'audio/mp4'],
    ['an mp4 with a codec parameter', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4'],
    ['uppercase from a stray client', 'AUDIO/WEBM', 'audio/webm'],
  ])('accepts what %s records', async (_label, contentType, expected) => {
    const response = await call(recording(somAudio, { token, contentType }));
    expect(response.status).toBe(200);
    await expect(n8nCalls[0]!.json()).resolves.toMatchObject({ audio_mime: expected });
  });

  it.each([
    ['plain text', 'text/plain'],
    ['JSON', 'application/json'],
    ['an unsupported container', 'audio/aiff'],
    ['a disguised document', 'text/html'],
  ])('refuses %s', async (_label, contentType) => {
    expect((await call(recording(somAudio, { token, contentType }))).status).toBe(415);
    expect(n8nCalls).toHaveLength(0);
  });

  it('refuses an empty recording', async () => {
    expect((await call(recording(new Uint8Array(0), { token }))).status).toBe(400);
    expect(n8nCalls).toHaveLength(0);
  });

  it('refuses a recording past the size limit', async () => {
    const huge = new Uint8Array(4 * 1024 * 1024 + 1);
    const response = await call(recording(huge, { token }));
    expect(response.status).toBe(413);
    await expect(response.json()).resolves.toEqual({ error: 'payload_too_large' });
    expect(n8nCalls).toHaveLength(0);
  });

  it('returns the spoken reply alongside the transcript', async () => {
    const response = await call(recording(somAudio, { token }));
    await expect(response.json()).resolves.toEqual({
      reply: 'Nothing in the diary tomorrow.',
      transcript: 'Anything on tomorrow?',
      audio_base64: 'SUQzBAAAAAA',
      audio_mime: 'audio/mpeg',
    });
  });

  /**
   * The media type ends up as a Blob type in the browser. Upstream is not the
   * authority on what this gateway hands a household's device.
   */
  it('drops audio whose media type is not one it would accept', async () => {
    upstream = () =>
      jsonResponse({
        reply: 'Here you go.',
        audio_base64: 'PHNjcmlwdD4',
        audio_mime: 'text/html',
      });

    const response = await call(recording(somAudio, { token }));
    await expect(response.json()).resolves.toEqual({ reply: 'Here you go.' });
  });

  it('still answers when n8n returns text only', async () => {
    upstream = () => jsonResponse({ reply: 'Nothing tomorrow.' });
    const response = await call(recording(somAudio, { token }));
    await expect(response.json()).resolves.toEqual({ reply: 'Nothing tomorrow.' });
  });

  it('reports agent_unavailable without leaking upstream text', async () => {
    upstream = () => new Response(`whisper failed at ${N8N_URL} secret=${N8N_SECRET}`, { status: 500 });
    const response = await call(recording(somAudio, { token }));
    const text = await response.text();
    expect(response.status).toBe(502);
    expect(text).toBe('{"error":"agent_unavailable"}');
    expect(text).not.toContain(N8N_SECRET);
  });

  it('refuses a disallowed origin', async () => {
    const response = await call(
      recording(somAudio, { token, origin: 'https://evil.example.com' }),
    );
    expect(response.status).toBe(403);
    expect(n8nCalls).toHaveLength(0);
  });

  it('logs the recording size but never what was said', async () => {
    await call(recording(somAudio, { token }));

    const entry = JSON.parse(logLines.at(-1)!) as Record<string, unknown>;
    expect(entry).toMatchObject({
      path: '/api/stead/voice',
      status: 200,
      user_id: USER_ID,
      audio_bytes: somAudio.length,
    });

    const logged = logLines.join('\n');
    expect(logged).not.toContain('Anything on tomorrow?');
    expect(logged).not.toContain('diary');
    expect(logged).not.toContain('SUQzB');
    expect(logged).not.toContain(N8N_SECRET);
  });

  it('answers a preflight for the voice route', async () => {
    const request = new Request('https://gateway.example.com/api/stead/voice', {
      method: 'OPTIONS',
      headers: { Origin: ORIGIN, 'Access-Control-Request-Method': 'POST' },
    });
    const response = await call(request);
    expect(response.status).toBe(204);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe(ORIGIN);
  });
});
