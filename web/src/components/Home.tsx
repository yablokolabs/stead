import { useState, type FormEvent } from 'react';
import { useAuth } from '../auth/AuthProvider';
import { askStead, SteadApiError } from '../lib/api';

function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return 'Good morning.';
  if (hour < 18) return 'Good afternoon.';
  return 'Good evening.';
}

export function Home({ apiUrl }: { apiUrl: string }) {
  const { email, signOut, getAccessToken } = useAuth();
  const [message, setMessage] = useState('');
  const [reply, setReply] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; expired: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text || busy) return;

    setBusy(true);
    setError(null);
    setReply(null);
    try {
      const token = await getAccessToken();
      if (!token) throw new SteadApiError('unauthorized', 401);
      setReply(await askStead({ apiUrl, accessToken: token, message: text }));
      setMessage('');
    } catch (cause) {
      const failure =
        cause instanceof SteadApiError ? cause : new SteadApiError('unknown', 0);
      setError({ text: failure.userMessage, expired: failure.code === 'unauthorized' });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="screen">
      <header className="stack stack--tight">
        <h1 className="wordmark">Stead</h1>
        <p className="tagline">{greeting()}</p>
      </header>

      <button
        type="button"
        className="button button--ghost"
        disabled
        title="Voice is not available on the web yet. Send Stead a Telegram voice note in the meantime."
      >
        Talk to Stead
        <span className="badge">soon</span>
      </button>

      <p className="divider">or</p>

      <form className="stack" onSubmit={onSubmit}>
        <label className="field">
          <span>Ask Stead…</span>
          <textarea
            name="message"
            rows={3}
            maxLength={4000}
            placeholder="What's happening tomorrow?"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </label>

        <button
          type="submit"
          className="button button--primary"
          disabled={busy || message.trim().length === 0}
        >
          {busy ? 'Asking Stead…' : 'Send'}
        </button>
      </form>

      {error && (
        <div className="notice notice--error" role="alert">
          <p>{error.text}</p>
          {error.expired && (
            <button type="button" className="button button--link" onClick={() => void signOut()}>
              Sign in again
            </button>
          )}
        </div>
      )}

      {reply && (
        <section className="reply" aria-live="polite">
          <p>{reply}</p>
        </section>
      )}

      <footer className="footer">
        <p className="footnote">
          Signed in as
          <br />
          <strong>{email}</strong>
        </p>
        <button type="button" className="button button--ghost" onClick={() => void signOut()}>
          Sign out
        </button>
      </footer>
    </main>
  );
}
