import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

/** An origin the browser may talk to, or nothing if the value is unusable. */
function originOf(value: unknown): string[] {
  if (typeof value !== 'string' || value.length === 0) return [];
  try {
    return [new URL(value).origin];
  } catch {
    return [];
  }
}

/**
 * Cloudflare Pages reads `_headers` from the build output.
 *
 * It is generated rather than committed because `connect-src` has to name the
 * exact Supabase and gateway origins, and those differ between local, preview
 * and production. A static file would be a CSP that is silently wrong in two
 * environments out of three — and a CSP that is wrong in the permissive
 * direction is worse than none, because it still looks like protection.
 */
function securityHeaders(): Plugin {
  let connectSrc: string[] = [];

  return {
    name: 'stead:security-headers',
    apply: 'build',

    configResolved(config) {
      connectSrc = [
        ...originOf(config.env.VITE_SUPABASE_URL),
        ...originOf(config.env.VITE_STEAD_API_URL),
      ];
    },

    generateBundle() {
      const csp = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "manifest-src 'self'",
        `connect-src ${["'self'", ...connectSrc].join(' ')}`,
        'upgrade-insecure-requests',
      ].join('; ');

      const headers = [
        `Content-Security-Policy: ${csp}`,
        'X-Content-Type-Options: nosniff',
        'Referrer-Policy: strict-origin-when-cross-origin',
        'X-Frame-Options: DENY',
        // microphone is granted to this origin only, for push to talk. Every
        // other capability stays denied, including to any embedded frame.
        'Permissions-Policy: accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(self), payment=(), usb=()',
        'Cross-Origin-Opener-Policy: same-origin',
      ];

      this.emitFile({
        type: 'asset',
        fileName: '_headers',
        source: `/*\n${headers.map((line) => `  ${line}`).join('\n')}\n`,
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), securityHeaders()],
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', sourcemap: false },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
