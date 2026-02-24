// =============================================================================
// GET /api/metrics - 통화 메트릭 집계 API (논문 Table용)
// =============================================================================

import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';

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

interface CallRow {
  call_result_data: { metrics?: CallMetricsRow; cost_usd?: number } | null;
  duration_s: number | null;
  total_tokens: number | null;
  communication_mode: string | null;
  status: string;
  created_at: string;
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
    avg: Math.round(arr.reduce((a, b) => a + b, 0) / arr.length * 10) / 10,
    std: Math.round(std(arr) * 10) / 10,
    min: Math.round(Math.min(...arr) * 10) / 10,
    max: Math.round(Math.max(...arr) * 10) / 10,
    count: arr.length,
  };
}

// Cost: relay server가 call_result_data.cost_usd에 정확한 비용을 저장함
// (CostTokens.cost_usd — per-category token pricing 기반)

export async function GET(request: NextRequest) {
  try {
    const supabase = await createClient();
    const { data: { user }, error: authError } = await supabase.auth.getUser();

    if (authError || !user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const searchParams = request.nextUrl.searchParams;
    const mode = searchParams.get('mode');
    const limit = Math.min(parseInt(searchParams.get('limit') || '100', 10), 500);

    let query = supabase
      .from('calls')
      .select('call_result_data, duration_s, total_tokens, communication_mode, status, created_at')
      .eq('status', 'COMPLETED')
      .order('created_at', { ascending: false })
      .limit(limit);

    if (mode) {
      query = query.eq('communication_mode', mode);
    }

    const { data: calls, error } = await query;

    if (error) {
      console.error('Failed to fetch metrics:', error);
      return NextResponse.json({ error: 'Failed to fetch metrics' }, { status: 500 });
    }

    // Filter calls with valid metrics
    const validCalls = (calls as CallRow[] || []).filter(
      (c) => c.call_result_data?.metrics
    );

    const allMetrics = validCalls.map((c) => c.call_result_data!.metrics!);

    // Flatten latency arrays
    const allSessionALatencies = allMetrics.flatMap((m) => m.session_a_latencies_ms);
    const allSessionBE2ELatencies = allMetrics.flatMap((m) => m.session_b_e2e_latencies_ms);
    const allSessionBSTTLatencies = allMetrics.flatMap((m) => m.session_b_stt_latencies_ms);
    const allFirstMessageLatencies = allMetrics
      .map((m) => m.first_message_latency_ms)
      .filter((v) => v > 0);

    // Durations
    const durations = validCalls
      .map((c) => c.duration_s)
      .filter((v): v is number => v != null && v > 0);
    const totalDurationMin = durations.reduce((a, b) => a + b, 0) / 60;

    // Tokens & Cost (cost_usd from relay server — exact per-category pricing)
    const totalTokens = validCalls
      .map((c) => c.total_tokens)
      .filter((v): v is number => v != null)
      .reduce((a, b) => a + b, 0);
    const totalCostUsd = validCalls
      .map((c) => c.call_result_data?.cost_usd ?? 0)
      .reduce((a, b) => a + b, 0);

    // Turn counts
    const turnCounts = allMetrics.map((m) => m.turn_count);

    // Echo / VAD / Hallucinations
    const totalEchoSuppressions = allMetrics.reduce((s, m) => s + m.echo_suppressions, 0);
    const totalEchoLoops = allMetrics.reduce((s, m) => s + m.echo_loops_detected, 0);
    const totalVadFalseTriggers = allMetrics.reduce((s, m) => s + m.vad_false_triggers, 0);
    const totalHallucinationsBlocked = allMetrics.reduce((s, m) => s + m.hallucinations_blocked, 0);

    const callCount = validCalls.length;

    // By mode breakdown
    const modes = ['voice_to_voice', 'text_to_voice', 'full_agent'];
    const byMode: Record<string, { call_count: number; avg_session_a_ms: number; avg_session_b_ms: number; avg_turns: number }> = {};

    for (const m of modes) {
      const modeCalls = validCalls.filter((c) => c.communication_mode === m);
      const modeMetrics = modeCalls.map((c) => c.call_result_data!.metrics!);
      if (modeMetrics.length === 0) continue;

      const modeALatencies = modeMetrics.flatMap((mm) => mm.session_a_latencies_ms);
      const modeBLatencies = modeMetrics.flatMap((mm) => mm.session_b_e2e_latencies_ms);
      const modeTurns = modeMetrics.map((mm) => mm.turn_count);

      byMode[m] = {
        call_count: modeCalls.length,
        avg_session_a_ms: modeALatencies.length > 0
          ? Math.round(modeALatencies.reduce((a, b) => a + b, 0) / modeALatencies.length)
          : 0,
        avg_session_b_ms: modeBLatencies.length > 0
          ? Math.round(modeBLatencies.reduce((a, b) => a + b, 0) / modeBLatencies.length)
          : 0,
        avg_turns: modeTurns.length > 0
          ? Math.round(modeTurns.reduce((a, b) => a + b, 0) / modeTurns.length * 10) / 10
          : 0,
      };
    }

    return NextResponse.json({
      call_count: callCount,
      total_calls_queried: (calls || []).length,
      session_a_latency: stats(allSessionALatencies),
      session_b_e2e_latency: stats(allSessionBE2ELatencies),
      session_b_stt_latency: stats(allSessionBSTTLatencies),
      first_message_latency: stats(allFirstMessageLatencies),
      turns: stats(turnCounts),
      duration: {
        total_minutes: Math.round(totalDurationMin * 10) / 10,
        ...stats(durations),
      },
      echo: {
        total_suppressions: totalEchoSuppressions,
        total_loops: totalEchoLoops,
        avg_suppressions_per_call: callCount > 0 ? Math.round(totalEchoSuppressions / callCount * 10) / 10 : 0,
        avg_loops_per_call: callCount > 0 ? Math.round(totalEchoLoops / callCount * 10) / 10 : 0,
      },
      vad: {
        total_false_triggers: totalVadFalseTriggers,
        avg_per_call: callCount > 0 ? Math.round(totalVadFalseTriggers / callCount * 10) / 10 : 0,
      },
      hallucinations: {
        total_blocked: totalHallucinationsBlocked,
        avg_per_call: callCount > 0 ? Math.round(totalHallucinationsBlocked / callCount * 10) / 10 : 0,
      },
      cost: {
        total_tokens: totalTokens,
        total_usd: Math.round(totalCostUsd * 1000) / 1000,
        avg_per_call: callCount > 0 ? Math.round(totalCostUsd / callCount * 1000) / 1000 : 0,
        avg_per_minute: totalDurationMin > 0 ? Math.round(totalCostUsd / totalDurationMin * 1000) / 1000 : 0,
      },
      by_mode: byMode,
    });
  } catch (error) {
    console.error('Failed to aggregate metrics:', error);
    return NextResponse.json({ error: 'Failed to aggregate metrics' }, { status: 500 });
  }
}
