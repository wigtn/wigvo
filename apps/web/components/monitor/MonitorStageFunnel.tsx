'use client';

// MonitorStageFunnel — ACTIVITY panel: the B·RECV filter stages where caller audio
// can be dropped. One line per stage; a stage shows ✓ (passing) / ⊘ DROP (caught)
// ONLY while it is live, idle stays dim (·). Footer = overall outcome (PASS / DROPPED).
// Only the 4 filter stages are shown (Echo Gate, Energy, Silero VAD, STT); Translate is
// just delivery. Derived from store pipeline state; no store/relay changes.

import { useEffect, useState } from 'react';
import { useMonitorStore, type PipeStageKey } from '@/hooks/useMonitorStore';
import { ShieldCheck, Activity, Volume2, FileText } from 'lucide-react';

const DECAY_MS = 1800;

type StageState = 'drop' | 'pass' | 'idle';
type IconType = typeof ShieldCheck;

// The 4 filter stages where audio can actually be dropped
const FILTER_STAGES: { key: PipeStageKey; label: string; Icon: IconType; desc: string }[] = [
  { key: 'echo_gate', label: 'Echo Gate', Icon: ShieldCheck, desc: 'Block bot voice echo' },
  { key: 'energy_gate', label: 'Energy', Icon: Activity, desc: 'Filter low-energy noise' },
  { key: 'silero_vad', label: 'Silero VAD', Icon: Volume2, desc: 'Detect speech segments' },
  { key: 'stt', label: 'STT', Icon: FileText, desc: 'Transcribe · filter hallucination' },
];

export default function MonitorStageFunnel() {
  const pipeline = useMonitorStore((s) => s.pipeline);

  const [now, setNow] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(id);
  }, []);

  const isHot = (at: number) => at > 0 && now - at < DECAY_MS;

  const bState = (key: PipeStageKey): StageState => {
    const node = pipeline.b[key];
    if (!isHot(node.at)) return 'idle';
    return node.status === 'block' ? 'drop' : 'pass'; // active/pass/done/bargein → pass
  };

  const passed = isHot(pipeline.b.translate_b.at) && pipeline.b.translate_b.status !== 'block';
  const dropStage = FILTER_STAGES.find((s) => bState(s.key) === 'drop');
  const outcome: { kind: 'pass' | 'drop'; label: string } | null = passed
    ? { kind: 'pass', label: '✓ PASS · delivered' }
    : dropStage
      ? { kind: 'drop', label: `⊘ DROPPED at ${dropStage.label}` }
      : null;

  return (
    <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-5 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-widest text-slate-300">ACTIVITY</span>
        <span className="text-xs text-slate-500">filter stages — where audio drops</span>
      </div>

      <ul className="flex flex-col gap-1">
        {FILTER_STAGES.map((s) => {
          const st = bState(s.key);
          const live = st !== 'idle';
          const cls = st === 'drop' ? 'text-red-300' : st === 'pass' ? 'text-emerald-300' : 'text-slate-600';
          return (
            <li
              key={s.key}
              className={`flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors duration-300 ${
                st === 'drop' ? 'bg-red-500/10' : st === 'pass' ? 'bg-emerald-500/5' : ''
              }`}
            >
              <s.Icon className={`size-4 shrink-0 ${cls}`} />
              <span className={`flex-1 text-sm font-medium ${live ? 'text-slate-200' : 'text-slate-500'}`}>
                {s.label}
                <span className="ml-2 text-xs font-normal text-slate-500">{s.desc}</span>
              </span>
              {/* DROP/PASS는 지금 실제로 그 스테이지에서 일어날 때만 — idle은 흐린 · */}
              <span className={`shrink-0 text-sm font-bold tabular-nums ${st === 'drop' ? 'text-red-300' : st === 'pass' ? 'text-emerald-300' : 'text-slate-700'}`}>
                {st === 'drop' ? '⊘ DROP' : st === 'pass' ? '✓' : '·'}
              </span>
            </li>
          );
        })}
      </ul>

      <div
        className={`mt-3 rounded-lg border px-3 py-1.5 text-center text-sm font-bold transition-colors duration-300 ${
          outcome?.kind === 'pass'
            ? 'border-emerald-400/50 bg-emerald-400/10 text-emerald-200'
            : outcome?.kind === 'drop'
              ? 'border-red-400/50 bg-red-400/10 text-red-200'
              : 'border-slate-700 bg-slate-800/30 text-slate-600'
        }`}
      >
        {outcome ? outcome.label : 'idle — waiting for audio'}
      </div>
    </div>
  );
}
