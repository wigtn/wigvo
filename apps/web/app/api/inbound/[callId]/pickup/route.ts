import { NextRequest, NextResponse } from 'next/server';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';
import { pickupInboundCall } from '@/lib/relay-client';
import { createClient } from '@/lib/supabase/server';

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ callId: string }> },
) {
  try {
    await requireUser();
    const { callId } = await params;
    const supabase = await createClient();
    const { data } = await supabase.auth.getSession();
    const accessToken = data.session?.access_token;
    if (!accessToken) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    return NextResponse.json(await pickupInboundCall(callId, accessToken));
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    const status = typeof error === 'object' && error && 'status' in error
      ? Number(error.status)
      : 500;
    console.error('[Inbound] Failed to pickup call:', error);
    return NextResponse.json(
      { error: status === 409 ? 'Call already claimed' : 'Failed to pickup inbound call' },
      { status },
    );
  }
}
