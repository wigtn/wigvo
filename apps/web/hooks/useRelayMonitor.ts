'use client';

// =============================================================================
// useRelayMonitor — 관전(observer) WebSocket 훅 (read-only)
// =============================================================================
// `WS /relay/calls/{id}/monitor` 에 붙어 발신자와 동일한 인바운드 이벤트를 소비한다.
// useRelayCall의 인바운드 switch를 발췌·이식하되, observer 제약을 강제한다:
//   - 아무것도 전송하지 않음 (마이크/audio_chunk/vad_state/text_input/end_call 없음)
//   - recipient_audio 재생 안 함
//   - call_status:ended / error 수신 시 의도적 disconnect (자동 재연결 폭주 방지)
// 캡션 머지(stage1→stage2, delta 누적)는 stateful이라 훅 로컬 state로 둔다(PRD M2).
// 파이프라인(pipeline.event/caption/translation.state/interrupt_alert)은 store로(PRD M3).
// =============================================================================

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  WsMessageType,
  type CallMode,
  type CaptionEntry,
  type CommunicationMode,
  type RelayWsMessage,
} from '@/shared/call-types';
import { useRelayWebSocket } from './useRelayWebSocket';
import {
  useMonitorStore,
  type MonitorCallStatus,
  type MonitorMetrics,
  type MonitorSnapshot,
} from './useMonitorStore';

const ROLE_TO_SPEAKER: Record<string, CaptionEntry['speaker']> = {
  assistant: 'ai',
  user: 'user',
  recipient: 'recipient',
  ai: 'ai',
};

// 파이프라인 신호 헬퍼 (store 직접 갱신 — 원본 useRelayCall과 동일 패턴)
function signalA(phase: Parameters<ReturnType<typeof useMonitorStore.getState>['signalA']>[0], detail = '') {
  useMonitorStore.getState().signalA(phase, detail);
}
function signalB(
  stage: Parameters<ReturnType<typeof useMonitorStore.getState>['signalB']>[0],
  status: Parameters<ReturnType<typeof useMonitorStore.getState>['signalB']>[1],
  detail = '',
) {
  useMonitorStore.getState().signalB(stage, status, detail);
}

function pushEvent(
  kind: Parameters<ReturnType<typeof useMonitorStore.getState>['pushEvent']>[0],
  label: string,
) {
  useMonitorStore.getState().pushEvent(kind, label);
}

export interface UseRelayMonitorReturn {
  callStatus: MonitorCallStatus;
  captions: CaptionEntry[];
  callDuration: number;
  error: string | null;
  snapshot: MonitorSnapshot | null;
}

