// Drizzle client. The pool is created lazily on first use so that
// `next build` (which evaluates route modules without runtime env) does
// not crash when DATABASE_URL is missing.

import { drizzle, type PostgresJsDatabase } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from './schema';

declare global {
  // eslint-disable-next-line no-var
  var __wigvoPg: ReturnType<typeof postgres> | undefined;
  // eslint-disable-next-line no-var
  var __wigvoDb: PostgresJsDatabase<typeof schema> | undefined;
}

function getQueryClient(): ReturnType<typeof postgres> {
  if (globalThis.__wigvoPg) return globalThis.__wigvoPg;
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error('DATABASE_URL is not set');
  }
  const client = postgres(url, {
    max: 10,
    idle_timeout: 30,
    connect_timeout: 10,
    // Supabase Transaction Pooler (port 6543, used on Vercel serverless) runs in
    // transaction mode and does not support prepared statements. postgres-js uses
    // them by default, so disable to avoid "prepared statement does not exist".
    prepare: false,
  });
  if (process.env.NODE_ENV !== 'production') {
    globalThis.__wigvoPg = client;
  }
  return client;
}

export const db = new Proxy({} as PostgresJsDatabase<typeof schema>, {
  get(_target, prop, receiver) {
    if (!globalThis.__wigvoDb) {
      globalThis.__wigvoDb = drizzle(getQueryClient(), { schema });
    }
    return Reflect.get(globalThis.__wigvoDb, prop, receiver);
  },
});

export type DB = typeof db;
export { schema };
