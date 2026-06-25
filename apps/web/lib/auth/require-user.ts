// Auth guard for API routes & server components.
//
// 1. Resolve the current session via the wigsso (shared SSO) Supabase client.
// 2. Upsert the user into the local `users` table on first sight, so that
//    new wigsso accounts gain wigvo access automatically (matches the wigex
//    pattern). Use `deleted_at` to revoke per-service access without
//    touching wigsso.
// 3. Return a slim { id, email, name } object — DB queries downstream use id.

import 'server-only';
import { eq } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { createClient } from '@/lib/supabase/server';

export type AuthedUser = {
  id: string;
  email: string | null;
  name: string | null;
};

export class UnauthorizedError extends Error {
  constructor(message = 'unauthorized') {
    super(message);
    this.name = 'UnauthorizedError';
  }
}

export class ForbiddenError extends Error {
  constructor(message = 'forbidden') {
    super(message);
    this.name = 'ForbiddenError';
  }
}

export async function requireUser(): Promise<AuthedUser> {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    throw new UnauthorizedError('no session');
  }
  const authUser = data.user;
  const meta = (authUser.user_metadata ?? {}) as Record<string, unknown>;
  const name =
    (typeof meta.name === 'string' && meta.name) ||
    (typeof meta.full_name === 'string' && meta.full_name) ||
    null;
  const email = authUser.email ?? null;

  const [existing] = await db
    .select({
      id: schema.users.id,
      email: schema.users.email,
      name: schema.users.name,
      deletedAt: schema.users.deletedAt,
    })
    .from(schema.users)
    .where(eq(schema.users.id, authUser.id))
    .limit(1);

  if (existing) {
    if (existing.deletedAt) {
      throw new ForbiddenError('account revoked');
    }
    return { id: existing.id, email: existing.email, name: existing.name };
  }

  // Auto-provision on first contact.
  await db
    .insert(schema.users)
    .values({ id: authUser.id, email, name })
    .onConflictDoNothing({ target: schema.users.id });

  return { id: authUser.id, email, name };
}
