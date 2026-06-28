'use client';

// MonitorStatusBar — 부스 관전 상단 배너: 상태 타임라인 + 언어쌍 + 모드 + 통화 시간.
// 타임라인은 store callStatus(idle/connecting/waiting/connected/ended)에 매핑.
// (relay가 주는 상태는 waiting/connected/ended 중심 — Dialing은 connecting 추론 표시)

import { useMonitorStore, type MonitorCallStatus } from '@/hooks/useMonitorStore';

const STEPS: { key: string; label: string }[] = [
  { key: 'dialing', label: 'Dialing' },
  { key: 'ringing', label: 'Ringing' },
  { key: 'connected', label: 'Connected' },
  { key: 'ended', label: 'Ended' },
];

function stepIndex(status: MonitorCallStatus): number {
  switch (status) {
    case 'idle':
    case 'connecting':
      return 0;
    case 'waiting':
      return 1;
    case 'connected':
      return 2;
    case 'ended':
      return 3;
  }
}

const MODE_LABEL: Record<string, string> = {
  voice_to_voice: 'Voice ↔ Voice',
  text_to_voice: 'Text → Voice',
  full_agent: 'AI Agent',
};

function fmtDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function MonitorStatusBar() {
  const callStatus = useMonitorStore((s) => s.callStatus);
  const snapshot = useMonitorStore((s) => s.snapshot);
  const callDuration = useMonitorStore((s) => s.callDuration);

  const active = stepIndex(callStatus);
  const ended = callStatus === 'ended';

  const src = snapshot?.sourceLanguage?.toUpperCase() ?? '--';
  const tgt = snapshot?.targetLanguage?.toUpperCase() ?? '--';
  const mode = snapshot?.communicationMode ? MODE_LABEL[snapshot.communicationMode] : null;

  return (
    <div className="flex items-center justify-between gap-6 rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-6 py-4">
      {/* 타임라인 */}
      <div className="flex items-center gap-3">
        {STEPS.map((step, i) => {
          const done = i < active;
          const current = i === active;
          const isEndedDot = step.key === 'ended' && ended;
          return (
            <div key={step.key} className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <span
                  className={`size-3 rounded-full transition-colors ${
                    isEndedDot
                      ? 'bg-red-400'
                      : current
                        ? 'bg-teal-400 animate-pulse shadow-[0_0_12px_rgba(45,212,191,0.7)]'
                        : done
                          ? 'bg-teal-500'
                          : 'bg-slate-700'
                  }`}
                />
                <span
                  className={`text-base font-semibold ${
                    isEndedDot ? 'text-red-300' : current ? 'text-teal-200' : done ? 'text-slate-300' : 'text-slate-600'
                  }`}
                >
                  {step.label}
                </span>
              </div>
              {i < STEPS.length - 1 && (
                <div className={`h-[2px] w-8 rounded ${i < active ? 'bg-teal-500' : 'bg-slate-700'}`} />
              )}
            </div>
          );
        })}
      </div>

      {/* 언어쌍 + 모드 + 시간 */}
      <div className="flex items-center gap-5 shrink-0">
        <div className="flex items-center gap-2 text-lg font-bold text-slate-100">
          <span>{src}</span>
          <span className="text-teal-400">↔</span>
          <span>{tgt}</span>
        </div>
        {mode && (
          <span className="rounded-full border border-slate-600 bg-slate-800/60 px-3 py-1 text-sm font-medium text-slate-300">
            {mode}
          </span>
        )}
        <span className="font-mono text-lg text-slate-200 tabular-nums">{fmtDuration(callDuration)}</span>
      </div>
    </div>
  );
}
