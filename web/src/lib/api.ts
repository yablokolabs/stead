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
 * Nothing the gateway or the agent sends is ever rendered. An upstream that
 * starts returning stack traces, internal URLs or secrets cannot put them on
 * screen.
 */
const USER_MESSAGE: Record<SteadErrorCode, string> = {
  unauthorized: 'Your session has expired. Please sign in again.',
  invalid_request: 'Stead could not read that. Try again.',
  payload_too_large: 'That was too long. Try something shorter.',
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

export interface SteadReply {
  reply: string;
  /** What Stead heard. Present only when the turn was spoken. */
  transcript?: string;
  /** Stead's reply as audio, ready for an object URL. */
  audio?: Blob;
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

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

/** Base64 to a Blob. Returns nothing if either half is missing or malformed. */
function decodeAudio(base64: unknown, mime: unknown): Blob | undefined {
  const encoded = text(base64);
  const type = text(mime);
  if (!encoded || !type) return undefined;

  try {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type });
  } catch {
    return undefined;
  }
}

function toReply(body: unknown, status: number): SteadReply {
  const record = body as Record<string, unknown> | null;
  const reply = text(record?.reply);
  if (!reply) throw new SteadApiError('agent_unavailable', status);

  const result: SteadReply = { reply };
  const transcript = text(record?.transcript);
  if (transcript) result.transcript = transcript;
  const audio = decodeAudio(record?.audio_base64, record?.audio_mime);
  if (audio) result.audio = audio;
  return result;
}

async function send(
  url: string,
  init: RequestInit,
  fetchImpl: typeof fetch,
): Promise<SteadReply> {
  let response: Response;
  try {
    response = await fetchImpl(url, init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new SteadApiError('network', 0);
  }

  const body = await readJson(response);
  if (!response.ok) throw new SteadApiError(codeFor(response.status, body), response.status);
  return toReply(body, response.status);
}

interface CallOptions {
  apiUrl: string;
  accessToken: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

/** Send one typed message to Stead. */
export async function askStead({
  apiUrl,
  accessToken,
  message,
  signal,
  fetchImpl = fetch,
}: CallOptions & { message: string }): Promise<SteadReply> {
  return send(
    `${apiUrl}/api/stead`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify({ message }),
      signal,
    },
    fetchImpl,
  );
}

/**
 * Send one recording to Stead.
 *
 * The body is the audio itself rather than base64 in JSON: encoding would add
 * a third to every upload, and a phone on mobile data is the case that matters.
 */
export async function speakToStead({
  apiUrl,
  accessToken,
  recording,
  signal,
  fetchImpl = fetch,
}: CallOptions & { recording: Blob }): Promise<SteadReply> {
  return send(
    `${apiUrl}/api/stead/voice`,
    {
      method: 'POST',
      headers: {
        'Content-Type': recording.type || 'audio/webm',
        Authorization: `Bearer ${accessToken}`,
      },
      body: recording,
      signal,
    },
    fetchImpl,
  );
}
