// GET /api/calls/[id] - fetch a single call by id (owner-scoped)

import { NextRequest, NextResponse } from 'next/server';
import { and, eq } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { callRowFromDb } from '@/lib/db/mappers';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { toCallResponse } from '@/lib/supabase/helpers';

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const user = await requireUser();

    const [row] = await db
      .select()
      .from(schema.calls)
      .where(and(eq(schema.calls.id, id), eq(schema.calls.userId, user.id)))
      .limit(1);

    if (!row) {
      return NextResponse.json({ error: 'Call not found' }, { status: 404 });
    }

    return NextResponse.json(toCallResponse(callRowFromDb(row)));
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to get call:', error);
    return NextResponse.json({ error: 'Failed to get call' }, { status: 500 });
  }
}
