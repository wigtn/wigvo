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

export const useRelayCallStore = create<RelayCallStoreState>((set) => ({
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

  syncState: (partial) => set(partial),

  reset: () => set({ ...initialState }),
}));
