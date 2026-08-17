/**
 * The only thing this app talks to is the Cloudflare Worker gateway.
 *
 * n8n's webhook URL and secret are not reachable from here by construction:
 * they exist solely as Worker secrets.
 */

/** Error codes this client is willing to recognise. Anything else is `unknown`. */
const KNOWN_CODES = [
  'unauthorized',
  'invalid_request',
  'payload_too_large',
  'agent_unavailable',
  'server_misconfigured',
  'network',
  'unknown',
] as const;

export type SteadErrorCode = (typeof KNOWN_CODES)[number];

/**
 * Fixed copy, indexed by code.
 *
 * Nothing the gateway or n8n sends is ever rendered. An upstream that starts
 * returning stack traces, internal URLs or secrets cannot put them on screen.
 */
const USER_MESSAGE: Record<SteadErrorCode, string> = {
  unauthorized: 'Your session has expired. Please sign in again.',
  invalid_request: 'Stead could not read that message. Try rephrasing it.',
  payload_too_large: 'That message is too long. Try a shorter one.',
  agent_unavailable: 'Stead is not reachable at the moment. Try again shortly.',
  server_misconfigured: 'Stead is not reachable at the moment. Try again shortly.',
  network: 'No connection to Stead. Check your network and try again.',
  unknown: 'Something went wrong. Try again.',
};

export class SteadApiError extends Error {
  readonly code: SteadErrorCode;
  readonly status: number;
  /** The only string a component may display. */
  readonly userMessage: string;

  constructor(code: SteadErrorCode, status: number) {
    super(`stead api error: ${code} (${status})`);
    this.name = 'SteadApiError';
    this.code = code;
    this.status = status;
    this.userMessage = USER_MESSAGE[code];
  }
}

function isKnownCode(value: unknown): value is SteadErrorCode {
  return typeof value === 'string' && (KNOWN_CODES as readonly string[]).includes(value);
}

/** Fall back on the status when the body carries no code we recognise. */
function codeFor(status: number, body: unknown): SteadErrorCode {
  const declared = (body as { error?: unknown } | null)?.error;
  if (isKnownCode(declared)) return declared;
  if (status === 401 || status === 403) return 'unauthorized';
  if (status === 413) return 'payload_too_large';
  if (status >= 400 && status < 500) return 'invalid_request';
  if (status >= 500) return 'agent_unavailable';
  return 'unknown';
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export interface AskSteadOptions {
  apiUrl: string;
  accessToken: string;
  message: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

/** Send one message to Stead and return its reply. */
export async function askStead({
  apiUrl,
  accessToken,
  message,
  signal,
  fetchImpl = fetch,
}: AskSteadOptions): Promise<string> {
  let response: Response;
  try {
    response = await fetchImpl(`${apiUrl}/api/stead`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ message }),
      signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new SteadApiError('network', 0);
  }

  const body = await readJson(response);
  if (!response.ok) throw new SteadApiError(codeFor(response.status, body), response.status);

  const reply = (body as { reply?: unknown } | null)?.reply;
  if (typeof reply !== 'string') throw new SteadApiError('agent_unavailable', response.status);
  return reply;
}
