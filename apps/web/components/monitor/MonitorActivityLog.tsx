'use client';

// MonitorActivityLog — ACTIVITY panel: an accumulating, scrollable log of every decisive
// DROP / PASS / BARGE-IN the B·RECV filter pipeline makes during the call. Unlike the old
// live funnel (marks vanished after ~1.8s), each decision stays as a timestamped line for
// the whole session so a booth observer can scroll back through what happened. Fed by
// useMonitorStore.activityLog (appended at signalB decision points). No relay changes.

import { useMemo } from 'react';
import { useMonitorStore, type PipeStageKey, type ActivityKind } from '@/hooks/useMonitorStore';
import { ShieldCheck, Activity, Volume2, FileText, Languages } from 'lucide-react';

const STAGE_LABEL: Record<PipeStageKey, string> = {
  echo_gate: 'Echo Gate',
  energy_gate: 'Energy',
  silero_vad: 'Silero VAD',
  stt: 'STT',
  translate_b: 'Translate',
};

const STAGE_ICON: Record<PipeStageKey, typeof ShieldCheck> = {
  echo_gate: ShieldCheck,
  energy_gate: Activity,
  silero_vad: Volume2,
  stt: FileText,
  translate_b: Languages,
};

const KIND: Record<ActivityKind, { badge: string; cls: string }> = {
  drop: { badge: '⊘ DROP', cls: 'text-red-300' },
  pass: { badge: '✓ PASS', cls: 'text-emerald-300' },
  bargein: { badge: '⚡ BARGE-IN', cls: 'text-amber-300' },
};

function hms(at: number): string {
  const d = new Date(at);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export default function MonitorActivityLog() {
  const log = useMonitorStore((s) => s.activityLog);
  const rows = useMemo(() => [...log].reverse(), [log]); // 최신이 위로

  return (
    <div className="flex min-h-0 flex-col rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-5 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-widest text-slate-300">ACTIVITY</span>
        <span className="text-xs text-slate-500">filter log — every drop / pass this call ({log.length})</span>
      </div>

      {rows.length === 0 ? (
        <p className="py-2 text-sm text-slate-600">No activity yet — waiting for audio</p>
      ) : (
        <ul aria-live="polite" className="flex max-h-72 flex-col gap-0.5 overflow-y-auto pr-1">
          {rows.map((e) => {
            const Icon = STAGE_ICON[e.stage];
            const k = KIND[e.kind];
            return (
              <li
                key={e.id}
                className="flex items-center gap-2 rounded px-1.5 py-1 text-xs transition-colors hover:bg-slate-800/40"
              >
                <span className="shrink-0 font-mono tabular-nums text-slate-500">{hms(e.at)}</span>
                <Icon className={`size-3.5 shrink-0 ${k.cls}`} />
                <span className="shrink-0 font-semibold text-slate-300">{STAGE_LABEL[e.stage]}</span>
                <span className={`shrink-0 font-bold ${k.cls}`}>{k.badge}</span>
                {e.detail && <span className="flex-1 truncate text-slate-500">— {e.detail}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
