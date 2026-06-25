'use client';

import { create } from 'zustand';
import type { CallMode, CaptionEntry, CommunicationMode } from '@/shared/call-types';
import type { Call } from '@/shared/types';

export interface EventLogEntry {
  id: number;
  timestamp: number;
  tag: string;
  message: string;
  color: string;
}

// --- Live Pipeline (실시간 단계 모니터) ---
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

export interface CallMetrics {
  session_a_latencies_ms: number[];
  session_b_e2e_latencies_ms: number[];
  session_b_stt_latencies_ms: number[];
  first_message_latency_ms: number;
  turn_count: number;
  echo_suppressions: number;
  hallucinations_blocked: number;
  vad_false_triggers: number;
  echo_loops_detected: number;
}

type CallStatus = 'idle' | 'connecting' | 'waiting' | 'connected' | 'ended';
type TranslationState = 'idle' | 'processing' | 'done';

interface RelayCallStoreState {
  // 상태 (RelayCallProvider가 동기화)
  callStatus: CallStatus;
  translationState: TranslationState;
  captions: CaptionEntry[];
  callDuration: number;
  callMode: CallMode;
  isMuted: boolean;
  isRecording: boolean;
  isPlaying: boolean;
  error: string | null;
  metrics: CallMetrics | null;
  eventLog: EventLogEntry[];
  pipeline: LivePipeline;

  // Call 메타데이터 (Provider가 동기화)
  callData: Call | null;
  callDataLoading: boolean;
  callDataError: string | null;
  refetchCallData: (() => void) | null;

  // 액션 (Provider가 주입)
  startCall: ((callId: string, relayWsUrl: string, mode: CallMode) => void) | null;
  endCall: (() => void) | null;
  sendText: ((text: string) => void) | null;
  sendTypingState: (() => void) | null;
  toggleMute: (() => void) | null;

  // 액션 (Event Log)
  addEventLog: (entry: Omit<EventLogEntry, 'id' | 'timestamp'>) => void;

  // 액션 (Live Pipeline)
  signalPipeA: (phase: APhase, detail?: string) => void;
  signalPipeB: (stage: PipeStageKey, status: PipeStatus, detail?: string) => void;
  resetPipeline: () => void;

  // 동기화
  syncState: (partial: Partial<RelayCallStoreState>) => void;
  reset: () => void;
}

const initialState = {
  callStatus: 'idle' as CallStatus,
  translationState: 'idle' as TranslationState,
  captions: [] as CaptionEntry[],
  callDuration: 0,
  callMode: 'agent' as CallMode,
  isMuted: false,
  isRecording: false,
  isPlaying: false,
  error: null as string | null,
  metrics: null as CallMetrics | null,
  eventLog: [] as EventLogEntry[],
  pipeline: freshPipeline() as LivePipeline,
  callData: null as Call | null,
  callDataLoading: true,
  callDataError: null as string | null,
  refetchCallData: null as RelayCallStoreState['refetchCallData'],
  startCall: null as RelayCallStoreState['startCall'],
  endCall: null as RelayCallStoreState['endCall'],
  sendText: null as RelayCallStoreState['sendText'],
  sendTypingState: null as RelayCallStoreState['sendTypingState'],
  toggleMute: null as RelayCallStoreState['toggleMute'],
};

let _eventLogId = 0;

export const useRelayCallStore = create<RelayCallStoreState>((set, get) => ({
  ...initialState,

  addEventLog: (entry) => {
    const now = Date.now();
    set((state) => ({
      eventLog: [
        ...state.eventLog.slice(-99),
        { ...entry, id: ++_eventLogId, timestamp: now },
      ],
    }));
  },

  signalPipeA: (phase, detail = '') => {
    const now = Date.now();
    set({ pipeline: { ...get().pipeline, aPhase: phase, aDetail: detail, aAt: now, lastAt: now } });
  },

  signalPipeB: (stage, status, detail = '') => {
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

  syncState: (partial) => set(partial),

  reset: () => set({ ...initialState, pipeline: freshPipeline() }),
}));
