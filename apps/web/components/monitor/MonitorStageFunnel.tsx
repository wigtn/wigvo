'use client';

// MonitorStageFunnel — ACTIVITY 패널: B·RECV 필터 파이프라인을 세로 퍼널로.
// 마지막 수신 오디오가 각 스테이지를 통과(✓)했는지 / 어디서 걸려 drop(⊘)됐는지 직관 표시.
// 끝에 결과: 끝까지 통과 → PASS(전달됨), 중간에 걸림 → DROPPED at <스테이지>.
// pipeline.b 상태에서 도출 — 스토어/relay 변경 없음. (decay 재계산용 가벼운 ticker)

import { useEffect, useState } from 'react';
import { useMonitorStore, type PipeStageKey } from '@/hooks/useMonitorStore';
import { ShieldCheck, Activity, Volume2, FileText, Languages } from 'lucide-react';

const DECAY_MS = 1800;

interface StageRow {
  key: PipeStageKey;
  label: string;
  Icon: typeof ShieldCheck;
  desc: string; // what this stage filters (rendered as secondary label)
}

// In B·RECV order — audio flows top to bottom
const STAGES: StageRow[] = [
  { key: 'echo_gate', label: 'Echo Gate', Icon: ShieldCheck, desc: 'Blocks bot voice echo' },
  { key: 'energy_gate', label: 'Energy', Icon: Activity, desc: 'Filters low-energy noise' },
  { key: 'silero_vad', label: 'Silero VAD', Icon: Volume2, desc: 'Detects speech segments' },
  { key: 'stt', label: 'STT', Icon: FileText, desc: 'Speech-to-text · filters hallucination' },
  { key: 'translate_b', label: 'Translate', Icon: Languages, desc: 'Translate → deliver to caller' },
];

type StageState = 'drop' | 'pass' | 'idle';

export default function MonitorStageFunnel() {
  const pipeline = useMonitorStore((s) => s.pipeline);

  const [now, setNow] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(id);
  }, []);

  const isHot = (at: number) => at > 0 && now - at < DECAY_MS;

  const stageState = (key: PipeStageKey): StageState => {
    const node = pipeline.b[key];
    if (!isHot(node.at)) return 'idle';
    return node.status === 'block' ? 'drop' : 'pass'; // active/pass/done/bargein → pass
  };

  // 결과: translate까지 통과 → PASS, block 스테이지 있으면 그 스테이지에서 DROPPED
  const passed = isHot(pipeline.b.translate_b.at) && pipeline.b.translate_b.status !== 'block';
  const dropStage = STAGES.find((s) => stageState(s.key) === 'drop');
  const outcome: { kind: 'pass' | 'drop'; label: string } | null = passed
    ? { kind: 'pass', label: '✓ PASS · delivered to caller' }
    : dropStage
      ? { kind: 'drop', label: `⊘ DROPPED at ${dropStage.label}` }
      : null;

  return (
    <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-5 py-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-widest text-slate-300">ACTIVITY</span>
        <span className="text-xs text-slate-500">B·RECV filter pipeline</span>
      </div>

      <ul className="flex flex-col gap-1">
        {STAGES.map((s) => {
          const st = stageState(s.key);
          return (
            <li
              key={s.key}
              className={`flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors duration-300 ${
                st === 'drop' ? 'bg-red-500/10' : st === 'pass' ? 'bg-emerald-500/5' : ''
              }`}
            >
              <s.Icon
                className={`size-4 shrink-0 ${
                  st === 'drop' ? 'text-red-300' : st === 'pass' ? 'text-emerald-300' : 'text-slate-600'
                }`}
              />
              <span className={`flex-1 text-sm font-medium ${st === 'idle' ? 'text-slate-600' : 'text-slate-200'}`}>
                {s.label}
                <span className="ml-2 text-xs font-normal text-slate-500">{s.desc}</span>
              </span>
              <span
                className={`shrink-0 text-sm font-bold tabular-nums ${
                  st === 'drop' ? 'text-red-300' : st === 'pass' ? 'text-emerald-300' : 'text-slate-700'
                }`}
              >
                {st === 'drop' ? '⊘ DROP' : st === 'pass' ? '✓' : '·'}
              </span>
            </li>
          );
        })}
      </ul>

      <div
        className={`mt-3 rounded-lg border px-3 py-2 text-center text-sm font-bold transition-colors duration-300 ${
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
