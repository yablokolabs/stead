/**
 * Browsers do not agree on a recording container.
 *
 * Chrome and Firefox produce `audio/webm;codecs=opus`; Safari — including every
 * iOS browser, and the home-screen PWA — produces `audio/mp4`. Both are sent
 * with codec parameters attached, so the type has to be matched on its base.
 */
const ALLOWED_AUDIO = [
  'audio/webm',
  'audio/ogg',
  'audio/mp4',
  'audio/mpeg',
  // Non-standard, but what OpenAI's text-to-speech node actually labels its
  // output. Omitting it made the gateway discard every spoken reply as an
  // unacceptable media type — the defensive check firing on legitimate data.
  'audio/mp3',
  'audio/wav',
  'audio/x-m4a',
  'audio/m4a',
  'audio/flac',
] as const;

/**
 * The recording's media type, or null if it is not one we accept.
 *
 * Parameters such as `;codecs=opus` are dropped: they matter to the browser
 * that wrote the file, not to the transcriber that reads it.
 */
export function audioMimeOf(header: string | null): string | null {
  if (!header) return null;
  const base = header.split(';')[0]!.trim().toLowerCase();
  return (ALLOWED_AUDIO as readonly string[]).includes(base) ? base : null;
}

/**
 * Base64 for a byte array.
 *
 * Chunked because `String.fromCharCode(...bytes)` spreads every byte into an
 * argument list, and V8 overflows the stack between 100 kB and 200 kB of them.
 * A twenty-second voice note already clears that, so the naive one-liner would
 * work in testing and throw on the first person who spoke a full sentence.
 */
export function toBase64(bytes: Uint8Array): string {
  const CHUNK = 0x8000;
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + CHUNK));
  }
  return btoa(binary);
}
