import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { auth, getSupabaseClient } = vi.hoisted(() => {
  const auth = {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithPassword: vi.fn(),
    signOut: vi.fn(),
  };
  return { auth, getSupabaseClient: vi.fn(() => ({ auth })) };
});

vi.mock('./lib/supabase', () => ({ getSupabaseClient }));

const { App } = await import('./App');

const SESSION = {
  access_token: 'header.payload.signature',
  user: { id: 'u-1', email: 'kerstin@example.com' },
};

const API = 'https://api.example.com';

function signedIn(session: unknown = SESSION) {
  auth.getSession.mockResolvedValue({ data: { session } });
}

beforeEach(() => {
  vi.stubEnv('VITE_SUPABASE_URL', 'https://project.supabase.co');
  vi.stubEnv('VITE_SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_test');
  vi.stubEnv('VITE_STEAD_API_URL', API);

  auth.getSession.mockResolvedValue({ data: { session: null } });
  auth.onAuthStateChange.mockReturnValue({
    data: { subscription: { unsubscribe: vi.fn() } },
  });
  auth.signInWithPassword.mockResolvedValue({ error: null });
  auth.signOut.mockResolvedValue({ error: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('configuration', () => {
  it('explains a missing variable instead of rendering a blank page', async () => {
    vi.stubEnv('VITE_STEAD_API_URL', '');
    render(<App />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('VITE_STEAD_API_URL');
    expect(getSupabaseClient).not.toHaveBeenCalled();
  });
});

describe('routing', () => {
  it('shows the sign-in screen when there is no session', async () => {
    render(<App />);

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Ask Stead…')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sign out/ })).not.toBeInTheDocument();
  });

  it('shows the authenticated shell when a session is restored', async () => {
    signedIn();
    render(<App />);

    expect(await screen.findByText(/Good (morning|afternoon|evening)\./)).toBeInTheDocument();
    expect(screen.getByLabelText('Ask Stead…')).toBeInTheDocument();
    expect(screen.getByText('kerstin@example.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });

  it('offers voice', async () => {
    signedIn();
    render(<App />);
    expect(await screen.findByRole('button', { name: 'Talk to Stead' })).toBeEnabled();
  });

  it('follows the session when it changes', async () => {
    let notify: ((event: string, session: unknown) => void) | undefined;
    auth.onAuthStateChange.mockImplementation((callback: typeof notify) => {
      notify = callback;
      return { data: { subscription: { unsubscribe: vi.fn() } } };
    });

    render(<App />);
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();

    notify!('SIGNED_IN', SESSION);
    expect(await screen.findByLabelText('Ask Stead…')).toBeInTheDocument();
  });
});

describe('signing in', () => {
  it('passes the credentials to Supabase', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText('Email'), 'kerstin@example.com');
    await user.type(screen.getByLabelText('Password'), 'correct horse');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(auth.signInWithPassword).toHaveBeenCalledWith({
      email: 'kerstin@example.com',
      password: 'correct horse',
    });
  });

  it('shows a safe message when the credentials are wrong', async () => {
    auth.signInWithPassword.mockResolvedValue({
      error: { code: 'invalid_credentials', message: 'Invalid login credentials' },
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText('Email'), 'kerstin@example.com');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Check your email address and password.');
  });
});

describe('asking Stead', () => {
  function stubGateway(body: unknown, status = 200) {
    const impl = vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', impl);
    return impl;
  }

  async function ask(text: string) {
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText('Ask Stead…'), text);
    await user.click(screen.getByRole('button', { name: 'Send' }));
    return user;
  }

  beforeEach(() => signedIn());

  it('sends the message with the current access token and shows the reply', async () => {
    const fetchImpl = stubGateway({ reply: 'The boiler service is on Tuesday.' });

    await ask('anything tomorrow?');

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    const [url, init] = fetchImpl.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe(`${API}/api/stead`);
    expect((init.headers as Record<string, string>).Authorization).toBe(
      `Bearer ${SESSION.access_token}`,
    );

    expect(
      await screen.findByText('The boiler service is on Tuesday.'),
    ).toBeInTheDocument();
  });

  it('shows a safe message when the gateway fails, not the upstream text', async () => {
    stubGateway(
      { error: 'agent_unavailable', detail: 'ECONNREFUSED upstream.internal secret=abc' },
      502,
    );

    await ask('anything tomorrow?');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Stead is not reachable at the moment.');
    expect(document.body.textContent).not.toContain('ECONNREFUSED');
    expect(document.body.textContent).not.toContain('secret=abc');
    expect(document.body.textContent).not.toContain('upstream.internal');
  });

  it('offers a way back in when the session has expired', async () => {
    stubGateway({ error: 'unauthorized' }, 401);

    const user = await ask('anything tomorrow?');

    expect(await screen.findByRole('alert')).toHaveTextContent('Your session has expired.');
    await user.click(screen.getByRole('button', { name: 'Sign in again' }));
    expect(auth.signOut).toHaveBeenCalled();
  });

  it('reports a network failure without calling it an agent error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    await ask('anything tomorrow?');

    expect(await screen.findByRole('alert')).toHaveTextContent('No connection to Stead.');
  });

  it('will not send an empty message', async () => {
    const fetchImpl = stubGateway({ reply: 'unused' });
    render(<App />);

    const send = await screen.findByRole('button', { name: 'Send' });
    expect(send).toBeDisabled();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe('talking to Stead', () => {
  let trackStops: number;
  let play: ReturnType<typeof vi.fn>;
  let getUserMedia: ReturnType<typeof vi.fn>;
  let spoken: string[];

  class FakeMediaRecorder {
    static isTypeSupported(type: string) {
      return type === 'audio/webm;codecs=opus';
    }
    state: 'inactive' | 'recording' = 'inactive';
    mimeType: string;
    ondataavailable: ((event: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;

    constructor(_stream: unknown, options?: { mimeType?: string }) {
      this.mimeType = options?.mimeType ?? 'audio/webm';
    }
    start() {
      this.state = 'recording';
    }
    stop() {
      this.state = 'inactive';
      this.ondataavailable?.({ data: new Blob([new Uint8Array([9, 9, 9])], { type: this.mimeType }) });
      this.onstop?.();
    }
  }

  function stubGateway(body: unknown, status = 200) {
    const impl = vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', impl);
    return impl;
  }

  beforeEach(() => {
    signedIn();
    trackStops = 0;

    const track = {
      stop: () => {
        trackStops += 1;
      },
    };
    getUserMedia = vi.fn(async () => ({ getTracks: () => [track] }));

    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    });

    URL.createObjectURL = vi.fn(() => 'blob:stead-reply');
    URL.revokeObjectURL = vi.fn();
    play = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.play = play as unknown as HTMLMediaElement['play'];

    spoken = [];
    class FakeUtterance {
      lang = '';
      voice: unknown = null;
      constructor(public text: string) {}
    }
    vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance);
    vi.stubGlobal('speechSynthesis', {
      cancel: vi.fn(),
      speak: (u: { text: string }) => void spoken.push(u.text),
      getVoices: () => [],
    });
  });

  async function startTalking() {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Talk to Stead' }));
    return user;
  }

  it('opens the microphone and shows that it is listening', async () => {
    await startTalking();

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(await screen.findByRole('status')).toHaveTextContent('Listening…');
    expect(screen.getByRole('button', { name: 'Stop and send' })).toBeInTheDocument();
  });

  it('sends the recording and shows what Stead heard and said', async () => {
    const fetchImpl = stubGateway({
      reply: 'Nothing in the diary tomorrow.',
      transcript: 'Anything on tomorrow?',
    });

    const user = await startTalking();
    await user.click(await screen.findByRole('button', { name: 'Stop and send' }));

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    const [url, init] = fetchImpl.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe(`${API}/api/stead/voice`);
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('audio/webm;codecs=opus');
    expect((init.headers as Record<string, string>).Authorization).toBe(
      `Bearer ${SESSION.access_token}`,
    );

    expect(await screen.findByText('Nothing in the diary tomorrow.')).toBeInTheDocument();
    expect(screen.getByText(/Anything on tomorrow\?/)).toBeInTheDocument();
  });

  /** The agent no longer synthesises; the device does, and it costs nothing. */
  it('speaks the reply itself when the agent sends no audio', async () => {
    stubGateway({ reply: 'Nothing in the diary tomorrow.', transcript: 'Anything on tomorrow?' });

    const user = await startTalking();
    await user.click(await screen.findByRole('button', { name: 'Stop and send' }));

    await screen.findByText('Nothing in the diary tomorrow.');
    // The priming space is spoken during the tap; the reply follows it.
    expect(spoken).toContain('Nothing in the diary tomorrow.');
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('prefers the agent audio if it ever sends any', async () => {
    stubGateway({ reply: 'Nothing tomorrow.', audio_base64: 'aGk=', audio_mime: 'audio/mpeg' });

    const user = await startTalking();
    await user.click(await screen.findByRole('button', { name: 'Stop and send' }));

    await screen.findByText('Nothing tomorrow.');
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    expect(spoken).not.toContain('Nothing tomorrow.');
  });

  it('plays the spoken reply', async () => {
    stubGateway({ reply: 'Nothing tomorrow.', audio_base64: 'aGk=', audio_mime: 'audio/mpeg' });

    const user = await startTalking();
    await user.click(await screen.findByRole('button', { name: 'Stop and send' }));

    await screen.findByText('Nothing tomorrow.');
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    // Once to unlock playback during the tap, once for the reply itself.
    expect(play.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('releases the microphone once the recording is sent', async () => {
    stubGateway({ reply: 'Nothing tomorrow.' });

    const user = await startTalking();
    await user.click(await screen.findByRole('button', { name: 'Stop and send' }));

    await screen.findByText('Nothing tomorrow.');
    expect(trackStops).toBeGreaterThan(0);
  });

  it('explains a refused microphone without sending anything', async () => {
    const fetchImpl = stubGateway({ reply: 'unused' });
    getUserMedia.mockRejectedValue(new DOMException('denied', 'NotAllowedError'));

    await startTalking();

    expect(await screen.findByRole('alert')).toHaveTextContent('needs microphone access');
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Talk to Stead' })).toBeInTheDocument();
  });

  it('explains a browser that cannot record at all', async () => {
    vi.stubGlobal('MediaRecorder', undefined);

    await startTalking();

    expect(await screen.findByRole('alert')).toHaveTextContent('cannot record audio');
  });
});
