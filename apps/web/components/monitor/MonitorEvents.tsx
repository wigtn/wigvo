'use client';

// MonitorEvents — 부스용 "활동" 패널: 에코/차단 카운터 + 결정적 순간 피드.
// 라이브 통화 중에만 의미가 있음 (이벤트는 저장 안 됨). 절제된 다크 스타일.

import { useMonitorStore, type MonitorEventKind } from '@/hooks/useMonitorStore';
import { ShieldCheck, Zap, AlertTriangle, Activity } from 'lucide-react';

const KIND: Record<MonitorEventKind, { Icon: typeof ShieldCheck; text: string; dot: string }> = {
  echo: { Icon: ShieldCheck, text: 'text-orange-200', dot: 'bg-orange-400' },
  bargein: { Icon: Zap, text: 'text-amber-200', dot: 'bg-amber-400' },
  guard: { Icon: AlertTriangle, text: 'text-red-200', dot: 'bg-red-400' },
  info: { Icon: Activity, text: 'text-slate-300', dot: 'bg-slate-500' },
};

function Counter({ label, value, tone }: { label: string; value: number; tone: 'orange' | 'red' }) {
  const cls = tone === 'orange' ? 'border-orange-400/40 text-orange-200' : 'border-red-400/40 text-red-200';
  return (
    <span className={`flex items-center gap-1.5 rounded-full border bg-slate-800/50 px-3 py-1 text-sm ${cls}`}>
      <span className="font-bold tabular-nums">{value}</span>
      <span className="text-slate-400">{label}</span>
    </span>
  );
}

export default function MonitorEvents() {
  const events = useMonitorStore((s) => s.events);
  const echoBlocked = useMonitorStore((s) => s.echoBlocked);
  const guardBlocked = useMonitorStore((s) => s.guardBlocked);

  const recent = [...events].slice(-8).reverse(); // 최신 위로

  return (
    <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-5 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-widest text-slate-300">ACTIVITY</span>
        <div className="flex gap-2">
          <Counter label="echoes" value={echoBlocked} tone="orange" />
          <Counter label="blocked" value={guardBlocked} tone="red" />
        </div>
      </div>

      {recent.length === 0 ? (
        <p className="py-2 text-sm text-slate-600">No events yet</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {recent.map((e, i) => {
            const k = KIND[e.kind];
            return (
              <li
                key={e.id}
                className={`flex items-center gap-3 rounded-lg px-2 py-1.5 ${
                  i === 0 ? 'bg-slate-800/40' : ''
                }`}
              >
                <span className={`size-2 shrink-0 rounded-full ${k.dot}`} />
                <k.Icon className={`size-4 shrink-0 ${k.text}`} />
                <span className="text-base font-medium text-slate-200">{e.label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
