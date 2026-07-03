'use client';

// =============================================================================
// MonitorPipeline — 부스 관전용 실시간 파이프라인 시각화
// =============================================================================
// 93c0142의 LivePipelineMonitor를 복원하되 (1) useMonitorStore에서 읽고
// (2) 부스 거리 가독성을 위해 폰트/아이콘/간격을 키운 변형.
// Session A(발신자→수신자, fast) + Session B(수신자→발신자, Echo→Energy→VAD→STT→Translate).
// =============================================================================

import { Fragment, useEffect, useState } from 'react';
import { Mic, Phone, Languages, ShieldCheck, Activity, Volume2, Captions, AudioLines } from 'lucide-react';
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
  { key: 'silero_vad', label: 'Silero VAD', tone: 'violet', Icon: AudioLines },
  { key: 'stt', label: 'STT', tone: 'blue', Icon: Captions },
  { key: 'translate_b', label: 'Translate', tone: 'emerald', Icon: Languages },
];

// grow=true면 커넥터가 늘어나 행이 전체 폭을 채움(B·RECV용). false면 고정 폭 컴팩트(A·SEND용).
function Arrow({ active, grow = false }: { active: boolean; grow?: boolean }) {
  return (
    <div className={`flex items-center ${grow ? 'min-w-[12px] flex-1' : 'shrink-0'}`} aria-hidden>
      <div className={`h-[2px] rounded transition-colors duration-500 ease-out ${grow ? 'flex-1' : 'w-14'} ${active ? 'bg-teal-400 animate-pulse' : 'bg-slate-700'}`} />
      <div className={`-ml-1 text-[11px] leading-none transition-colors duration-500 ease-out ${active ? 'text-teal-400' : 'text-slate-700'}`}>▶</div>
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
  dropped = false,
}: {
  label: string;
  detail: string;
  tone: Tone;
  status: PipeStatus;
  hot: boolean;
  head: boolean;
  Icon: typeof Mic;
  dropped?: boolean;
}) {
  return (
    <div
      className={`relative flex w-[80px] shrink-0 flex-col items-center justify-center rounded-xl border px-1 py-2 transition-all duration-500 ease-out ${nodeClass(
        tone,
        status,
        hot,
      )} ${head ? 'ring-2 ring-white/70 scale-105' : ''}`}
    >
      <Icon className="size-6 mb-0.5" strokeWidth={2} />
      <span className="text-sm font-semibold leading-tight whitespace-nowrap">{label}</span>
      {/* 걸려서 drop된 스테이지는 detail 슬롯에 빨간 DROP 표시 (레이아웃 변화 없음) */}
      <span
        className={`w-full truncate text-center text-[10px] leading-tight h-3.5 ${
          dropped ? 'font-bold text-red-300' : 'opacity-80'
        }`}
      >
        {dropped ? '⊘ DROP' : hot ? detail : ''}
      </span>
    </div>
  );
}

