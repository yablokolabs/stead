import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { SteadEnv } from './env';

let client: SupabaseClient | null = null;

/**
 * The browser's Supabase client, created once.
 *
 * This holds the publishable key only. The service-role key must never be
 * built into this bundle — anything requiring it belongs behind the Worker.
 */
export function getSupabaseClient(env: SteadEnv): SupabaseClient {
  client ??= createClient(env.supabaseUrl, env.supabasePublishableKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
  return client;
}
