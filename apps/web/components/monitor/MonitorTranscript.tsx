'use client';

// MonitorTranscript — 부스 관전 자막 패널.
// store captions에서 번역문(stage !== 1)만 표시 (ChatContainer와 동일 정책).
// 발신자(user)=우측, 수신자(recipient)/AI=좌측. 신규 자막 시 자동 스크롤.
// CaptionMessage는 라이트 테마 하드코딩(PRD M4)이라 재사용 대신 다크 대형 버블을 직접 렌더.

import { useEffect, useMemo, useRef } from 'react';
import { useMonitorStore } from '@/hooks/useMonitorStore';
import type { CaptionEntry } from '@/shared/call-types';
import { cn } from '@/lib/utils';

function Bubble({ entry }: { entry: CaptionEntry }) {
  const isUser = entry.speaker === 'user';
  const speakerLabel = isUser ? 'Caller' : entry.speaker === 'ai' ? 'AI' : 'Callee';

  return (
    <div className={cn('flex w-full mb-4', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[80%] rounded-2xl px-5 py-3.5 leading-relaxed',
          isUser
            ? 'bg-teal-500/20 border border-teal-400/40 text-teal-50 rounded-br-md'
            : 'bg-slate-800/70 border border-slate-600/50 text-slate-100 rounded-bl-md',
        )}
      >
        <div
          className={cn(
            'text-xs font-semibold mb-1.5 uppercase tracking-widest',
            isUser ? 'text-teal-300/80' : 'text-slate-400',
          )}
        >
          {speakerLabel}
        </div>
        <p className="text-xl font-medium">{entry.text}</p>
        {entry.originalText && <p className="mt-1.5 text-sm text-slate-400 italic">{entry.originalText}</p>}
      </div>
    </div>
  );
}

export default function MonitorTranscript() {
  const captions = useMonitorStore((s) => s.captions);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const shown = useMemo(() => captions.filter((c) => c.stage !== 1), [captions]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length]);

  return (
    <div className="flex h-full flex-col rounded-2xl border border-[#1E293B] bg-[#0B1220]/80">
      <div className="shrink-0 border-b border-[#1E293B] px-6 py-3">
        <span className="text-sm font-semibold tracking-widest text-slate-300">TRANSCRIPT</span>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-5">
        {shown.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-lg text-slate-600">Waiting for conversation…</p>
          </div>
        ) : (
          shown.map((entry) => <Bubble key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
}
