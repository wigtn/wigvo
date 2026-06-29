'use client';

// MonitorStageFunnel — ACTIVITY panel: the full live pipeline as vertical funnels.
// Shows BOTH directions — A·SEND (caller→callee) and B·RECV (callee→caller) —
// with each stage's live state: passed (✓) / dropped (⊘ DROP) / idle (·),
// plus a per-direction outcome. Derived from store pipeline state; no store/relay changes.
// (light ticker re-computes the decay window)

import { useEffect, useState } from 'react';
import { useMonitorStore, type PipeStageKey, type APhase } from '@/hooks/useMonitorStore';
import { ShieldCheck, Activity, Volume2, FileText, Languages } from 'lucide-react';

const DECAY_MS = 1800;

type StageState = 'drop' | 'pass' | 'idle';
type IconType = typeof ShieldCheck;

interface Row {
  label: string;
  Icon: IconType;
  desc: string;
  state: StageState;
}

type Outcome = { kind: 'pass' | 'drop'; label: string } | null;

function FunnelGroup({ title, dir, rows, outcome }: { title: string; dir: string; rows: Row[]; outcome: Outcome }) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-xs font-bold tracking-widest text-slate-300">{title}</span>
        <span className="text-[11px] text-slate-500">{dir}</span>
      </div>
      <ul className="flex flex-col gap-1">
        {rows.map((r) => {
          const iconCls = r.state === 'drop' ? 'text-red-300' : r.state === 'pass' ? 'text-emerald-300' : 'text-slate-600';
          const markCls = r.state === 'drop' ? 'text-red-300' : r.state === 'pass' ? 'text-emerald-300' : 'text-slate-700';
          const bgCls = r.state === 'drop' ? 'bg-red-500/10' : r.state === 'pass' ? 'bg-emerald-500/5' : '';
          return (
            <li
              key={r.label}
              className={`flex items-center gap-3 rounded-lg px-2 py-1 transition-colors duration-300 ${bgCls}`}
            >
              <r.Icon className={`size-4 shrink-0 ${iconCls}`} />
              <span className={`flex-1 text-sm font-medium ${r.state === 'idle' ? 'text-slate-600' : 'text-slate-200'}`}>
                {r.label}
                <span className="ml-2 text-xs font-normal text-slate-500">{r.desc}</span>
              </span>
              <span className={`shrink-0 text-sm font-bold tabular-nums ${markCls}`}>
                {r.state === 'drop' ? '⊘ DROP' : r.state === 'pass' ? '✓' : '·'}
              </span>
            </li>
          );
        })}
      </ul>
      <div
        className={`mt-2 rounded-lg border px-3 py-1.5 text-center text-sm font-bold transition-colors duration-300 ${
          outcome?.kind === 'pass'
            ? 'border-emerald-400/50 bg-emerald-400/10 text-emerald-200'
            : outcome?.kind === 'drop'
              ? 'border-red-400/50 bg-red-400/10 text-red-200'
              : 'border-slate-700 bg-slate-800/30 text-slate-600'
        }`}
      >
        {outcome ? outcome.label : 'idle'}
      </div>
    </div>
  );
}

export default function MonitorStageFunnel() {
  const pipeline = useMonitorStore((s) => s.pipeline);

  const [now, setNow] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(id);
  }, []);

  const isHot = (at: number) => at > 0 && now - at < DECAY_MS;
  const passIf = (active: boolean): StageState => (active ? 'pass' : 'idle');

  // A·SEND (caller → callee): fast path driven by aPhase, no filter gates → pass or idle
  const aPhase: APhase = isHot(pipeline.aAt) ? pipeline.aPhase : 'idle';
  const aRows: Row[] = [
    {
      label: 'STT',
      Icon: FileText,
      desc: 'Recognize caller speech',
      state: passIf(aPhase === 'speaking' || aPhase === 'translating' || aPhase === 'delivered'),
    },
    {
      label: 'Translate',
      Icon: Languages,
      desc: 'Translate for callee',
      state: passIf(aPhase === 'translating' || aPhase === 'delivered'),
    },
    { label: 'TTS', Icon: Volume2, desc: 'Speak → deliver to callee', state: passIf(aPhase === 'delivered') },
  ];
  const aOutcome: Outcome = aPhase === 'delivered' ? { kind: 'pass', label: '✓ delivered to callee' } : null;

  // B·RECV (callee → caller): filter pipeline → pass / drop / idle
  const bState = (key: PipeStageKey): StageState => {
    const node = pipeline.b[key];
    if (!isHot(node.at)) return 'idle';
    return node.status === 'block' ? 'drop' : 'pass'; // active/pass/done/bargein → pass
  };
  const B_STAGES: { key: PipeStageKey; label: string; Icon: IconType; desc: string }[] = [
    { key: 'echo_gate', label: 'Echo Gate', Icon: ShieldCheck, desc: 'Block bot voice echo' },
    { key: 'energy_gate', label: 'Energy', Icon: Activity, desc: 'Filter low-energy noise' },
    { key: 'silero_vad', label: 'Silero VAD', Icon: Volume2, desc: 'Detect speech segments' },
    { key: 'stt', label: 'STT', Icon: FileText, desc: 'Transcribe · filter hallucination' },
    { key: 'translate_b', label: 'Translate', Icon: Languages, desc: 'Translate → deliver to caller' },
  ];
  const bRows: Row[] = B_STAGES.map((s) => ({ label: s.label, Icon: s.Icon, desc: s.desc, state: bState(s.key) }));
  const bPassed = isHot(pipeline.b.translate_b.at) && pipeline.b.translate_b.status !== 'block';
  const bDrop = B_STAGES.find((s) => bState(s.key) === 'drop');
  const bOutcome: Outcome = bPassed
    ? { kind: 'pass', label: '✓ PASS · delivered to caller' }
    : bDrop
      ? { kind: 'drop', label: `⊘ DROPPED at ${bDrop.label}` }
      : null;

  return (
    <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-5 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-widest text-slate-300">ACTIVITY</span>
        <span className="text-xs text-slate-500">live pipeline flow</span>
      </div>
      <div className="flex flex-col gap-4">
        <FunnelGroup title="A·SEND" dir="caller → callee" rows={aRows} outcome={aOutcome} />
        <FunnelGroup title="B·RECV" dir="callee → caller" rows={bRows} outcome={bOutcome} />
      </div>
    </div>
  );
}
