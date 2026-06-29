'use client';

// =============================================================================
// MonitorPipeline — 부스 관전용 실시간 파이프라인 시각화
// =============================================================================
// 93c0142의 LivePipelineMonitor를 복원하되 (1) useMonitorStore에서 읽고
// (2) 부스 거리 가독성을 위해 폰트/아이콘/간격을 키운 변형.
// Session A(발신자→수신자, fast) + Session B(수신자→발신자, Echo→Energy→VAD→STT→Translate).
// =============================================================================

import { useEffect, useState } from 'react';
import { Mic, Phone, Languages, ShieldCheck, Activity, Volume2, FileText } from 'lucide-react';
import {
  useMonitorStore,
  type APhase,
  type PipeNode,
  type PipeStageKey,
  type PipeStatus,
} from '@/hooks/useMonitorStore';

// 단계 갱신 후 이 시간(ms) 안이면 "불 켜짐(hot)"
const DECAY_MS = 1800;

type Tone = 'orange' | 'cyan' | 'violet' | 'blue' | 'emerald';

// Tailwind 정적 클래스 (purge 대비 — 동적 조합 금지)
const TONE_ON: Record<Tone, string> = {
  orange: 'border-orange-400 bg-orange-400/15 shadow-[0_0_24px_rgba(251,146,60,0.5)] text-orange-200',
  cyan: 'border-cyan-400 bg-cyan-400/15 shadow-[0_0_24px_rgba(34,211,238,0.5)] text-cyan-200',
  violet: 'border-violet-400 bg-violet-400/15 shadow-[0_0_24px_rgba(167,139,250,0.5)] text-violet-200',
  blue: 'border-blue-400 bg-blue-400/15 shadow-[0_0_24px_rgba(96,165,250,0.5)] text-blue-200',
  emerald: 'border-emerald-400 bg-emerald-400/15 shadow-[0_0_24px_rgba(52,211,153,0.5)] text-emerald-200',
};
const IDLE_CLS = 'border-slate-700 bg-slate-800/40 text-slate-500';
const BLOCK_CLS = 'border-red-500 bg-red-500/15 shadow-[0_0_24px_rgba(248,113,113,0.55)] text-red-200';
const BARGEIN_CLS = 'border-amber-400 bg-amber-400/20 shadow-[0_0_26px_rgba(251,191,36,0.65)] text-amber-100';

function nodeClass(tone: Tone, status: PipeStatus, hot: boolean): string {
  if (!hot || status === 'idle') return IDLE_CLS;
  if (status === 'block') return BLOCK_CLS;
  if (status === 'bargein') return BARGEIN_CLS;
  return TONE_ON[tone];
}

interface StageDef {
  key: PipeStageKey;
  label: string;
  tone: Tone;
  Icon: typeof Mic;
}

const SESSION_B_STAGES: StageDef[] = [
  { key: 'echo_gate', label: 'Echo Gate', tone: 'orange', Icon: ShieldCheck },
  { key: 'energy_gate', label: 'Energy', tone: 'cyan', Icon: Activity },
  { key: 'silero_vad', label: 'Silero VAD', tone: 'violet', Icon: Volume2 },
  { key: 'stt', label: 'STT', tone: 'blue', Icon: FileText },
  { key: 'translate_b', label: 'Translate', tone: 'emerald', Icon: Languages },
];

function Arrow({ active }: { active: boolean }) {
  return (
    <div className="flex items-center shrink-0" aria-hidden>
      <div className={`h-[2px] w-2.5 rounded transition-colors duration-200 ${active ? 'bg-teal-400 animate-pulse' : 'bg-slate-700'}`} />
      <div className={`-ml-1 text-[10px] leading-none transition-colors duration-200 ${active ? 'text-teal-400' : 'text-slate-700'}`}>▶</div>
    </div>
  );
}

function StagePill({
  label,
  detail,
  tone,
  status,
  hot,
  head,
  Icon,
}: {
  label: string;
  detail: string;
  tone: Tone;
  status: PipeStatus;
  hot: boolean;
  head: boolean;
  Icon: typeof Mic;
}) {
  return (
    <div
      className={`relative flex w-[72px] flex-col items-center justify-center rounded-lg border px-1 py-1.5 transition-all duration-500 ease-out ${nodeClass(
        tone,
        status,
        hot,
      )} ${head ? 'ring-2 ring-white/70 scale-105' : ''}`}
    >
      <Icon className="size-4 mb-0.5" />
      <span className="text-xs font-semibold leading-tight whitespace-nowrap">{label}</span>
      <span className="w-full truncate text-center text-[10px] leading-tight opacity-80 h-3">{hot ? detail : ''}</span>
    </div>
  );
}

