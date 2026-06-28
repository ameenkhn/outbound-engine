import "server-only";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Server-side Supabase client using the SERVICE-ROLE key.
 *
 * This bypasses RLS, so it must NEVER be imported into a client component.
 * The `server-only` import above turns any client-side import into a build
 * error. Env is read lazily (inside the function, not at module load) so
 * `next build` succeeds without secrets present; the error only fires at the
 * first real request if env is missing.
 *
 * v1 is an internal ops tool. Protect the Vercel deployment (Vercel password
 * protection or Supabase Auth gating) before exposing it — there is no per-user
 * RLS here. TODO: add Supabase Auth + drop to anon+RLS for least privilege.
 */
let _client: SupabaseClient | null = null;

export function getServerClient(): SupabaseClient {
  if (_client) return _client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error(
      "Missing Supabase env: set NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env.local / Vercel env.",
    );
  }
  _client = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return _client;
}
