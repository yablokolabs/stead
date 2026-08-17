import { useMemo } from 'react';
import { AuthProvider, useAuth } from './auth/AuthProvider';
import { Home } from './components/Home';
import { SignIn } from './components/SignIn';
import { readEnv, type SteadEnv } from './lib/env';
import { getSupabaseClient } from './lib/supabase';

type Config = { ok: true; env: SteadEnv } | { ok: false; message: string };

function readConfig(): Config {
  try {
    return { ok: true, env: readEnv() };
  } catch (cause) {
    return { ok: false, message: cause instanceof Error ? cause.message : String(cause) };
  }
}

function Screens({ apiUrl }: { apiUrl: string }) {
  const { status } = useAuth();
  if (status === 'loading') {
    return (
      <main className="screen screen--centred">
        <h1 className="wordmark">Stead</h1>
        <p className="tagline">One moment…</p>
      </main>
    );
  }
  return status === 'signed-in' ? <Home apiUrl={apiUrl} /> : <SignIn />;
}

export function App() {
  const config = useMemo(readConfig, []);

  if (!config.ok) {
    return (
      <main className="screen screen--centred">
        <h1 className="wordmark">Stead</h1>
        <p className="notice notice--error" role="alert">
          {config.message}
        </p>
      </main>
    );
  }

  const client = getSupabaseClient(config.env);
  return (
    <AuthProvider client={client}>
      <Screens apiUrl={config.env.steadApiUrl} />
    </AuthProvider>
  );
}