function EndPoint({ label, Icon, active }: { label: string; Icon: typeof Mic; active: boolean }) {
  return (
    <div
      className={`flex w-14 flex-col items-center justify-center rounded-lg border px-1 py-1.5 transition-all duration-500 ease-out ${
        active
          ? 'border-teal-400 bg-teal-400/15 text-teal-200 shadow-[0_0_22px_rgba(45,212,191,0.45)]'
          : 'border-slate-700 bg-slate-800/40 text-slate-400'
      }`}
    >
      <Icon className="size-4 mb-0.5" />
      <span className="text-xs font-semibold leading-tight whitespace-nowrap">{label}</span>
    </div>
  );
}

function latencyBadge(values: number[] | undefined, fallback: string): string {
  if (values && values.length > 0) {
    return `${Math.round(values[values.length - 1])}ms`;
  }
  return fallback;
}

export default function MonitorPipeline() {
  const pipeline = useMonitorStore((s) => s.pipeline);
  const metrics = useMonitorStore((s) => s.metrics);

  // decay 재계산용 가벼운 ticker
  const [now, setNow] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 80);
    return () => clearInterval(id);
  }, []);

  const isHot = (at: number) => at > 0 && now - at < DECAY_MS;

  // Session B 현재 헤드(가장 최근 갱신 단계)
  let headKey: PipeStageKey | null = null;
  let headAt = 0;
  for (const s of SESSION_B_STAGES) {
    const node = pipeline.b[s.key];
    if (isHot(node.at) && node.at > headAt) {
      headAt = node.at;
      headKey = s.key;
    }
  }

  const aHot = isHot(pipeline.aAt);
  const aPhase: APhase = aHot ? pipeline.aPhase : 'idle';
  const aSpeaking = aHot && (aPhase === 'speaking' || aPhase === 'translating' || aPhase === 'delivered');
  const aTranslating = aHot && (aPhase === 'translating' || aPhase === 'delivered');
  const aDelivered = aHot && aPhase === 'delivered';
  const bAnyHot = SESSION_B_STAGES.some((s) => isHot(pipeline.b[s.key].at));

  return (
    <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-6 py-5">
      <div className="flex items-center justify-between mb-4">
        <span className="text-slate-300 text-sm font-semibold tracking-widest">LIVE PIPELINE</span>
        <span className="text-xs text-slate-500">real-time stage tracing</span>
      </div>

      {/* Session A: Caller → Callee (fast path) */}
      <div className="flex items-center gap-1 overflow-x-auto">
        <span className="text-[10px] text-emerald-400/90 font-bold w-11 shrink-0">A·SEND</span>
        <EndPoint label="Caller" Icon={Mic} active={aSpeaking} />
        <Arrow active={aSpeaking} />
        <StagePill
          label="Translate"
          detail={aTranslating ? 'translating' : ''}
          tone="emerald"
          status={aTranslating ? 'active' : 'idle'}
          hot={aTranslating}
          head={aHot && aPhase === 'translating'}
          Icon={Languages}
        />
        <Arrow active={aDelivered} />
        <EndPoint label="Callee" Icon={Phone} active={aDelivered} />
        <span className="ml-2 text-xs text-emerald-400/80 font-mono shrink-0">
          {latencyBadge(metrics?.session_a_latencies_ms, '~555ms')}
        </span>
      </div>

      {/* Session B: Callee → Caller (3-stage filter + STT + translate) */}
      <div className="flex items-center gap-1 overflow-x-auto mt-3">
        <span className="text-[10px] text-cyan-400/90 font-bold w-11 shrink-0">B·RECV</span>
        <EndPoint label="Callee" Icon={Phone} active={bAnyHot} />
        {SESSION_B_STAGES.map((s) => {
          const node: PipeNode = pipeline.b[s.key];
          const hot = isHot(node.at);
          return (
            <div key={s.key} className="flex items-center gap-0.5 shrink-0">
              <Arrow active={hot} />
              <StagePill
                label={s.label}
                detail={node.detail}
                tone={s.tone}
                status={node.status}
                hot={hot}
                head={headKey === s.key}
                Icon={s.Icon}
              />
            </div>
          );
        })}
        <Arrow active={isHot(pipeline.b.translate_b.at)} />
        <EndPoint label="Caller" Icon={Mic} active={isHot(pipeline.b.translate_b.at)} />
        <span className="ml-2 text-xs text-cyan-400/80 font-mono shrink-0">
          {latencyBadge(metrics?.session_b_e2e_latencies_ms, '~2684ms')}
        </span>
      </div>
    </div>
  );
}
