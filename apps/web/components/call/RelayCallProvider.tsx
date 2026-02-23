'use client';

import { useEffect, useRef } from 'react';
import { useCallPolling } from '@/hooks/useCallPolling';
import { useRelayCall } from '@/hooks/useRelayCall';
import { useRelayCallStore } from '@/hooks/useRelayCallStore';
import type { CallMode, CommunicationMode } from '@/shared/call-types';
import { isDemoMode, MOCK_WS_URL_PREFIX } from '@/lib/demo';

interface RelayCallProviderProps {
  callingCallId: string;
  communicationMode: CommunicationMode;
  children: React.ReactNode;
}

/**
 * RelayCallProvider
 * - useCallPolling으로 Call 메타데이터 조회 (relayWsUrl, callMode)
 * - useRelayCall 인스턴스 생성
 * - relay 상태를 Zustand store에 동기화
 * - children (CallEffectPanel 등)은 store에서 읽기
 * - ChatContainer는 Provider 바깥이지만 동일한 store를 읽음
 */
export default function RelayCallProvider({
  callingCallId,
  communicationMode,
  children,
}: RelayCallProviderProps) {
  const { call } = useCallPolling(callingCallId);
  const relay = useRelayCall(communicationMode);
  const syncState = useRelayCallStore((s) => s.syncState);
  const reset = useRelayCallStore((s) => s.reset);
  const startedRef = useRef(false);
  const prevCallIdRef = useRef(callingCallId);

  // callingCallId 변경 시 startedRef 리셋 (같은 마운트에서 새 통화 시작 가능)
  useEffect(() => {
    if (prevCallIdRef.current !== callingCallId) {
      prevCallIdRef.current = callingCallId;
      startedRef.current = false;
    }
  }, [callingCallId]);

  // relayWsUrl 확보 시 startCall
  useEffect(() => {
    if (startedRef.current) return;

    // Demo preview path: force-start mock realtime call even before polling settles.
    if (isDemoMode()) {
      startedRef.current = true;
      const demoWsUrl = call?.relayWsUrl ?? `${MOCK_WS_URL_PREFIX}${callingCallId}`;
      const demoMode = (call?.callMode as CallMode | undefined) ?? 'relay';
      relay.startCall(callingCallId, demoWsUrl, demoMode);
      return;
    }

    if (!call?.relayWsUrl || !call.callMode) return;
    startedRef.current = true;
    relay.startCall(callingCallId, call.relayWsUrl, call.callMode as CallMode);
  }, [call?.relayWsUrl, call?.callMode, callingCallId, relay]);

  // relay 반환값 → store 동기화
  useEffect(() => {
    syncState({
      callStatus: relay.callStatus,
      translationState: relay.translationState,
      captions: relay.captions,
      callDuration: relay.callDuration,
      callMode: relay.callMode,
      isMuted: relay.isMuted,
      isRecording: relay.isRecording,
      isPlaying: relay.isPlaying,
      error: relay.error,
    });
  }, [
    relay.callStatus,
    relay.translationState,
    relay.captions,
    relay.callDuration,
    relay.callMode,
    relay.isMuted,
    relay.isRecording,
    relay.isPlaying,
    relay.error,
    syncState,
  ]);

  // actions → store 등록
  useEffect(() => {
    syncState({
      startCall: relay.startCall,
      endCall: relay.endCall,
      sendText: relay.sendText,
      sendTypingState: relay.sendTypingState,
      toggleMute: relay.toggleMute,
    });
  }, [relay.startCall, relay.endCall, relay.sendText, relay.sendTypingState, relay.toggleMute, syncState]);

  // 언마운트 시 정리
  useEffect(() => {
    return () => {
      relay.endCall();
      reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <>{children}</>;
}
