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
  // 관전 화면은 2자(발신자↔수신자) 대화. assistant(ai) 출력은 발신자 측 통역 발화이므로 Caller로 묶는다.
  const isCaller = entry.speaker === 'user' || entry.speaker === 'ai';
  const speakerLabel = isCaller ? 'Caller' : 'Callee';

  // 메인 = 실제로 한 말(원문), 아래 = 번역. 원문이 없으면 가진 텍스트만 표시.
  const spoken = entry.originalText ?? entry.text;
  const translated = entry.originalText ? entry.text : null;

  return (
    <div className={cn('flex w-full mb-2', isCaller ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[85%] rounded-xl px-4 py-2 leading-snug',
          isCaller
            ? 'bg-teal-500/20 border border-teal-400/40 text-teal-50 rounded-br-sm'
            : 'bg-slate-800/70 border border-slate-600/50 text-slate-100 rounded-bl-sm',
        )}
      >
        <div
          className={cn(
            'text-[10px] font-semibold mb-1 uppercase tracking-widest',
            isCaller ? 'text-teal-300/80' : 'text-slate-400',
          )}
        >
          {speakerLabel}
        </div>
        <p className="text-base font-medium">{spoken}</p>
        {translated && <p className="mt-1 text-sm text-slate-400 italic">{translated}</p>}
      </div>
    </div>
  );
}

// AI 시스템 발화(자기소개 고지·타이핑 필러)는 실제 대화가 아니므로 관전 화면에서 숨긴다.
const SYSTEM_SIGNATURES = [
  'AI translation',
  'on behalf of a customer',
  'relay their message',
  'relaying their message',
  'AI 번역',
  '번역 서비스',
  '메시지를 작성',
  '잠시만 기다',
];
const isSystemBoilerplate = (text: string) =>
  SYSTEM_SIGNATURES.some((sig) => text.includes(sig));

export default function MonitorTranscript() {
  const captions = useMonitorStore((s) => s.captions);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const shown = useMemo(
    () => captions.filter((c) => c.stage !== 1 && !isSystemBoilerplate(c.text)),
    [captions],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length]);

  return (
    <div className="flex h-full flex-col rounded-2xl border border-[#1E293B] bg-[#0B1220]/80">
      <div className="shrink-0 border-b border-[#1E293B] px-4 py-2.5">
        <span className="text-sm font-semibold tracking-widest text-slate-300">TRANSCRIPT</span>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3">
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
