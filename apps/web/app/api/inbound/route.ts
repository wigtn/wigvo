import { NextResponse } from 'next/server';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { listInboundCalls } from '@/lib/relay-client';
import { createClient } from '@/lib/supabase/server';

export async function GET() {
  try {
    await requireUser();
    const supabase = await createClient();
    const { data } = await supabase.auth.getSession();
    const accessToken = data.session?.access_token;
    if (!accessToken) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    return NextResponse.json({ calls: await listInboundCalls(accessToken) });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    const status = typeof error === 'object' && error && 'status' in error
      ? Number(error.status)
      : 500;
    console.error('[Inbound] Failed to list waiting calls:', error);
    return NextResponse.json({ error: 'Failed to list inbound calls' }, { status });
  }
}
