'use client';

// MonitorStageFunnel — ACTIVITY panel: the B·RECV filter stages where caller audio
// can be dropped. Each stage lists its internal steps with a short explanation, so a
// booth observer can read what the stage does. The ⊘ DROP / ✓ PASS mark on a step is
// shown ONLY while that step is actually happening (live) — idle = quiet gray text, so
// the marks stay meaningful. Footer = overall outcome. No store/relay changes.

import { useEffect, useState } from 'react';
import { useMonitorStore, type PipeStageKey } from '@/hooks/useMonitorStore';
import { ShieldCheck, Activity, Volume2, FileText } from 'lucide-react';

const DECAY_MS = 1800;

type StageState = 'drop' | 'pass' | 'idle';
type IconType = typeof ShieldCheck;

interface SubStep {
  n: number;
  label: string;
  desc: string;
  active: boolean;
  kind?: 'drop' | 'pass' | 'bargein'; // outcome branch — only marked when live-active
}

interface Stage {
  key: PipeStageKey;
  label: string;
  Icon: IconType;
  desc: string;
  state: StageState;
  substeps: SubStep[];
}

const CIRCLED = ['①', '②', '③'];

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

  // Echo Gate internal step from status: active=①, block=②, bargein=③
  const ehNode = pipeline.b.echo_gate;
  const ehStep = isHot(ehNode.at)
    ? ehNode.status === 'bargein'
      ? 3
      : ehNode.status === 'block'
        ? 2
        : ehNode.status === 'active'
          ? 1
          : 0
    : 0;

  // Silero VAD: speech 감지 vs barge-in (drop 없음 — 침묵은 신호 부재일 뿐)
  const vadNode = pipeline.b.silero_vad;
  const vadBargein = isHot(vadNode.at) && vadNode.status === 'bargein';
  const vadSpeech = isHot(vadNode.at) && !vadBargein;

  // Binary stage (drop branch ① / pass branch ②), active branch from bState
  const twoStep = (key: PipeStageKey, dropLabel: string, dropDesc: string, passLabel: string, passDesc: string): SubStep[] => {
    const st = bState(key);
    return [
      { n: 1, label: dropLabel, desc: dropDesc, active: st === 'drop', kind: 'drop' },
      { n: 2, label: passLabel, desc: passDesc, active: st === 'pass', kind: 'pass' },
    ];
  };

  const STAGES: Stage[] = [
    {
      key: 'echo_gate',
      label: 'Echo Gate',
      Icon: ShieldCheck,
      desc: 'Block bot voice echo',
      state: bState('echo_gate'),
      substeps: [
        { n: 1, label: 'Window active', desc: 'inject silence while the bot speaks', active: ehStep === 1 },
        { n: 2, label: 'Echo absorbed', desc: 'first return frame = PSTN echo', active: ehStep === 2, kind: 'drop' },
        { n: 3, label: 'Breakthrough', desc: 'louder/continued audio = real speech', active: ehStep === 3, kind: 'pass' },
      ],
    },
    {
      key: 'energy_gate',
      label: 'Energy',
      Icon: Activity,
      desc: 'Filter low-energy noise',
      state: bState('energy_gate'),
      substeps: twoStep('energy_gate', 'Low energy', 'background noise, below threshold', 'Voice energy', 'above threshold → passes'),
    },
    {
      key: 'silero_vad',
      label: 'Silero VAD',
      Icon: Volume2,
      desc: 'Detect speech segments',
      state: bState('silero_vad'),
      // VAD는 content를 drop하지 않음 — 실제 라이브 신호는 speech 감지 / barge-in(끼어듦)
      substeps: [
        { n: 1, label: 'Speech', desc: 'voice segment detected', active: vadSpeech, kind: 'pass' },
        { n: 2, label: 'Barge-in', desc: 'recipient cuts in while the bot speaks', active: vadBargein, kind: 'bargein' },
      ],
    },
    {
      key: 'stt',
      label: 'STT',
      Icon: FileText,
      desc: 'Transcribe · filter hallucination',
      state: bState('stt'),
      substeps: twoStep('stt', 'Hallucination', 'blocklist / garbage transcript', 'Transcript', 'real words recognized → passes'),
    },
  ];

  const passed = isHot(pipeline.b.translate_b.at) && pipeline.b.translate_b.status !== 'block';
  const dropStage = STAGES.find((s) => s.state === 'drop');
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

      <ul className="flex flex-col gap-2.5">
        {STAGES.map((s) => {
          const live = s.state !== 'idle';
          const iconCls = s.state === 'drop' ? 'text-red-300' : s.state === 'pass' ? 'text-emerald-300' : 'text-slate-500';
          return (
            <li key={s.key} className="flex flex-col">
              <div className="flex items-center gap-3">
                <s.Icon className={`size-4 shrink-0 ${iconCls}`} />
                <span className={`text-sm font-semibold ${live ? 'text-slate-100' : 'text-slate-300'}`}>{s.label}</span>
                <span className="text-xs font-normal text-slate-500">{s.desc}</span>
              </div>
              <ul className="ml-7 mt-1 flex flex-col gap-0.5 border-l border-slate-700/60 pl-3">
                {s.substeps.map((ss) => (
                  <li
                    key={ss.n}
                    className={`flex items-baseline gap-2 rounded px-1.5 py-0.5 text-xs transition-colors duration-300 ${
                      ss.active ? 'bg-amber-400/15' : ''
                    }`}
                  >
                    <span className={`shrink-0 font-bold ${ss.active ? 'text-amber-300' : 'text-slate-600'}`}>
                      {CIRCLED[ss.n - 1]}
                    </span>
                    <span className={`shrink-0 font-semibold ${ss.active ? 'text-amber-100' : 'text-slate-400'}`}>{ss.label}</span>
                    <span className="flex-1 truncate text-slate-500">— {ss.desc}</span>
                    {/* DROP/PASS는 지금 그 단계가 실제로 일어날 때만 */}
                    {ss.active && ss.kind && (
                      <span
                        className={`shrink-0 font-bold ${
                          ss.kind === 'drop' ? 'text-red-300' : ss.kind === 'bargein' ? 'text-amber-300' : 'text-emerald-300'
                        }`}
                      >
                        {ss.kind === 'drop' ? '⊘ DROP' : ss.kind === 'bargein' ? '⚡ BARGE-IN' : '✓ PASS'}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
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
