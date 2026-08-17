import { bearerToken, verifySupabaseUser, type TrustedUser } from './auth';
import { classifyOrigin, corsHeaders, type OriginDecision } from './cors';

export interface Env {
  /** Supabase project URL. Public; a plain var, not a secret. */
  SUPABASE_URL: string;
  /** Comma-separated browser origins allowed to call this Worker. */
  ALLOWED_ORIGIN: string;
  /** Secret. The n8n webhook, which must never reach the browser. */
  N8N_WEBHOOK_URL?: string;
  /** Secret. Proves to n8n that a request came through this gateway. */
  N8N_WEBHOOK_SECRET?: string;
  /** Optional override, in milliseconds. */
  N8N_TIMEOUT_MS?: string;
}

const SERVICE = 'stead-gateway';
const MAX_BODY_BYTES = 16 * 1024;
const MAX_MESSAGE_CHARS = 4000;
const DEFAULT_TIMEOUT_MS = 60_000;

type ErrorCode =
  | 'unauthorized'
  | 'invalid_request'
  | 'payload_too_large'
  | 'agent_unavailable'
  | 'server_misconfigured'
  | 'not_found'
  | 'method_not_allowed'
  | 'unknown';

interface Log {
  requestId: string;
  method: string;
  path: string;
  userId?: string;
  upstreamStatus?: number;
}

function json(body: unknown, status: number, extra: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      ...extra,
    },
  });
}

/** The only error shape this Worker emits. No upstream text ever reaches it. */
function fail(code: ErrorCode, status: number, extra: Record<string, string>): Response {
  return json({ error: code }, status, extra);
}

function timeoutMs(env: Env): number {
  const parsed = Number(env.N8N_TIMEOUT_MS);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_TIMEOUT_MS;
}

/**
 * Read a reply out of whatever n8n returned.
 *
 * The documented contract is `{"reply": "..."}`. `output` is accepted because
 * that is the AI Agent node's own field name, and a single-element array
 * because that is how n8n hands items to Respond to Webhook.
 */
function replyFrom(text: string): string | null {
  if (text.trim().length === 0) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return text;
  }

  if (typeof parsed === 'string') return parsed;
  if (Array.isArray(parsed)) parsed = parsed[0];
  if (typeof parsed !== 'object' || parsed === null) return null;

  const record = parsed as Record<string, unknown>;
  for (const key of ['reply', 'output'] as const) {
    const value = record[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return null;
}

/**
 * Validate the body and return the message.
 *
 * Only `message` is read. Any `user`, `user_id` or `household_id` the browser
 * sends is discarded here rather than filtered later — identity comes from the
 * verified token and from nowhere else.
 */
async function readMessage(request: Request): Promise<{ message: string } | { code: ErrorCode; status: number }> {
  const contentType = request.headers.get('Content-Type') ?? '';
  if (!contentType.toLowerCase().includes('application/json')) {
    return { code: 'invalid_request', status: 415 };
  }

  const declared = Number(request.headers.get('Content-Length'));
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    return { code: 'payload_too_large', status: 413 };
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return { code: 'payload_too_large', status: 413 };

  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return { code: 'invalid_request', status: 400 };
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    return { code: 'invalid_request', status: 400 };
  }

  const message = (body as Record<string, unknown>).message;
  if (typeof message !== 'string') return { code: 'invalid_request', status: 400 };

  const trimmed = message.trim();
  if (trimmed.length === 0) return { code: 'invalid_request', status: 400 };
  if (trimmed.length > MAX_MESSAGE_CHARS) return { code: 'payload_too_large', status: 413 };

  return { message: trimmed };
}

/** Forward a verified message to n8n. Secrets are set here, never proxied. */
async function forward(
  env: Env,
  user: TrustedUser,
  message: string,
  log: Log,
): Promise<string | null> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    'X-Stead-Request-Id': log.requestId,
    'X-Stead-User-Id': user.id,
  };
  if (user.email) headers['X-Stead-User-Email'] = user.email;
  if (env.N8N_WEBHOOK_SECRET) headers['X-Stead-Webhook-Secret'] = env.N8N_WEBHOOK_SECRET;

  const upstream = await fetch(env.N8N_WEBHOOK_URL!, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      message,
      user: user.email ? { id: user.id, email: user.email } : { id: user.id },
      channel: 'web',
      request_id: log.requestId,
    }),
    signal: AbortSignal.timeout(timeoutMs(env)),
  });

  log.upstreamStatus = upstream.status;
  if (!upstream.ok) return null;
  return replyFrom(await upstream.text());
}

async function handleStead(
  request: Request,
  env: Env,
  cors: Record<string, string>,
  log: Log,
): Promise<Response> {
  // Authenticate before anything else, so an anonymous caller learns nothing
  // about this deployment's validation rules or configuration state.
  const token = bearerToken(request.headers.get('Authorization'));
  if (!token) return fail('unauthorized', 401, cors);

  const user = await verifySupabaseUser(token, env.SUPABASE_URL);
  if (!user) return fail('unauthorized', 401, cors);
  log.userId = user.id;

  const parsed = await readMessage(request);
  if ('code' in parsed) return fail(parsed.code, parsed.status, cors);

  if (!env.N8N_WEBHOOK_URL) {
    console.error(JSON.stringify({ service: SERVICE, error: 'N8N_WEBHOOK_URL is not set' }));
    return fail('server_misconfigured', 500, cors);
  }

  let reply: string | null;
  try {
    reply = await forward(env, user, parsed.message, log);
  } catch {
    // Timeout, DNS, TLS. The cause stays in the logs, never in the response.
    return fail('agent_unavailable', 502, cors);
  }

  if (reply === null) return fail('agent_unavailable', 502, cors);
  return json({ reply }, 200, cors);
}

async function route(request: Request, env: Env, log: Log): Promise<Response> {
  const url = new URL(request.url);

  if (url.pathname === '/health') {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return fail('method_not_allowed', 405, {});
    }
    // Unauthenticated and carries no data, so it is safe for uptime checks
    // from any origin. Nothing else in this Worker uses a wildcard.
    return json({ status: 'ok', service: SERVICE }, 200, { 'Access-Control-Allow-Origin': '*' });
  }

  if (url.pathname === '/api/stead') {
    const decision: OriginDecision = classifyOrigin(request, env.ALLOWED_ORIGIN);
    if (decision.kind === 'denied') return fail('unauthorized', 403, {});

    const cors = corsHeaders(decision);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'POST') return fail('method_not_allowed', 405, cors);
    return handleStead(request, env, cors, log);
  }

  return fail('not_found', 404, {});
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const started = Date.now();
    const url = new URL(request.url);
    const log: Log = {
      requestId: crypto.randomUUID(),
      method: request.method,
      path: url.pathname,
    };

    let response: Response;
    try {
      response = await route(request, env, log);
    } catch (cause) {
      console.error(
        JSON.stringify({
          service: SERVICE,
          request_id: log.requestId,
          error: cause instanceof Error ? cause.name : 'unhandled',
        }),
      );
      response = fail('unknown', 500, {});
    }

    // One line per request. No token, no message body, no secret, no email.
    console.log(
      JSON.stringify({
        service: SERVICE,
        request_id: log.requestId,
        method: log.method,
        path: log.path,
        status: response.status,
        upstream_status: log.upstreamStatus ?? null,
        user_id: log.userId ?? null,
        duration_ms: Date.now() - started,
      }),
    );

    return response;
  },
} satisfies ExportedHandler<Env>;
