'use client';

// MonitorReplay — 종료된 통화의 "저장된" 관전 뷰.
// 라이브 WS 없이 DB Call 레코드(transcriptBilingual + 메타)를 store에 시드한다.
// → 통화가 끝난 뒤 새로고침/재진입해도 대화 기록이 사라지지 않는다 (라이브 모니터는 이벤트 기반이라 휘발).

import { useEffect } from 'react';
import { useMonitorStore } from '@/hooks/useMonitorStore';
import type { Call } from '@/shared/types';
import type { CaptionEntry } from '@/shared/call-types';

function transcriptToCaptions(call: Call): CaptionEntry[] {
  const entries = call.transcriptBilingual ?? [];
  return entries.map((e, i) => ({
    id: `saved-${i}`,
    speaker: e.role === 'user' ? 'user' : 'recipient',
    text: e.translated_text || e.original_text,
    originalText: e.translated_text ? e.original_text : undefined,
    language: e.language,
    isFinal: true,
    timestamp: e.timestamp,
    stage: 2,
  }));
}

export default function MonitorReplay({ call, children }: { call: Call; children: React.ReactNode }) {
  const syncState = useMonitorStore((s) => s.syncState);
  const reset = useMonitorStore((s) => s.reset);

  useEffect(() => {
    syncState({
      captions: transcriptToCaptions(call),
      callStatus: 'ended',
      callDuration: call.durationS ?? 0,
      snapshot: {
        sourceLanguage: call.sourceLanguage ?? undefined,
        targetLanguage: call.targetLanguage ?? undefined,
        communicationMode: call.communicationMode,
        callMode: call.callMode,
        targetName: call.targetName,
      },
    });
    return () => reset();
  }, [call, syncState, reset]);

  return <>{children}</>;
}
