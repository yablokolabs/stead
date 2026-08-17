import { useState, type FormEvent } from 'react';
import { SignInError, useAuth } from '../auth/AuthProvider';

export function SignIn() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(email.trim(), password);
    } catch (cause) {
      setError(cause instanceof SignInError ? cause.userMessage : 'Could not sign in. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="screen screen--centred">
      <h1 className="wordmark">Stead</h1>
      <p className="tagline">Your household manager.</p>

      <form className="stack" onSubmit={onSubmit}>
        <label className="field">
          <span>Email</span>
          <input
            type="email"
            name="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error && (
          <p className="notice notice--error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="button button--primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <p className="footnote">Stead is invite-only. Ask for an account if you do not have one.</p>
    </main>
  );
}
