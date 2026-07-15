// GET /api/metrics — aggregate call-quality metrics across completed calls.

import { NextRequest, NextResponse } from 'next/server';
import { and, desc, eq } from 'drizzle-orm';
import { db, schema } from '@/lib/db/client';
import { requireUser } from '@/lib/auth/require-user';
import { authErrorResponse } from '@/lib/auth/route-helpers';

interface CallMetricsRow {
  session_a_latencies_ms: number[];
  session_b_e2e_latencies_ms: number[];
  session_b_stt_latencies_ms: number[];
  first_message_latency_ms: number;
  turn_count: number;
  echo_suppressions: number;
  hallucinations_blocked: number;
  vad_false_triggers: number;
  echo_loops_detected: number;
}

interface CallResultData {
  metrics?: CallMetricsRow;
  cost_usd?: number;
}

function std(arr: number[]): number {
  if (arr.length < 2) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  const variance = arr.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

function stats(arr: number[]) {
  if (arr.length === 0) return { avg: 0, std: 0, min: 0, max: 0, count: 0 };
  return {
    avg: Math.round((arr.reduce((a, b) => a + b, 0) / arr.length) * 10) / 10,
    std: Math.round(std(arr) * 10) / 10,
    min: Math.round(Math.min(...arr) * 10) / 10,
    max: Math.round(Math.max(...arr) * 10) / 10,
    count: arr.length,
  };
}

export async function GET(request: NextRequest) {
  try {
    const user = await requireUser();

    const searchParams = request.nextUrl.searchParams;
    const mode = searchParams.get('mode');
    const limit = Math.min(parseInt(searchParams.get('limit') || '100', 10), 500);

    const whereClause = mode
      ? and(
          eq(schema.calls.tenantId, user.tenantId),
          eq(schema.calls.status, 'COMPLETED'),
          eq(schema.calls.communicationMode, mode),
        )
      : and(
          eq(schema.calls.tenantId, user.tenantId),
          eq(schema.calls.status, 'COMPLETED'),
        );

    const rows = await db
      .select({
        callResultData: schema.calls.callResultData,
        durationS: schema.calls.durationS,
        totalTokens: schema.calls.totalTokens,
        communicationMode: schema.calls.communicationMode,
        status: schema.calls.status,
        createdAt: schema.calls.createdAt,
      })
      .from(schema.calls)
      .where(whereClause)
      .orderBy(desc(schema.calls.createdAt))
      .limit(limit);

    const validCalls = rows.filter((c) => {
      const data = c.callResultData as CallResultData | null;
      return Boolean(data?.metrics);
    });
    const allMetrics = validCalls.map((c) => (c.callResultData as CallResultData).metrics!);

    const allSessionALatencies = allMetrics.flatMap((m) => m.session_a_latencies_ms);
    const allSessionBE2ELatencies = allMetrics.flatMap((m) => m.session_b_e2e_latencies_ms);
    const allSessionBSTTLatencies = allMetrics.flatMap((m) => m.session_b_stt_latencies_ms);
    const allFirstMessageLatencies = allMetrics
      .map((m) => m.first_message_latency_ms)
      .filter((v) => v > 0);

    const durations = validCalls
      .map((c) => c.durationS)
      .filter((v): v is number => v != null && v > 0);
    const totalDurationMin = durations.reduce((a, b) => a + b, 0) / 60;

    const totalTokens = validCalls
      .map((c) => c.totalTokens)
      .filter((v): v is number => v != null)
      .reduce((a, b) => a + b, 0);
    const totalCostUsd = validCalls
      .map((c) => (c.callResultData as CallResultData | null)?.cost_usd ?? 0)
      .reduce((a, b) => a + b, 0);

    const turnCounts = allMetrics.map((m) => m.turn_count);
    const totalEchoSuppressions = allMetrics.reduce((s, m) => s + m.echo_suppressions, 0);
    const totalEchoLoops = allMetrics.reduce((s, m) => s + m.echo_loops_detected, 0);
    const totalVadFalseTriggers = allMetrics.reduce((s, m) => s + m.vad_false_triggers, 0);
    const totalHallucinationsBlocked = allMetrics.reduce((s, m) => s + m.hallucinations_blocked, 0);
    const callCount = validCalls.length;

    const modes = ['voice_to_voice', 'text_to_voice', 'full_agent'];
    const byMode: Record<
      string,
      { call_count: number; avg_session_a_ms: number; avg_session_b_ms: number; avg_turns: number }
    > = {};

    for (const m of modes) {
      const modeCalls = validCalls.filter((c) => c.communicationMode === m);
      if (modeCalls.length === 0) continue;
      const modeMetrics = modeCalls.map((c) => (c.callResultData as CallResultData).metrics!);
      const modeALatencies = modeMetrics.flatMap((mm) => mm.session_a_latencies_ms);
      const modeBLatencies = modeMetrics.flatMap((mm) => mm.session_b_e2e_latencies_ms);
      const modeTurns = modeMetrics.map((mm) => mm.turn_count);
      byMode[m] = {
        call_count: modeCalls.length,
        avg_session_a_ms:
          modeALatencies.length > 0
            ? Math.round(modeALatencies.reduce((a, b) => a + b, 0) / modeALatencies.length)
            : 0,
        avg_session_b_ms:
          modeBLatencies.length > 0
            ? Math.round(modeBLatencies.reduce((a, b) => a + b, 0) / modeBLatencies.length)
            : 0,
        avg_turns:
          modeTurns.length > 0
            ? Math.round((modeTurns.reduce((a, b) => a + b, 0) / modeTurns.length) * 10) / 10
            : 0,
      };
    }

    return NextResponse.json({
      call_count: callCount,
      total_calls_queried: rows.length,
      session_a_latency: stats(allSessionALatencies),
      session_b_e2e_latency: stats(allSessionBE2ELatencies),
      session_b_stt_latency: stats(allSessionBSTTLatencies),
      first_message_latency: stats(allFirstMessageLatencies),
      turns: stats(turnCounts),
      duration: { total_minutes: Math.round(totalDurationMin * 10) / 10, ...stats(durations) },
      echo: {
        total_suppressions: totalEchoSuppressions,
        total_loops: totalEchoLoops,
        avg_suppressions_per_call:
          callCount > 0 ? Math.round((totalEchoSuppressions / callCount) * 10) / 10 : 0,
        avg_loops_per_call:
          callCount > 0 ? Math.round((totalEchoLoops / callCount) * 10) / 10 : 0,
      },
      vad: {
        total_false_triggers: totalVadFalseTriggers,
        avg_per_call:
          callCount > 0 ? Math.round((totalVadFalseTriggers / callCount) * 10) / 10 : 0,
      },
      hallucinations: {
        total_blocked: totalHallucinationsBlocked,
        avg_per_call:
          callCount > 0 ? Math.round((totalHallucinationsBlocked / callCount) * 10) / 10 : 0,
      },
      cost: {
        total_tokens: totalTokens,
        total_usd: Math.round(totalCostUsd * 1000) / 1000,
        avg_per_call:
          callCount > 0 ? Math.round((totalCostUsd / callCount) * 1000) / 1000 : 0,
        avg_per_minute:
          totalDurationMin > 0
            ? Math.round((totalCostUsd / totalDurationMin) * 1000) / 1000
            : 0,
      },
      by_mode: byMode,
    });
  } catch (error) {
    const authResp = authErrorResponse(error);
    if (authResp) return authResp;
    console.error('Failed to aggregate metrics:', error);
    return NextResponse.json({ error: 'Failed to aggregate metrics' }, { status: 500 });
  }
}
