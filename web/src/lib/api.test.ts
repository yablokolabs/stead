import { describe, expect, it, vi } from 'vitest';
import { askStead, speakToStead, SteadApiError } from './api';

const API = 'https://api.example.com';
const TOKEN = 'header.payload.signature';

function respondWith(body: unknown, status = 200): typeof fetch {
  return vi.fn(async () =>
    new Response(typeof body === 'string' ? body : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

async function failWith(body: unknown, status: number): Promise<SteadApiError> {
  try {
    await askStead({
      apiUrl: API,
      accessToken: TOKEN,
      message: 'hello',
      fetchImpl: respondWith(body, status),
    });
  } catch (cause) {
    return cause as SteadApiError;
  }
  throw new Error('expected askStead to reject');
}

describe('askStead', () => {
  it('attaches the bearer token and posts to the gateway', async () => {
    const fetchImpl = respondWith({ reply: 'Nothing tomorrow.' });

    await askStead({
      apiUrl: API,
      accessToken: TOKEN,
      message: "What's happening tomorrow?",
      fetchImpl,
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = vi.mocked(fetchImpl).mock.calls[0]!;
    expect(url).toBe(`${API}/api/stead`);
    expect(init?.method).toBe('POST');
    expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(JSON.parse(init?.body as string)).toEqual({
      message: "What's happening tomorrow?",
    });
  });

  it('sends no identity of its own', async () => {
    const fetchImpl = respondWith({ reply: 'ok' });
    await askStead({ apiUrl: API, accessToken: TOKEN, message: 'hello', fetchImpl });

    const body = JSON.parse(vi.mocked(fetchImpl).mock.calls[0]![1]?.body as string);
    expect(Object.keys(body)).toEqual(['message']);
  });

  it('returns the reply', async () => {
    const result = await askStead({
      apiUrl: API,
      accessToken: TOKEN,
      message: 'hello',
      fetchImpl: respondWith({ reply: 'Nothing tomorrow.' }),
    });
    expect(result).toEqual({ reply: 'Nothing tomorrow.' });
  });

  it.each([
    ['unauthorized', { error: 'unauthorized' }, 401],
    ['invalid_request', { error: 'invalid_request' }, 400],
    ['payload_too_large', { error: 'payload_too_large' }, 413],
    ['agent_unavailable', { error: 'agent_unavailable' }, 502],
    ['server_misconfigured', { error: 'server_misconfigured' }, 500],
  ])('surfaces %s', async (code, body, status) => {
    const error = await failWith(body, status);
    expect(error).toBeInstanceOf(SteadApiError);
    expect(error.code).toBe(code);
    expect(error.userMessage).toBeTruthy();
  });

  it('falls back on the status when the body carries no known code', async () => {
    expect((await failWith({ error: 'something_new' }, 401)).code).toBe('unauthorized');
    expect((await failWith({}, 400)).code).toBe('invalid_request');
    expect((await failWith('<html>502 Bad Gateway</html>', 502)).code).toBe('agent_unavailable');
  });

  /**
   * The gateway is trusted not to leak upstream text, but this client does not
   * rely on that: nothing a server sends is ever put into a displayed string.
   */
  it('never turns server text into a user-facing message', async () => {
    const nasty = '<img src=x onerror=alert(1)> secret=abc123 at /opt/agent/workflow.js:42';
    const error = await failWith({ error: nasty, detail: nasty }, 500);

    expect(error.userMessage).not.toContain('secret');
    expect(error.userMessage).not.toContain('onerror');
    expect(error.userMessage).not.toContain('workflow.js');
    expect(error.userMessage).toBe('Stead is not reachable at the moment. Try again shortly.');
  });

  it('reports a network failure distinctly', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;

    await expect(
      askStead({ apiUrl: API, accessToken: TOKEN, message: 'hello', fetchImpl }),
    ).rejects.toMatchObject({ code: 'network' });
  });

  it('rejects a 200 that carries no reply string', async () => {
    await expect(
      askStead({
        apiUrl: API,
        accessToken: TOKEN,
        message: 'hello',
        fetchImpl: respondWith({ workflow: 'started' }),
      }),
    ).rejects.toMatchObject({ code: 'agent_unavailable' });
  });

  it('lets an abort propagate rather than reporting it as a failure', async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchImpl = vi.fn(async () => {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }) as unknown as typeof fetch;

    await expect(
      askStead({
        apiUrl: API,
        accessToken: TOKEN,
        message: 'hello',
        signal: controller.signal,
        fetchImpl,
      }),
    ).rejects.toBeInstanceOf(DOMException);
  });
});

describe('speakToStead', () => {
  const recording = () => new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/webm;codecs=opus' });

  it('posts the recording to the voice route with its own media type', async () => {
    const fetchImpl = respondWith({ reply: 'Nothing tomorrow.' });

    await speakToStead({ apiUrl: API, accessToken: TOKEN, recording: recording(), fetchImpl });

    const [url, init] = vi.mocked(fetchImpl).mock.calls[0]!;
    expect(url).toBe(`${API}/api/stead/voice`);
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(headers['Content-Type']).toBe('audio/webm;codecs=opus');
    expect(init?.body).toBeInstanceOf(Blob);
  });

  it('falls back to webm when the browser gave the blob no type', async () => {
    const fetchImpl = respondWith({ reply: 'ok' });
    await speakToStead({
      apiUrl: API,
      accessToken: TOKEN,
      recording: new Blob([new Uint8Array([1])]),
      fetchImpl,
    });

    const headers = vi.mocked(fetchImpl).mock.calls[0]![1]?.headers as Record<string, string>;
    expect(headers['Content-Type']).toBe('audio/webm');
  });

  it('decodes the spoken reply into a playable blob', async () => {
    // "hi" as base64.
    const result = await speakToStead({
      apiUrl: API,
      accessToken: TOKEN,
      recording: recording(),
      fetchImpl: respondWith({
        reply: 'Nothing tomorrow.',
        transcript: 'Anything on tomorrow?',
        audio_base64: 'aGk=',
        audio_mime: 'audio/mpeg',
      }),
    });

    expect(result.reply).toBe('Nothing tomorrow.');
    expect(result.transcript).toBe('Anything on tomorrow?');
    expect(result.audio).toBeInstanceOf(Blob);
    expect(result.audio!.type).toBe('audio/mpeg');
    await expect(result.audio!.text()).resolves.toBe('hi');
  });

  it('still returns the reply when the agent sent no audio', async () => {
    const result = await speakToStead({
      apiUrl: API,
      accessToken: TOKEN,
      recording: recording(),
      fetchImpl: respondWith({ reply: 'Nothing tomorrow.' }),
    });
    expect(result).toEqual({ reply: 'Nothing tomorrow.' });
  });

  it.each([
    ['malformed base64', { reply: 'ok', audio_base64: '!!!not base64!!!', audio_mime: 'audio/mpeg' }],
    ['a missing media type', { reply: 'ok', audio_base64: 'aGk=' }],
    ['a missing payload', { reply: 'ok', audio_mime: 'audio/mpeg' }],
  ])('drops audio with %s rather than failing the turn', async (_label, body) => {
    const result = await speakToStead({
      apiUrl: API,
      accessToken: TOKEN,
      recording: recording(),
      fetchImpl: respondWith(body),
    });
    expect(result.reply).toBe('ok');
    expect(result.audio).toBeUndefined();
  });

  it('surfaces a recording the gateway refused as too large', async () => {
    await expect(
      speakToStead({
        apiUrl: API,
        accessToken: TOKEN,
        recording: recording(),
        fetchImpl: respondWith({ error: 'payload_too_large' }, 413),
      }),
    ).rejects.toMatchObject({ code: 'payload_too_large' });
  });
});
