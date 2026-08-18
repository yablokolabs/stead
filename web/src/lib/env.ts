export interface SteadEnv {
  /** Supabase project URL, e.g. https://<ref>.supabase.co */
  supabaseUrl: string;
  /** Browser-safe publishable key. Never the service-role key. */
  supabasePublishableKey: string;
  /** Origin of the Cloudflare Worker gateway. No n8n URL ever reaches here. */
  steadApiUrl: string;
}

export class ConfigError extends Error {
  constructor(readonly missing: string[]) {
    super(
      `Stead is not configured. Missing: ${missing.join(', ')}. ` +
        'Copy web/.env.example to web/.env.local and fill it in.',
    );
    this.name = 'ConfigError';
  }
}

type EnvSource = Record<string, string | undefined>;

/**
 * Read configuration, or throw with every missing name at once.
 *
 * A function rather than a module-level constant so a misconfigured build shows
 * the reader an explanation instead of a blank page.
 */
export function readEnv(source: EnvSource = import.meta.env): SteadEnv {
  const names = ['VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY', 'VITE_STEAD_API_URL'] as const;
  const missing = names.filter((name) => !source[name]?.trim());
  if (missing.length > 0) throw new ConfigError([...missing]);

  return {
    supabaseUrl: source.VITE_SUPABASE_URL!.trim(),
    supabasePublishableKey: source.VITE_SUPABASE_PUBLISHABLE_KEY!.trim(),
    steadApiUrl: source.VITE_STEAD_API_URL!.trim().replace(/\/+$/, ''),
  };
}
