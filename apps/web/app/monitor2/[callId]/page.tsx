'use client';

// /monitor2/[callId] — /monitor/[callId]의 변형. ACTIVITY 패널을 고정 카탈로그
// (MonitorSignals)로 교체한 실험 관전 화면. 파이프라인/자막/상태바는 동일 공유.

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Loader2, ArrowLeft } from 'lucide-react';
import { getCall } from '@/lib/api';
import { RELAY_WS_URL } from '@/lib/constants';
import type { Call } from '@/shared/types';
import MonitorProvider from '@/components/monitor/MonitorProvider';
import MonitorReplay from '@/components/monitor/MonitorReplay';
import MonitorStatusBar from '@/components/monitor/MonitorStatusBar';
import MonitorPipeline from '@/components/monitor/MonitorPipeline';
import MonitorSignals from '@/components/monitor/MonitorSignals';
import MonitorTranscript from '@/components/monitor/MonitorTranscript';

// 관전 WS URL 도출 (PRD C1):
//   서버가 만든 sender URL(.../stream)을 /monitor로 치환 → scheme/도메인/id 일치 보장.
//   relayWsUrl이 아직 없으면 env + DB call.id 로 조합 (call.callId 컬럼은 비어있으므로 사용 금지).
function deriveMonitorWsUrl(call: Call): string | null {
  if (call.relayWsUrl) return call.relayWsUrl.replace('/stream', '/monitor');
  if (call.id) return `${RELAY_WS_URL}/relay/calls/${call.id}/monitor`;
  return null;
}

export default function Monitor2CallPage() {
  const params = useParams();
  const router = useRouter();
  const callId = params.callId as string;

  const [call, setCall] = useState<Call | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!callId) return;
    getCall(callId)
      .then((data) => setCall(data as unknown as Call))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load call'))
      .finally(() => setLoading(false));
  }, [callId]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#070B14]">
        <Loader2 className="size-8 animate-spin text-slate-500" />
      </div>
    );
  }

  if (error || !call) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-[#070B14] text-slate-300">
        <p className="text-lg">{error ?? 'Call not found'}</p>
        <button
          onClick={() => router.push('/monitor2')}
          className="flex items-center gap-2 rounded-xl border border-slate-600 px-4 py-2 text-sm hover:bg-slate-800"
        >
          <ArrowLeft className="size-4" /> Back to list
        </button>
      </div>
    );
  }

  // 진행 중인 통화만 라이브 관전(WS). 종료된 통화는 DB 저장 기록을 재생(replay).
  const isActive = call.status === 'CALLING' || call.status === 'IN_PROGRESS';

  const body = (
    <div className="flex h-screen flex-col gap-4 bg-[#070B14] p-5 text-slate-100">
      {/* 상단 헤더 + 상태 타임라인 */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push('/monitor2')}
          className="flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:bg-slate-800"
        >
          <ArrowLeft className="size-4" />
        </button>
        <div className="flex-1">
          <MonitorStatusBar />
        </div>
      </div>

      {/* 본문: 진행중이면 좌 파이프라인 + 우 자막 / 종료면 자막 전체폭(저장된 기록) */}
      {isActive ? (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
          <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
            <MonitorPipeline />
            <MonitorSignals />
          </div>
          <div className="min-h-0">
            <MonitorTranscript />
          </div>
        </div>
      ) : (
        <div className="mx-auto min-h-0 w-full max-w-3xl flex-1">
          <MonitorTranscript />
        </div>
      )}
    </div>
  );

  return isActive ? (
    <MonitorProvider wsUrl={deriveMonitorWsUrl(call)}>{body}</MonitorProvider>
  ) : (
    <MonitorReplay call={call}>{body}</MonitorReplay>
  );
}
