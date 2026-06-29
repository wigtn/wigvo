'use client';

// =============================================================================
// useMonitorStore — 부스 관전(observer) 전용 Zustand store
// =============================================================================
// 통화 화면 store(useRelayCallStore)와 분리. 0df4d35로 통화 화면에서 제거된
// 파이프라인 viz를 "모니터 스코프로만" 되살리기 위해 pipeline 슬라이스를 여기 둔다.
// (통화 화면 store를 다시 오염시키지 않는다 — PRD §5.5)
//
// 데이터 흐름: useRelayMonitor(WS 수신) → MonitorProvider(sync) → store → 컴포넌트
//   - captions/callStatus/callDuration/error/snapshot: Provider가 훅 state를 sync
//   - pipeline: useRelayMonitor가 signalA/signalB로 직접 갱신(원본 패턴과 동일)
// =============================================================================

import { create } from 'zustand';
import type { CallMode, CaptionEntry, CommunicationMode } from '@/shared/call-types';

export type MonitorCallStatus = 'idle' | 'connecting' | 'waiting' | 'connected' | 'ended';

// --- Live Pipeline (93c0142에서 복원, 모니터 스코프로 이식) ---
export type PipeStatus = 'idle' | 'active' | 'pass' | 'block' | 'bargein' | 'done';
export interface PipeNode {
  status: PipeStatus;
  detail: string;
  at: number; // 마지막 갱신 시각(ms) — 컴포넌트가 decay(불 꺼짐) 판정에 사용
}
export type PipeStageKey = 'echo_gate' | 'energy_gate' | 'silero_vad' | 'stt' | 'translate_b';
export type APhase = 'idle' | 'speaking' | 'translating' | 'delivered';

export interface LivePipeline {
  aPhase: APhase; // Session A (발신자→수신자) 빠른 경로
  aDetail: string;
  aAt: number;
  b: Record<PipeStageKey, PipeNode>; // Session B (수신자→발신자) 3단계+STT+번역
  lastAt: number;
}

// MonitorPipeline의 지연 배지 + 부스 카운터에 쓰는 부분만 (relay metrics 전체 중 일부)
export interface MonitorMetrics {
  session_a_latencies_ms?: number[];
  session_b_e2e_latencies_ms?: number[];
  echo_suppressions?: number;
  hallucinations_blocked?: number;
}

// 결정적 순간(에코 흡수·돌파·가드레일)을 부스 이벤트 피드에 표시
export type MonitorEventKind = 'echo' | 'bargein' | 'guard' | 'info';

// ACTIVITY 카탈로그: 추적하는 결정적 신호 5종 (고정 표시 + 신호별 카운트)
export type MonitorSignalKey =
  | 'echo_absorbed'
  | 'recipient_interrupted'
  | 'echo_bargein'
  | 'hallucination'
  | 'guardrail';

export interface MonitorEvent {
  id: number;
  kind: MonitorEventKind;
  label: string;
  signal?: MonitorSignalKey;
  at: number;
}

// 관전 연결 시 call_status 스냅샷에서 받는 통화 메타
export interface MonitorSnapshot {
  sourceLanguage?: string;
  targetLanguage?: string;
  communicationMode?: CommunicationMode;
  callMode?: CallMode;
  targetName?: string | null;
}

const freshNode = (): PipeNode => ({ status: 'idle', detail: '', at: 0 });
const freshPipeline = (): LivePipeline => ({
  aPhase: 'idle',
  aDetail: '',
  aAt: 0,
  b: {
    echo_gate: freshNode(),
    energy_gate: freshNode(),
    silero_vad: freshNode(),
    stt: freshNode(),
    translate_b: freshNode(),
  },
  lastAt: 0,
});

interface MonitorState {
  callStatus: MonitorCallStatus;
  captions: CaptionEntry[];
  callDuration: number;
  error: string | null;
  snapshot: MonitorSnapshot | null;
  metrics: MonitorMetrics | null;
  pipeline: LivePipeline;
  events: MonitorEvent[];
  echoBlocked: number; // 에코 흡수(잡아낸) 누적 횟수
  guardBlocked: number; // 환각/가드레일 차단 누적 횟수
  signalCounts: Record<MonitorSignalKey, number>; // ACTIVITY 신호별 누적 횟수

  // 동기화 (MonitorProvider가 훅 state를 주입)
  syncState: (partial: Partial<MonitorState>) => void;

  // 부스 이벤트 피드 (useRelayMonitor가 결정적 순간마다 호출)
  pushEvent: (kind: MonitorEventKind, label: string, signal?: MonitorSignalKey) => void;

  // 파이프라인 신호 (useRelayMonitor가 WS 이벤트로 호출)
  signalA: (phase: APhase, detail?: string) => void;
  signalB: (stage: PipeStageKey, status: PipeStatus, detail?: string) => void;
  resetPipeline: () => void;

  reset: () => void;
}

const initialState = {
  callStatus: 'connecting' as MonitorCallStatus,
  captions: [] as CaptionEntry[],
  callDuration: 0,
  error: null as string | null,
  snapshot: null as MonitorSnapshot | null,
  metrics: null as MonitorMetrics | null,
  pipeline: freshPipeline() as LivePipeline,
  events: [] as MonitorEvent[],
  echoBlocked: 0,
  guardBlocked: 0,
  signalCounts: {
    echo_absorbed: 0,
    recipient_interrupted: 0,
    echo_bargein: 0,
    hallucination: 0,
    guardrail: 0,
  } as Record<MonitorSignalKey, number>,
};

let _eventId = 0;

export const useMonitorStore = create<MonitorState>((set, get) => ({
  ...initialState,

  syncState: (partial) => set(partial),

  pushEvent: (kind, label, signal) => {
    const now = Date.now();
    set((state) => ({
      events: [...state.events.slice(-29), { id: ++_eventId, kind, label, signal, at: now }],
      echoBlocked: state.echoBlocked + (kind === 'echo' ? 1 : 0),
      guardBlocked: state.guardBlocked + (kind === 'guard' ? 1 : 0),
      signalCounts: signal
        ? { ...state.signalCounts, [signal]: (state.signalCounts[signal] ?? 0) + 1 }
        : state.signalCounts,
    }));
  },

  signalA: (phase, detail = '') => {
    const now = Date.now();
    set({ pipeline: { ...get().pipeline, aPhase: phase, aDetail: detail, aAt: now, lastAt: now } });
  },

  signalB: (stage, status, detail = '') => {
    const now = Date.now();
    const prev = get().pipeline;
    set({
      pipeline: {
        ...prev,
        b: { ...prev.b, [stage]: { status, detail, at: now } },
        lastAt: now,
      },
    });
  },

  resetPipeline: () => set({ pipeline: freshPipeline() }),

  reset: () => set({ ...initialState, pipeline: freshPipeline() }),
}));
