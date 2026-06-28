'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { useTranslations } from 'next-intl';
import { useRelayCallStore } from '@/hooks/useRelayCallStore';
import { useDashboard } from '@/hooks/useDashboard';
import CallingStatus from './CallingStatus';
import CallStatusBar from './CallStatusBar';
import CallSummaryPanel from './CallSummaryPanel';
import MetricsPanel from './MetricsPanel';
import EventLogPanel from './EventLogPanel';
import {
  PhoneOff,
  Mic,
  MicOff,
  MessageSquare,
  Bot,
  Loader2,
  BarChart3,
  ScrollText,
} from 'lucide-react';
import type { CommunicationMode } from '@/shared/call-types';
import { isDemoMode } from '@/lib/demo';

const Orb = dynamic(() => import('@/components/ui/Orb'), { ssr: false });

const modeBadgeIcon: Record<CommunicationMode, typeof Mic> = {
  voice_to_voice: Mic,
  text_to_voice: MessageSquare,
  full_agent: Bot,
};

const COMM_MODE_KEYS: Record<CommunicationMode, string> = {
  voice_to_voice: 'voiceToVoice',
  text_to_voice: 'textToVoice',
  full_agent: 'fullAgent',
};

function getOrbHue(isRecording: boolean, isPlaying: boolean, isMuted: boolean): number {
  if (isMuted) return 0; // gray
  if (isRecording) return 220; // blue
  if (isPlaying) return 160; // teal
  return 120; // green (idle)
}

