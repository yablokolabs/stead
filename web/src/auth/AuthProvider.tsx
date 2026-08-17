import type { Session, SupabaseClient } from '@supabase/supabase-js';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type AuthStatus = 'loading' | 'signed-out' | 'signed-in';

export class SignInError extends Error {
  constructor(readonly userMessage: string) {
    super(userMessage);
    this.name = 'SignInError';
  }
}

export interface AuthValue {
  status: AuthStatus;
  email: string | null;
  signIn(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  /** A fresh access token, refreshed by supabase-js when it has expired. */
  getAccessToken(): Promise<string | null>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}

/** Supabase's own wording is not shown to the user; these two cases are. */
function signInMessage(code: string | undefined): string {
  if (code === 'email_not_confirmed') {
    return 'That address has not been confirmed yet. Check your email for the confirmation link.';
  }
  return 'Could not sign in. Check your email address and password.';
}

export function AuthProvider({
  client,
  children,
}: {
  client: SupabaseClient;
  children: ReactNode;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;

    client.auth.getSession().then(({ data }) => {
      if (!live) return;
      setSession(data.session);
      setReady(true);
    });

    const { data } = client.auth.onAuthStateChange((_event, next) => {
      if (!live) return;
      setSession(next);
      setReady(true);
    });

    return () => {
      live = false;
      data.subscription.unsubscribe();
    };
  }, [client]);

  const value = useMemo<AuthValue>(
    () => ({
      status: !ready ? 'loading' : session ? 'signed-in' : 'signed-out',
      email: session?.user.email ?? null,

      async signIn(email, password) {
        const { error } = await client.auth.signInWithPassword({ email, password });
        if (error) throw new SignInError(signInMessage(error.code));
      },

      async signOut() {
        await client.auth.signOut();
      },

      async getAccessToken() {
        const { data } = await client.auth.getSession();
        return data.session?.access_token ?? null;
      },
    }),
    [client, ready, session],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
