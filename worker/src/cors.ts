/**
 * CORS for a browser-only, token-authenticated API.
 *
 * There is deliberately no `Access-Control-Allow-Credentials`: the session
 * travels in an Authorization header, never a cookie, so the browser has no
 * ambient credential to attach and CSRF has nothing to ride on.
 */

export type OriginDecision =
  | { kind: 'absent' }
  | { kind: 'allowed'; origin: string }
  | { kind: 'denied'; origin: string };

/** Comma-separated so one Worker can serve localhost and a preview origin. */
export function allowedOrigins(configured: string): string[] {
  return configured
    .split(',')
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

/**
 * An absent Origin is not a denial.
 *
 * CORS is enforced by browsers, and a browser always sends Origin on a
 * cross-origin request. curl and server-to-server callers send none; refusing
 * them would break debugging without closing anything, because the real
 * boundary is the verified JWT, not this header.
 */
export function classifyOrigin(request: Request, configured: string): OriginDecision {
  const origin = request.headers.get('Origin');
  if (!origin) return { kind: 'absent' };
  return allowedOrigins(configured).includes(origin)
    ? { kind: 'allowed', origin }
    : { kind: 'denied', origin };
}

export function corsHeaders(decision: OriginDecision): Record<string, string> {
  if (decision.kind !== 'allowed') return {};
  return {
    'Access-Control-Allow-Origin': decision.origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}
