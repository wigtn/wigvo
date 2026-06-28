'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  WsMessageType,
  getModeUIConfig,
  type CallMode,
  type CaptionEntry,
  type CommunicationMode,
  type RelayWsMessage,
} from '@/shared/call-types';
import { useRelayWebSocket } from './useRelayWebSocket';
import { useClientVad } from './useClientVad';
import { useWebAudioPlayer } from './useWebAudioPlayer';
import { useRelayCallStore, type CallMetrics, type EventLogEntry } from './useRelayCallStore';

// --- Pipeline Event Log helpers (module scope for stable reference) ---
const PIPELINE_STAGE_TAG_MAP: Record<string, { tag: string; color: string }> = {
  echo_gate: { tag: 'Echo Gate', color: 'text-orange-400' },
  energy_gate: { tag: 'Energy Gate', color: 'text-cyan-400' },
  silero_vad: { tag: 'Silero VAD', color: 'text-violet-400' },
};

function pushEventLog(entry: Omit<EventLogEntry, 'id' | 'timestamp'>) {
  useRelayCallStore.getState().addEventLog(entry);
}

type CallStatus = 'idle' | 'connecting' | 'waiting' | 'connected' | 'ended';
type TranslationState = 'idle' | 'processing' | 'done';

interface UseRelayCallReturn {
  callStatus: CallStatus;
  translationState: TranslationState;
  captions: CaptionEntry[];
  callDuration: number;
  callMode: CallMode;
  startCall: (callId: string, relayWsUrl: string, mode: CallMode) => void;
  endCall: () => void;
  sendText: (text: string) => void;
  sendTypingState: () => void;
  toggleMute: () => void;
  isMuted: boolean;
  isRecording: boolean;
  isPlaying: boolean;
  error: string | null;
}

