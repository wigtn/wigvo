'use client';

// MonitorSignals — 부스용 "ACTIVITY" 패널 (고정 카탈로그 버전, /monitor2 전용).
// 추적하는 결정적 신호 5종을 항상 고정 표시하고, 발동된 신호는 강조 + 누적 횟수,
// 미발동은 흐리게(—). 가장 최근 발동 신호는 강조 행으로 표시.
// 시간순 피드(MonitorEvents)와 달리 "무엇을 보고 있고 그중 무엇이 떴는지"를 한눈에.

import { useMonitorStore, type MonitorSignalKey } from '@/hooks/useMonitorStore';
import { ShieldCheck, Zap, Mic, AlertTriangle, ShieldAlert } from 'lucide-react';

interface SignalDef {
  key: MonitorSignalKey;
  label: string;
  Icon: typeof ShieldCheck;
  on: string; // 발동 시 텍스트/아이콘 색
  dot: string; // 발동 시 도트 색
}

// 고정 카탈로그 — useRelayMonitor가 push하는 signal key와 1:1 대응
const CATALOG: SignalDef[] = [
  { key: 'echo_absorbed', label: 'Echo absorbed', Icon: ShieldCheck, on: 'text-orange-200', dot: 'bg-orange-400' },
  { key: 'recipient_interrupted', label: 'Recipient interrupted', Icon: Zap, on: 'text-amber-200', dot: 'bg-amber-400' },
  { key: 'echo_bargein', label: 'Echo gate barge-in', Icon: Mic, on: 'text-amber-200', dot: 'bg-amber-400' },
  { key: 'hallucination', label: 'Hallucination blocked', Icon: AlertTriangle, on: 'text-red-200', dot: 'bg-red-400' },
  { key: 'guardrail', label: 'Guardrail triggered', Icon: ShieldAlert, on: 'text-red-200', dot: 'bg-red-400' },
];

export default function MonitorSignals() {
  const counts = useMonitorStore((s) => s.signalCounts);
  const events = useMonitorStore((s) => s.events);
  const lastSignal = events.length ? events[events.length - 1].signal : undefined;
  const total = CATALOG.reduce((sum, s) => sum + (counts[s.key] ?? 0), 0);

  return (
    <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-5 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-widest text-slate-300">ACTIVITY</span>
        <span className="text-xs text-slate-500">
          {total} events · watching {CATALOG.length} signals
        </span>
      </div>

      <ul className="flex flex-col gap-1.5">
        {CATALOG.map((s) => {
          const n = counts[s.key] ?? 0;
          const fired = n > 0;
          const isLast = s.key === lastSignal;
          return (
            <li
              key={s.key}
              className={`flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors duration-300 ${
                isLast ? 'bg-slate-800/50' : ''
              }`}
            >
              <span
                className={`size-2 shrink-0 rounded-full ${fired ? s.dot : 'bg-slate-700'} ${
                  isLast ? 'animate-pulse' : ''
                }`}
              />
              <s.Icon className={`size-4 shrink-0 ${fired ? s.on : 'text-slate-600'}`} />
              <span className={`flex-1 text-base font-medium ${fired ? 'text-slate-100' : 'text-slate-600'}`}>
                {s.label}
              </span>
              <span className={`shrink-0 font-bold tabular-nums ${fired ? s.on : 'text-slate-700'}`}>
                {fired ? n : '—'}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