function EndPoint({ label, Icon, active }: { label: string; Icon: typeof Mic; active: boolean }) {
  return (
    <div
      className={`flex w-14 shrink-0 flex-col items-center justify-center rounded-xl border px-1 py-2 transition-all duration-500 ease-out ${
        active
          ? 'border-teal-400 bg-teal-400/15 text-teal-200 shadow-[0_0_22px_rgba(45,212,191,0.45)]'
          : 'border-slate-700 bg-slate-800/40 text-slate-400'
      }`}
    >
      <Icon className="size-6 mb-0.5" strokeWidth={2} />
      <span className="text-sm font-semibold leading-tight whitespace-nowrap">{label}</span>
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
    const id = setInterval(() => setNow(Date.now()), 150);
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

  // B·RECV 결과: translate까지 도달 → PASS, frontier 스테이지가 block(에코 흡수/노이즈 거절) → DROP
  const bOutcome: 'pass' | 'drop' | null = isHot(pipeline.b.translate_b.at)
    ? 'pass'
    : headKey && pipeline.b[headKey].status === 'block'
      ? 'drop'
      : null;

  return (
    <div className="flex shrink-0 flex-col rounded-2xl border border-[#1E293B] bg-[#0B1220]/80 px-6 py-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-slate-300 text-sm font-semibold tracking-widest">LIVE PIPELINE</span>
        <span className="text-xs text-slate-500">real-time stage tracing</span>
      </div>

      {/* 파이프라인 두 행 — glow(shadow 24px)+scale-105가 잘리지 않도록 세로 패딩 확보.
          overflow-x-auto는 overflow-y를 auto로 강제하므로 py는 glow 반경 이상이어야 함. */}
      <div className="flex flex-col gap-1">
      {/* Session A: Caller → Callee (STT → Translate → TTS) */}
      <div className="flex items-center gap-1 overflow-x-auto px-1 py-6">
        <span className="text-[10px] text-emerald-400/90 font-bold w-11 shrink-0">A·SEND</span>
        <EndPoint label="Caller" Icon={Mic} active={aSpeaking} />
        <Arrow active={aSpeaking} />
        <StagePill
          label="STT"
          detail={aSpeaking ? 'recognizing' : ''}
          tone="blue"
          status={aSpeaking ? 'active' : 'idle'}
          hot={aSpeaking}
          head={aHot && aPhase === 'speaking'}
          Icon={Captions}
        />
        <Arrow active={aTranslating} />
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
        <StagePill
          label="TTS"
          detail={aDelivered ? 'speaking' : ''}
          tone="violet"
          status={aDelivered ? 'active' : 'idle'}
          hot={aDelivered}
          head={aHot && aPhase === 'delivered'}
          Icon={Volume2}
        />
        <Arrow active={aDelivered} />
        <EndPoint label="Callee" Icon={Phone} active={aDelivered} />
        <span
          className={`ml-2 text-xs text-emerald-400/80 font-mono shrink-0 transition-opacity duration-500 ${
            aHot ? 'opacity-100' : 'opacity-0'
          }`}
        >
          {latencyBadge(metrics?.session_a_latencies_ms, '~555ms')}
        </span>
      </div>

      {/* Session B: Callee → Caller (3-stage filter + STT + translate + TTS) */}
      <div className="flex items-center gap-1 overflow-x-auto px-1 py-6">
        <span className="text-[10px] text-cyan-400/90 font-bold w-11 shrink-0">B·RECV</span>
        <EndPoint label="Callee" Icon={Phone} active={bAnyHot} />
        {SESSION_B_STAGES.map((s) => {
          const node: PipeNode = pipeline.b[s.key];
          const hot = isHot(node.at);
          return (
            <Fragment key={s.key}>
              <Arrow active={hot} grow />
              <StagePill
                label={s.label}
                detail={node.detail}
                tone={s.tone}
                status={node.status}
                hot={hot}
                head={headKey === s.key}
                Icon={s.Icon}
                dropped={hot && node.status === 'block'}
              />
            </Fragment>
          );
        })}
        <Arrow active={isHot(pipeline.b.translate_b.at)} grow />
        <StagePill
          label="TTS"
          detail={isHot(pipeline.b.translate_b.at) ? 'speaking' : ''}
          tone="violet"
          status={isHot(pipeline.b.translate_b.at) ? 'active' : 'idle'}
          hot={isHot(pipeline.b.translate_b.at)}
          head={false}
          Icon={Volume2}
        />
        <Arrow active={isHot(pipeline.b.translate_b.at)} grow />
        <EndPoint label="Caller" Icon={Mic} active={isHot(pipeline.b.translate_b.at)} />
        {/* 뱃지 자리를 항상 고정 폭으로 예약 — PASS/DROP가 토글돼도 행 폭이 변하지 않음
            (→ 오른쪽 채팅 컬럼이 들썩이지 않도록 '작은=뱃지 있는' 상태 기준으로 고정) */}
        <span className="ml-1.5 flex w-[66px] shrink-0 justify-center">
          {bOutcome && (
            <span
              className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-bold transition-colors duration-300 ${
                bOutcome === 'pass'
                  ? 'border-emerald-400/60 bg-emerald-400/15 text-emerald-200'
                  : 'border-red-400/60 bg-red-400/15 text-red-200'
              }`}
            >
              {bOutcome === 'pass' ? '✓ PASS' : '⊘ DROP'}
            </span>
          )}
        </span>
        <span
          className={`ml-2 text-xs text-cyan-400/80 font-mono shrink-0 transition-opacity duration-500 ${
            bAnyHot ? 'opacity-100' : 'opacity-0'
          }`}
        >
          {latencyBadge(metrics?.session_b_e2e_latencies_ms, '~2684ms')}
        </span>
      </div>
      </div>
    </div>
  );
}
