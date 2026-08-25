import { createRemoteJWKSet, jwtVerify } from 'jose';

/** Identity the gateway is willing to act on. Never assembled from the body. */
export interface TrustedUser {
  id: string;
  email?: string;
}

type JwkSet = ReturnType<typeof createRemoteJWKSet>;

/**
 * One JWKS per Supabase URL, reused for the life of the isolate.
 *
 * `createRemoteJWKSet` handles fetching, caching and its own cooldown, so a
 * burst of requests does not become a burst of key fetches.
 */
const keySets = new Map<string, JwkSet>();

function keySetFor(supabaseUrl: string): JwkSet {
  let set = keySets.get(supabaseUrl);
  if (!set) {
    set = createRemoteJWKSet(new URL(`${supabaseUrl}/auth/v1/.well-known/jwks.json`));
    keySets.set(supabaseUrl, set);
  }
  return set;
}

/** Exposed so tests can start from a clean isolate. */
export function resetKeySets(): void {
  keySets.clear();
}

/**
 * Pull the credential out of an Authorization header.
 *
 * Requires three dot-separated segments, so a bearer value that is not even
 * shaped like a JWT is refused before any key material is fetched.
 */
export function bearerToken(header: string | null): string | null {
  if (!header) return null;
  const match = /^Bearer[ \t]+([\w-]+\.[\w-]+\.[\w-]+)$/i.exec(header.trim());
  return match ? match[1]! : null;
}

/**
 * Verify a Supabase access token and return the identity it proves.
 *
 * The signature is checked against the project's published JWKS. `algorithms`
 * is pinned to the asymmetric set on purpose: without it, a token presented as
 * HS256 could be offered for verification against a published key, which is
 * the classic algorithm-confusion forgery. Returns null for every failure —
 * the caller has no legitimate use for the distinction, and the difference
 * between "expired" and "forged" is not something to tell an anonymous client.
 */
export async function verifySupabaseUser(
  token: string,
  supabaseUrl: string,
): Promise<TrustedUser | null> {
  try {
    const { payload } = await jwtVerify(token, keySetFor(supabaseUrl), {
      issuer: `${supabaseUrl}/auth/v1`,
      audience: 'authenticated',
      algorithms: ['ES256', 'RS256'],
      requiredClaims: ['sub', 'exp'],
      clockTolerance: '5s',
    });

    // Rejects the project's own anon/publishable JWT, which is not a user.
    if (payload.role !== 'authenticated') return null;

    const id = payload.sub;
    if (typeof id !== 'string' || id.length === 0) return null;

    const email = typeof payload.email === 'string' ? payload.email : undefined;
    return email ? { id, email } : { id };
  } catch {
    return null;
  }
}