export default function CallEffectPanel() {
  const t = useTranslations('call');
  const tc = useTranslations('common');
  const { callingCallId, callingCommunicationMode, resetDashboard } = useDashboard();

  const {
    callData: call,
    callDataLoading: loading,
    callDataError: pollError,
    refetchCallData,
    callStatus,
    callDuration,
    callMode,
    isMuted,
    isRecording,
    isPlaying,
    error,
    metrics,
    endCall,
    toggleMute,
  } = useRelayCallStore();

  const [showMetrics, setShowMetrics] = useState(false);
  const [showEventLog, setShowEventLog] = useState(false);

  // WebSocket reports ended → immediately refetch call data from server
  const prevCallStatusRef = useRef(callStatus);
  useEffect(() => {
    if (callStatus === 'ended' && prevCallStatusRef.current !== 'ended') {
      refetchCallData?.();
    }
    prevCallStatusRef.current = callStatus;
  }, [callStatus, refetchCallData]);

  const communicationMode = callingCommunicationMode ?? 'voice_to_voice';
  const BadgeIcon = modeBadgeIcon[communicationMode];
  const badgeLabel = t(`modeBadge.${COMM_MODE_KEYS[communicationMode]}`);

  // 통화 완료 → 새 대화 시작 (useChat() 없이 직접 리셋)
  // NOTE: useCallback은 반드시 조건부 return 전에 호출 (React Rules of Hooks)
  const handleNewChat = useCallback(() => {
    localStorage.removeItem('currentConversationId');
    localStorage.removeItem('currentCommunicationMode');
    localStorage.removeItem('currentSourceLang');
    localStorage.removeItem('currentTargetLang');
    resetDashboard();
    window.location.href = '/';
  }, [resetDashboard]);

  // Relay/Agent 모드 판별
  // Demo preview에서는 polling 타이밍과 무관하게 realtime UI(orb)를 강제 노출.
  const demoModeActive = isDemoMode();
  const isRealtimeMode = demoModeActive || call?.callMode === 'agent' || call?.callMode === 'relay';
  const hasRelayWsUrl = demoModeActive || !!call?.relayWsUrl;

  // Legacy 폴백: isRealtimeMode가 false면 기존 CallingStatus
  if (!isRealtimeMode || !hasRelayWsUrl) {
    if (loading && !call) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-3">
          <Loader2 className="size-6 text-[#0F172A] animate-spin" />
          <p className="text-sm text-[#94A3B8]">{t('loadingCallInfo')}</p>
        </div>
      );
    }

    if (pollError) {
      return (
        <div className="flex items-center justify-center h-full">
          <div className="text-center px-6">
            <p className="text-sm text-red-500 mb-2">{pollError}</p>
            <button
              onClick={() => window.location.reload()}
              className="text-sm text-[#64748B] hover:text-[#334155] underline"
            >
              {tc('retry')}
            </button>
          </div>
        </div>
      );
    }

    return (
      <div className="flex items-center justify-center h-full">
        <CallingStatus call={call} elapsed={callDuration} />
      </div>
    );
  }

  const isTerminal = !demoModeActive && (call?.status === 'COMPLETED' || call?.status === 'FAILED');
  const endedAtBootstrap = demoModeActive && callStatus === 'ended' && callDuration === 0;
  const isActive = (callStatus !== 'ended' || endedAtBootstrap) && !isTerminal;
  const isEnded = !endedAtBootstrap && (callStatus === 'ended' || isTerminal);

  const handleEndCall = () => {
    endCall?.();
  };

  if (isEnded && call) {
    return <CallSummaryPanel call={call} onNewChat={handleNewChat} />;
  }

  return (
    <div className="flex flex-col h-full bg-transparent overflow-hidden">
      {/* Status Bar */}
      <CallStatusBar
        callStatus={callStatus}
        callDuration={callDuration}
        targetName={call?.targetName}
        callMode={callMode}
      />

      {/* Mode Badge */}
      <div className="flex items-center gap-1.5 px-4 py-1.5 border-b border-[#E2E8F0]/80 bg-white/14">
        <BadgeIcon className="size-3 text-[#64748B]" />
        <span className="text-[10px] font-medium text-[#64748B]">{badgeLabel}</span>
      </div>

      {/* Error display */}
      {error && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-100">
          <p className="text-xs text-red-600">{error}</p>
        </div>
      )}

      {/* Orb area */}
      <div className="flex-1 flex flex-col items-center justify-center gap-4 px-6">
        <div className="w-56 h-56">
          <Orb
            hue={getOrbHue(isRecording, isPlaying, isMuted)}
            hoverIntensity={0.5}
            rotateOnHover={true}
            forceHoverState={isRecording || isPlaying}
            backgroundColor="transparent"
          />
        </div>

        {/* Voice status text */}
        <div className="text-center">
          {isMuted ? (
            <div className="flex items-center gap-1.5 text-sm text-[#94A3B8]">
              <MicOff className="size-3.5" />
              {t('muted')}
            </div>
          ) : isRecording ? (
            <p className="text-sm text-blue-600 font-medium">{t('speaking')}</p>
          ) : isPlaying ? (
            <p className="text-sm text-teal-600 font-medium">
              {communicationMode === 'full_agent' ? t('aiHandling') : t('listening')}
            </p>
          ) : (
            <p className="text-sm text-[#64748B]">{t('listening')}</p>
          )}
        </div>

        {/* AI Agent status card */}
        {communicationMode === 'full_agent' && (
          <div className="w-full max-w-xs rounded-xl bg-white border border-[#E2E8F0] px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-[#0F172A] flex items-center justify-center">
                <Bot className="size-4 text-white" />
              </div>
              <div className="flex-1">
                <p className="text-xs font-medium text-[#1E293B]">{t('aiHandling')}</p>
                <p className="text-[10px] text-[#94A3B8]">{t('aiHandlingHint')}</p>
              </div>
              <div className="w-2 h-2 rounded-full bg-teal-500 animate-pulse" />
            </div>
          </div>
        )}
      </div>

      {/* Metrics Panel */}
      {showMetrics && <MetricsPanel metrics={metrics} />}

      {/* Event Log Panel */}
      {showEventLog && <EventLogPanel />}

      {/* Controls */}
      {isActive && (
        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-t border-[#E2E8F0]">
          {/* Metrics toggle */}
          <button
            onClick={() => setShowMetrics(!showMetrics)}
            className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-all ${
              showMetrics
                ? 'bg-[#0F172A] text-white'
                : 'bg-[#F1F5F9] text-[#334155] border border-[#E2E8F0] hover:bg-[#E2E8F0]'
            }`}
          >
            <BarChart3 className="size-3.5" />
          </button>

          {/* Event Log toggle */}
          <button
            onClick={() => setShowEventLog(!showEventLog)}
            className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-all ${
              showEventLog
                ? 'bg-[#0F172A] text-white'
                : 'bg-[#F1F5F9] text-[#334155] border border-[#E2E8F0] hover:bg-[#E2E8F0]'
            }`}
          >
            <ScrollText className="size-3.5" />
          </button>

          {/* Mute button (voice modes only) */}
          {communicationMode === 'voice_to_voice' && (
            <button
              onClick={() => toggleMute?.()}
              className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-medium transition-all ${
                isMuted
                  ? 'bg-red-50 text-red-600 border border-red-200 hover:bg-red-100'
                  : 'bg-[#F1F5F9] text-[#334155] border border-[#E2E8F0] hover:bg-[#E2E8F0]'
              }`}
            >
              {isMuted ? (
                <>
                  <MicOff className="size-3.5" />
                  {t('unmute')}
                </>
              ) : (
                <>
                  <Mic className="size-3.5" />
                  {t('mute')}
                </>
              )}
            </button>
          )}

          {/* End call button */}
          <button
            onClick={handleEndCall}
            className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-red-600"
          >
            <PhoneOff className="size-4" />
            {t('endCall')}
          </button>
        </div>
      )}
    </div>
  );
}
