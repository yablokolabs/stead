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

  it('offers voice but does not yet enable it', async () => {
    signedIn();
    render(<App />);
    expect(await screen.findByRole('button', { name: /Talk to Stead/ })).toBeDisabled();
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
