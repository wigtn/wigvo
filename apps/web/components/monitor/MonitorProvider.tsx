'use client';

// MonitorProvider — useRelayMonitor(관전 WS) → useMonitorStore 동기화.
// RelayCallProvider와 동일 패턴: 훅이 인바운드를 처리하고 store에 sync,
// children(MonitorStatusBar/Pipeline/Transcript)은 store에서 읽는다.

import { useEffect } from 'react';
import { useRelayMonitor } from '@/hooks/useRelayMonitor';
import { useMonitorStore } from '@/hooks/useMonitorStore';

interface MonitorProviderProps {
  wsUrl: string | null;
  children: React.ReactNode;
}

export default function MonitorProvider({ wsUrl, children }: MonitorProviderProps) {
  const monitor = useRelayMonitor(wsUrl);
  const syncState = useMonitorStore((s) => s.syncState);
  const reset = useMonitorStore((s) => s.reset);

  // 훅 state → store 동기화
  useEffect(() => {
    syncState({
      callStatus: monitor.callStatus,
      captions: monitor.captions,
      callDuration: monitor.callDuration,
      error: monitor.error,
      snapshot: monitor.snapshot,
    });
  }, [monitor.callStatus, monitor.captions, monitor.callDuration, monitor.error, monitor.snapshot, syncState]);

  // 언마운트 시 store 초기화 (관전 소켓은 useRelayWebSocket cleanup이 닫음 → 통화는 유지)
  useEffect(() => {
    return () => {
      reset();
    };
  }, [reset]);

  return <>{children}</>;
}