export function useRelayCall(communicationMode: CommunicationMode = 'voice_to_voice'): UseRelayCallReturn {
  const [callStatus, setCallStatus] = useState<CallStatus>('idle');
  const [translationState, setTranslationState] = useState<TranslationState>('idle');
  const [captions, setCaptions] = useState<CaptionEntry[]>([]);
  const [callDuration, setCallDuration] = useState(0);
  const [callMode, setCallMode] = useState<CallMode>('agent');
  const [isMuted, setIsMuted] = useState(false);
  const [wsUrl, setWsUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const durationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const userSpeakingRef = useRef(false);
  const wsRef = useRef<{ disconnect: () => void } | null>(null);

  // Mode UI config
  const modeConfig = getModeUIConfig(communicationMode);

  // Audio player
  const player = useWebAudioPlayer();

  // Caption counter for unique IDs
  const captionCounterRef = useRef(0);

  // Track current streaming caption for delta accumulation
  // Streaming deltas from the same speaker/direction/stage are merged into one caption
  const streamingRef = useRef<{
    direction: string;
    stage: number | undefined;
    speaker: string;
  } | null>(null);

  // Handle incoming WS messages
  const handleMessage = useCallback(
    (msg: RelayWsMessage) => {
      switch (msg.type) {
        case WsMessageType.CAPTION:
        case WsMessageType.CAPTION_ORIGINAL:
        case WsMessageType.CAPTION_TRANSLATED: {
          const stage = msg.type === WsMessageType.CAPTION_ORIGINAL ? 1
            : msg.type === WsMessageType.CAPTION_TRANSLATED ? 2
            : (msg.data.stage as 1 | 2 | undefined);

          const direction = (msg.data.direction as string) ?? 'unknown';
          // Server sends "role" (assistant/user/recipient) — map to client speaker type
          const rawRole = (msg.data.role as string) ?? (msg.data.speaker as string) ?? 'recipient';
          const ROLE_TO_SPEAKER: Record<string, CaptionEntry['speaker']> = {
            assistant: 'ai', user: 'user', recipient: 'recipient', ai: 'ai',
          };
          const speaker: CaptionEntry['speaker'] = ROLE_TO_SPEAKER[rawRole] ?? 'recipient';
          const text = (msg.data.text as string) ?? '';

          const cur = streamingRef.current;

          // Append to existing caption if same speaker + direction + stage
          if (cur &&
              cur.direction === direction &&
              cur.stage === stage &&
              cur.speaker === speaker) {
            setCaptions((prev) => {
              if (prev.length === 0) return prev;
              const updated = [...prev];
              const last = updated[updated.length - 1];
              // Stage 2 스트리밍 중: 원문도 함께 누적
              updated[updated.length - 1] = { ...last, text: last.text + text };
              return updated;
            });
          } else if (stage === 2 && direction === 'inbound') {
            // Stage 2(번역) 시작 시: 직전 Stage 1(원문)을 찾아 병합
            setCaptions((prev) => {
              // 직전 Stage 1 엔트리 찾기 (같은 speaker, inbound)
              const lastStage1Idx = prev.length > 0 && prev[prev.length - 1].stage === 1
                  && prev[prev.length - 1].speaker === speaker
                ? prev.length - 1
                : -1;

              if (lastStage1Idx >= 0) {
                // Stage 1을 제거하고 originalText로 보존한 Stage 2 엔트리 생성
                const stage1 = prev[lastStage1Idx];
                captionCounterRef.current += 1;
                const merged: CaptionEntry = {
                  id: `caption-${captionCounterRef.current}`,
                  speaker,
                  text,
                  originalText: stage1.text,
                  language: (msg.data.language as string) ?? '',
                  isFinal: false,
                  timestamp: Date.now(),
                  stage: 2,
                };
                const updated = prev.slice(0, lastStage1Idx);
                return [...updated, merged];
              }

              // Stage 1이 없으면 단독 Stage 2
              captionCounterRef.current += 1;
              const entry: CaptionEntry = {
                id: `caption-${captionCounterRef.current}`,
                speaker,
                text,
                language: (msg.data.language as string) ?? '',
                isFinal: false,
                timestamp: Date.now(),
                stage,
              };
              return [...prev, entry];
            });
            streamingRef.current = { direction, stage, speaker };
          } else {
            // New caption entry (Stage 1 원문 또는 outbound)
            captionCounterRef.current += 1;
            const entry: CaptionEntry = {
              id: `caption-${captionCounterRef.current}`,
              speaker,
              text,
              language: (msg.data.language as string) ?? '',
              isFinal: false,
              timestamp: Date.now(),
              stage,
            };
            setCaptions((prev) => [...prev, entry]);
            streamingRef.current = { direction, stage, speaker };
          }
          break;
        }

        case WsMessageType.RECIPIENT_AUDIO: {
          // audioOutput이 false인 모드: 수신자 음성은 재생하지 않음
          if (!modeConfig.audioOutput) break;
          const audio = msg.data.audio as string;
          if (audio) {
            player.play(audio);
          }
          break;
        }

        case WsMessageType.CALL_STATUS: {
          const status = (msg.data.status as string) ?? (msg.data.message as string);
          pushEventLog({ tag: 'Call', message: status ?? 'unknown', color: 'text-blue-400' });
          if (status === 'ringing' || status === 'waiting') {
            setCallStatus('waiting');
          } else if (status === 'connected' || status === 'in-progress') {
            setCallStatus('connected');
          } else if (status === 'ended' || status === 'completed' || status === 'failed') {
            setCallStatus('ended');
            // Server confirmed call ended — clean up resources
            player.stop();
            if (durationTimerRef.current) {
              clearInterval(durationTimerRef.current);
              durationTimerRef.current = null;
            }
            // Delay disconnect so any final messages can arrive
            setTimeout(() => {
              wsRef.current?.disconnect();
              setWsUrl(null);
            }, 300);
          }
          break;
        }

        case WsMessageType.TRANSLATION_STATE: {
          const state = msg.data.state as string;
          if (state === 'processing' || state === 'done' || state === 'caption_done') {
            pushEventLog({ tag: 'Session A', message: state, color: 'text-green-400' });
          }
          if (state === 'caption_done') {
            // Session B 번역 완료 → 스트리밍 컨텍스트 리셋
            // 다음 수신자 발화 delta가 새 캡션 엔트리로 생성됨
            streamingRef.current = null;
          } else if (state) {
            setTranslationState(state as TranslationState);
          }
          break;
        }

        case WsMessageType.INTERRUPT_ALERT: {
          pushEventLog({ tag: 'Interrupt', message: 'Recipient speaking', color: 'text-red-400' });
          // Clear playback queue when recipient is speaking
          player.clearQueue();
          break;
        }

        case WsMessageType.METRICS:
          useRelayCallStore.getState().syncState({ metrics: msg.data as unknown as CallMetrics });
          break;

        case WsMessageType.GUARDRAIL_TRIGGERED: {
          const level = (msg.data.level as string) ?? '';
          pushEventLog({ tag: 'Guardrail', message: `L${level} triggered`, color: 'text-yellow-400' });
          break;
        }

        case WsMessageType.SESSION_RECOVERY: {
          const recoveryType = (msg.data.type as string) ?? 'unknown';
          pushEventLog({ tag: 'Recovery', message: recoveryType, color: 'text-cyan-400' });
          break;
        }

        case WsMessageType.PIPELINE_EVENT: {
          const stage = msg.data.stage as string;
          const event = msg.data.event as string;
          const { tag, color } = PIPELINE_STAGE_TAG_MAP[stage] ?? { tag: stage, color: 'text-gray-400' };
          const rms = msg.data.rms != null ? ` (RMS: ${msg.data.rms})` : '';
          const peakRms = msg.data.peak_rms != null ? ` (peak: ${msg.data.peak_rms})` : '';
          const extra = msg.data.duration_s != null ? ` ${(msg.data.duration_s as number).toFixed(1)}s` : '';
          const totalS = msg.data.total_s != null ? ` ${(msg.data.total_s as number).toFixed(1)}s` : '';
          pushEventLog({ tag, message: `${event}${rms}${peakRms}${extra}${totalS}`, color });
          break;
        }

        case WsMessageType.ERROR: {
          const message = (msg.data.message as string) ?? 'Unknown error';
          setError(message);
          break;
        }

        default:
          break;
      }
    },
    [player, modeConfig.audioOutput],
  );

  // WebSocket connection
  const ws = useRelayWebSocket({
    url: wsUrl,
    onMessage: handleMessage,
    autoConnect: true,
  });
  wsRef.current = ws;

  // Update callStatus when ws connects
  useEffect(() => {
    if (ws.status === 'connected' && callStatus === 'connecting') {
      setCallStatus('waiting');
    } else if (ws.status === 'error') {
      setError('WebSocket connection failed');
    }
  }, [ws.status, callStatus]);

  // Client VAD — active only when audioInput is enabled and not muted
  const vadEnabled = modeConfig.audioInput && !isMuted && wsUrl !== null && ws.status === 'connected';

  const { isSpeaking } = useClientVad({
    onSpeechAudio: (base64Audio: string) => {
      if (!userSpeakingRef.current) {
        userSpeakingRef.current = true;
        player.stop();
      }
      ws.sendAudioChunk(base64Audio);
    },
    onSpeechCommitted: () => {
      userSpeakingRef.current = false;
      ws.sendVadState('committed');
    },
    enabled: vadEnabled,
  });

  // Call duration timer
  useEffect(() => {
    if (callStatus === 'connected') {
      durationTimerRef.current = setInterval(() => {
        setCallDuration((prev) => prev + 1);
      }, 1000);
    }

    return () => {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
        durationTimerRef.current = null;
      }
    };
  }, [callStatus]);

  const startCall = useCallback(
    (callId: string, relayWsUrl: string, mode: CallMode) => {
      setCallMode(mode);
      setCallStatus('connecting');
      setCaptions([]);
      setCallDuration(0);
      setError(null);
      setTranslationState('idle');
      setIsMuted(false);
      captionCounterRef.current = 0;
      streamingRef.current = null;
      setWsUrl(relayWsUrl);
    },
    [],
  );

  const endCall = useCallback(() => {
    // Send END_CALL first, then wait briefly before disconnecting
    // to ensure the message is delivered to the relay server.
    const sent = ws.sendEndCall();
    player.stop();
    setCallStatus('ended');

    if (durationTimerRef.current) {
      clearInterval(durationTimerRef.current);
      durationTimerRef.current = null;
    }

    if (sent) {
      // Give the server time to receive END_CALL and process Twilio hangup
      setTimeout(() => {
        ws.disconnect();
        setWsUrl(null);
      }, 500);
    } else {
      // WebSocket was not open — disconnect immediately
      ws.disconnect();
      setWsUrl(null);
    }
  }, [ws, player]);

  const sendText = useCallback(
    (text: string) => {
      ws.sendText(text);
      // Add local caption immediately so the user sees their text in the chat
      captionCounterRef.current += 1;
      const entry: CaptionEntry = {
        id: `caption-${captionCounterRef.current}`,
        speaker: 'user',
        text,
        language: '',
        isFinal: true,
        timestamp: Date.now(),
      };
      setCaptions((prev) => [...prev, entry]);
      streamingRef.current = null;
    },
    [ws],
  );

  const sendTypingState = useCallback(() => {
    ws.sendTypingState();
  }, [ws]);

  const toggleMute = useCallback(() => {
    setIsMuted((prev) => !prev);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
      }
    };
  }, []);

  return {
    callStatus,
    translationState,
    captions,
    callDuration,
    callMode,
    startCall,
    endCall,
    sendText,
    sendTypingState,
    toggleMute,
    isMuted,
    isRecording: vadEnabled && isSpeaking,
    isPlaying: player.isPlaying,
    error,
  };
}