export function useRelayMonitor(wsUrl: string | null): UseRelayMonitorReturn {
  const [callStatus, setCallStatus] = useState<MonitorCallStatus>('connecting');
  const [captions, setCaptions] = useState<CaptionEntry[]>([]);
  const [callDuration, setCallDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<MonitorSnapshot | null>(null);

  const captionCounterRef = useRef(0);
  const streamingRef = useRef<{ direction: string; stage: number | undefined; speaker: string } | null>(null);
  const durationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef = useRef<{ disconnect: () => void } | null>(null);
  const blockedCountRef = useRef(0); // hallucinations_blocked 증가 감지용
  const callerTurnRef = useRef<string | null>(null); // 발신자 현재 턴 버블 id(원문+번역 병합용)
  const calleeTurnRef = useRef<string | null>(null); // 수신자 현재 턴 버블 id(원문+번역 병합용)

  const handleMessage = useCallback((msg: RelayWsMessage) => {
    switch (msg.type) {
      case WsMessageType.CAPTION:
      case WsMessageType.CAPTION_ORIGINAL:
      case WsMessageType.CAPTION_TRANSLATED: {
        const stage =
          msg.type === WsMessageType.CAPTION_ORIGINAL ? 1
          : msg.type === WsMessageType.CAPTION_TRANSLATED ? 2
          : (msg.data.stage as 1 | 2 | undefined);

        const direction = (msg.data.direction as string) ?? 'unknown';
        const rawRole = (msg.data.role as string) ?? (msg.data.speaker as string) ?? 'recipient';
        const speaker: CaptionEntry['speaker'] = ROLE_TO_SPEAKER[rawRole] ?? 'recipient';
        const text = (msg.data.text as string) ?? '';

        // Live pipeline: 캡션 방향/단계로 흐름 구동 (PRD M3)
        if (direction === 'outbound') {
          signalA(stage === 2 ? 'delivered' : 'speaking');
        } else if (direction === 'inbound') {
          if (stage === 2) {
            signalB('stt', 'done');
            signalB('translate_b', 'done', 'translated');
          } else {
            signalB('stt', 'active', 'STT...');
          }
        }

        // 발신자(outbound) 턴: 원문(role=user) + 번역(role=ai)을 한 버블로 병합.
        // ⚠️ Realtime에서 원문 STT가 번역보다 늦게 오는 경우가 흔함 → 순서 무관하게 페어링.
        //   - 번역(ai): 열린 번역 버블(callerTurnRef)에 누적, 없으면 원문만 있는 버블에 붙이거나 새로.
        //   - 원문(user): "원문이 아직 없는 가장 최근 발신자 버블"에 붙임(늦게 와도 페어링됨).
        //   - 턴은 TRANSLATION_STATE 'done'(번역 완료) / 수신자 발화에서 닫는다.
        // 메인 = 원문(originalText), 아래 = 번역(text).
        if (direction === 'outbound') {
          const lang = (msg.data.language as string) ?? '';
          const isOriginal = speaker === 'user';
          setCaptions((prev) => {
            const updated = [...prev];
            if (isOriginal) {
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].speaker === 'user' && !updated[i].originalText) {
                  updated[i] = { ...updated[i], originalText: text };
                  return updated;
                }
              }
            } else {
              if (callerTurnRef.current) {
                const i = updated.findIndex((c) => c.id === callerTurnRef.current);
                if (i >= 0) {
                  updated[i] = { ...updated[i], text: (updated[i].text ?? '') + text };
                  return updated;
                }
              }
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].speaker === 'user' && !updated[i].text) {
                  callerTurnRef.current = updated[i].id;
                  updated[i] = { ...updated[i], text };
                  return updated;
                }
              }
            }
            captionCounterRef.current += 1;
            const id = `caption-${captionCounterRef.current}`;
            if (!isOriginal) callerTurnRef.current = id;
            updated.push({
              id,
              speaker: 'user',
              text: isOriginal ? '' : text,
              originalText: isOriginal ? text : undefined,
              language: lang,
              isFinal: false,
              timestamp: Date.now(),
            });
            return updated;
          });
          streamingRef.current = null;
          break;
        }
        // 수신자 발화 시작 = 발신자 턴 종료
        callerTurnRef.current = null;

        // 수신자(inbound) 턴: 원문(stage 1) + 번역(stage 2)을 한 버블로 병합 (발신자와 동일 방식).
        // 스트리밍/순서 무관 페어링. 메인 = 원문(originalText), 아래 = 번역(text).
        // 턴은 TRANSLATION_STATE 'caption_done'(수신자 번역 완료) / 발신자 발화에서 닫는다.
        {
          const lang = (msg.data.language as string) ?? '';
          const isOriginalIn = stage === 1;
          setCaptions((prev) => {
            const updated = [...prev];
            if (isOriginalIn) {
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].speaker === 'recipient' && !updated[i].originalText) {
                  updated[i] = { ...updated[i], originalText: text };
                  return updated;
                }
              }
            } else {
              if (calleeTurnRef.current) {
                const i = updated.findIndex((c) => c.id === calleeTurnRef.current);
                if (i >= 0) {
                  updated[i] = { ...updated[i], text: (updated[i].text ?? '') + text };
                  return updated;
                }
              }
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].speaker === 'recipient' && !updated[i].text) {
                  calleeTurnRef.current = updated[i].id;
                  updated[i] = { ...updated[i], text };
                  return updated;
                }
              }
            }
            captionCounterRef.current += 1;
            const id = `caption-${captionCounterRef.current}`;
            if (!isOriginalIn) calleeTurnRef.current = id;
            updated.push({
              id,
              speaker: 'recipient',
              text: isOriginalIn ? '' : text,
              originalText: isOriginalIn ? text : undefined,
              language: lang,
              isFinal: false,
              timestamp: Date.now(),
            });
            return updated;
          });
        }
        break;
      }

      case WsMessageType.RECIPIENT_AUDIO:
        // 관전 모드: 수신자 음성은 재생하지 않음
        break;

      case WsMessageType.CALL_STATUS: {
        const status = (msg.data.status as string) ?? (msg.data.message as string);

        // 연결 스냅샷: 통화 메타 캡처 (call_status에 동봉됨)
        if (msg.data.source_language || msg.data.target_language || msg.data.communication_mode) {
          setSnapshot((prev) => ({
            ...prev,
            sourceLanguage: (msg.data.source_language as string) ?? prev?.sourceLanguage,
            targetLanguage: (msg.data.target_language as string) ?? prev?.targetLanguage,
            communicationMode: (msg.data.communication_mode as CommunicationMode) ?? prev?.communicationMode,
            callMode: (msg.data.call_mode as CallMode) ?? prev?.callMode,
          }));
        }

        if (status === 'ringing' || status === 'waiting') {
          setCallStatus('waiting');
        } else if (status === 'connected' || status === 'in-progress') {
          setCallStatus('connected');
        } else if (status === 'ended' || status === 'completed' || status === 'failed') {
          setCallStatus('ended');
          if (durationTimerRef.current) {
            clearInterval(durationTimerRef.current);
            durationTimerRef.current = null;
          }
          // 종료 시 의도적 disconnect — 서버가 소켓을 닫으므로 자동 재연결 방지 (PRD M1)
          setTimeout(() => wsRef.current?.disconnect(), 300);
        }
        break;
      }

      case WsMessageType.TRANSLATION_STATE: {
        const state = msg.data.state as string;
        if (state === 'processing') signalA('translating', 'translating');
        else if (state === 'done') {
          signalA('delivered');
          // 발신자 번역 완료 → 다음 outbound 캡션은 새 턴 버블로 시작 (원문은 늦게 와도 직전 버블에 페어링됨)
          callerTurnRef.current = null;
        }
        if (state === 'caption_done') {
          streamingRef.current = null;
          // 수신자 번역 완료 → 다음 inbound 캡션은 새 턴 버블로 시작
          calleeTurnRef.current = null;
        }
        break;
      }

      case WsMessageType.INTERRUPT_ALERT:
        signalB('silero_vad', 'bargein', 'callee barge-in');
        pushEvent('bargein', 'Recipient interrupted');
        break;

      case WsMessageType.METRICS: {
        const m = msg.data as unknown as MonitorMetrics;
        useMonitorStore.getState().syncState({ metrics: m });
        // hallucinations_blocked 증가 = 이번에 환각/에코 누출을 차단함
        const blocked = m.hallucinations_blocked ?? 0;
        if (blocked > blockedCountRef.current) {
          pushEvent('guard', 'Hallucination blocked');
          blockedCountRef.current = blocked;
        }
        break;
      }

      case WsMessageType.GUARDRAIL_TRIGGERED: {
        const level = (msg.data.level as string) ?? '';
        pushEvent('guard', level ? `Guardrail L${level} triggered` : 'Guardrail triggered');
        break;
      }

      case WsMessageType.PIPELINE_EVENT: {
        const stage = msg.data.stage as string;
        const event = (msg.data.event as string) ?? '';
        const rmsVal = msg.data.rms ?? msg.data.peak_rms;
        const rmsTxt = rmsVal != null ? `RMS ${rmsVal}` : '';
        if (stage === 'echo_gate') {
          if (event.includes('absorb')) {
            signalB('echo_gate', 'block', 'echo absorbed');
            pushEvent('echo', 'Echo absorbed');
          } else if (event.includes('break')) {
            signalB('echo_gate', 'bargein', 'barge-in');
            pushEvent('bargein', 'Echo gate barge-in');
          } else if (event.includes('deactiv')) signalB('echo_gate', 'idle', '');
          else signalB('echo_gate', 'active', 'gate closed');
        } else if (stage === 'energy_gate') {
          signalB('energy_gate', event === 'accept' ? 'pass' : 'block', rmsTxt);
        } else if (stage === 'silero_vad') {
          signalB(
            'silero_vad',
            event === 'speech_start' ? 'active' : 'done',
            event === 'speech_start' ? rmsTxt : 'speech end',
          );
        }
        break;
      }

      case WsMessageType.ERROR: {
        const message = (msg.data.message as string) ?? 'Unknown error';
        setError(message);
        // Call not found 등 — 재연결 폭주 방지 위해 의도적 disconnect (PRD M1)
        setTimeout(() => wsRef.current?.disconnect(), 100);
        break;
      }

      default:
        break;
    }
  }, []);

  const ws = useRelayWebSocket({ url: wsUrl, onMessage: handleMessage, autoConnect: true });

  // wsRef 동기화 (handleMessage가 ended/error 시 disconnect 호출에 사용) — 렌더 중 ref 쓰기 회피
  useEffect(() => {
    wsRef.current = ws;
  });

  // 통화 시간 (관전 접속 시점 기준 — PRD §10 비고)
  useEffect(() => {
    if (callStatus === 'connected') {
      durationTimerRef.current = setInterval(() => setCallDuration((prev) => prev + 1), 1000);
    }
    return () => {
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
        durationTimerRef.current = null;
      }
    };
  }, [callStatus]);

  return { callStatus, captions, callDuration, error, snapshot };
}
