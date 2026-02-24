'use client';

import { useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import type { CallMode, CommunicationMode } from '@/shared/call-types';
import { useRelayCall } from '@/hooks/useRelayCall';
import CallStatusBar from './CallStatusBar';
import LiveCaptionPanel from './LiveCaptionPanel';
import { PhoneOff, Send, Mic, MicOff, MessageSquare, Bot } from 'lucide-react';

interface RealtimeCallViewProps {
  callId: string;
  relayWsUrl: string;
  callMode: CallMode;
  communicationMode?: CommunicationMode;
  targetName?: string | null;
  onCallEnd?: () => void;
}

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

export default function RealtimeCallView({
  callId,
  relayWsUrl,
  callMode,
  communicationMode = 'voice_to_voice',
  targetName,
  onCallEnd,
}: RealtimeCallViewProps) {
  const t = useTranslations('call');
  const relay = useRelayCall(communicationMode);
  const [textInput, setTextInput] = useState('');

  const BadgeIcon = modeBadgeIcon[communicationMode];
  const badgeLabel = t(`modeBadge.${COMM_MODE_KEYS[communicationMode]}`);

  const quickReplies = [
    { label: t('quickReplyYes'), value: t('quickReplyYesValue') },
    { label: t('quickReplyNo'), value: t('quickReplyNoValue') },
    { label: t('quickReplyWait'), value: t('quickReplyWaitValue') },
    { label: t('quickReplyRepeat'), value: t('quickReplyRepeatValue') },
  ];

  // Start the call on mount
  const startedRef = useState(() => {
    relay.startCall(callId, relayWsUrl, callMode);
    return true;
  })[0];
  void startedRef;

  const handleEndCall = useCallback(() => {
    relay.endCall();
    onCallEnd?.();
  }, [relay, onCallEnd]);

  const handleSendText = useCallback((text?: string) => {
    const msg = text ?? textInput.trim();
    if (!msg) return;
    relay.sendText(msg);
    setTextInput('');
  }, [textInput, relay]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSendText();
      }
    },
    [handleSendText],
  );

  const isActive = relay.callStatus !== 'ended';

  // --- Voice to Voice layout ---
  const renderVoiceToVoice = () => (
    <>
      {/* Main area: speaking visualizer */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="flex-1 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            {relay.isMuted ? (
              <>
                <div className="w-20 h-20 rounded-full bg-[#CBD5E1] flex items-center justify-center">
                  <MicOff className="size-8 text-white" />
                </div>
                <span className="text-sm text-[#94A3B8]">{t('muted')}</span>
              </>
            ) : relay.isRecording ? (
              <>
                <div className="w-20 h-20 rounded-full bg-blue-500 animate-pulse flex items-center justify-center">
                  <Mic className="size-8 text-white" />
                </div>
                <span className="text-sm text-blue-600 font-medium">{t('speaking')}</span>
              </>
            ) : (
              <>
                <div className="w-10 h-10 rounded-full bg-blue-500 flex items-center justify-center">
                  <Mic className="size-5 text-white" />
                </div>
                <span className="text-sm text-[#64748B]">{t('listening')}</span>
              </>
            )}
          </div>
        </div>

        {/* Bottom 30%: compact captions */}
        <div className="h-[30%] min-h-0 border-t border-[#E2E8F0]">
          <LiveCaptionPanel
            captions={relay.captions}
            translationState={relay.translationState}
            compact
          />
        </div>
      </div>

      {/* Audio controls: mute + end call */}
      {isActive && (
        <div className="flex items-center gap-3 px-4 py-3 border-t border-[#E2E8F0]">
          <button
            onClick={relay.toggleMute}
            className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-medium transition-all ${
              relay.isMuted
                ? 'bg-red-50 text-red-600 border border-red-200 hover:bg-red-100'
                : 'bg-[#F1F5F9] text-[#334155] border border-[#E2E8F0] hover:bg-[#E2E8F0]'
            }`}
          >
            {relay.isMuted ? (
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
          <button
            onClick={handleEndCall}
            className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-red-600"
          >
            <PhoneOff className="size-4" />
            {t('endCall')}
          </button>
        </div>
      )}
    </>
  );

  // --- Text to Voice layout ---
  const renderTextToVoice = () => (
    <>
      {/* Caption area: AI responses (top 60%) */}
      <div className="flex-1 min-h-0">
        <LiveCaptionPanel
          captions={relay.captions}
          translationState={relay.translationState}
        />
      </div>

      {isActive && (
        <>
          {/* Quick reply chips */}
          <div className="flex items-center gap-2 px-4 py-2 border-t border-[#E2E8F0] overflow-x-auto">
            {quickReplies.map((reply) => (
              <button
                key={reply.label}
                onClick={() => handleSendText(reply.value)}
                className="shrink-0 rounded-full border border-[#E2E8F0] bg-[#F8FAFC] px-3 py-1.5 text-xs font-medium text-[#334155] transition-colors hover:bg-[#E2E8F0]"
              >
                {reply.label}
              </button>
            ))}
          </div>

          {/* Text input */}
          <div className="flex items-center gap-2 px-4 py-3 border-t border-[#E2E8F0]">
            <input
              type="text"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t('sendMessage')}
              className="flex-1 rounded-xl border border-[#E2E8F0] px-3 py-2 text-sm text-[#334155] placeholder:text-[#CBD5E1] focus:outline-none focus:ring-1 focus:ring-[#0F172A]"
            />
            <button
              onClick={() => handleSendText()}
              disabled={!textInput.trim()}
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#0F172A] text-white transition-colors hover:bg-[#1E293B] disabled:opacity-40"
            >
              <Send className="size-4" />
            </button>
          </div>

          {/* End call */}
          <div className="px-4 py-3 border-t border-[#E2E8F0]">
            <button
              onClick={handleEndCall}
              className="w-full flex items-center justify-center gap-2 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-red-600"
            >
              <PhoneOff className="size-4" />
              {t('endCall')}
            </button>
          </div>
        </>
      )}
    </>
  );

  // --- Full Agent layout ---
  const renderFullAgent = () => (
    <>
      {/* AI status card + captions */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* AI status card */}
        <div className="px-4 py-4 border-b border-[#E2E8F0] bg-[#F8FAFC]">
          <div className="flex items-center gap-3 rounded-xl bg-white border border-[#E2E8F0] px-4 py-3">
            <div className="w-10 h-10 rounded-full bg-[#0F172A] flex items-center justify-center">
              <Bot className="size-5 text-white" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-medium text-[#1E293B]">{t('aiHandling')}</p>
              <p className="text-xs text-[#94A3B8]">{t('aiHandlingHint')}</p>
            </div>
            <div className="w-2 h-2 rounded-full bg-teal-500 animate-pulse" />
          </div>
        </div>

        {/* Caption area: AI <-> recipient conversation */}
        <div className="flex-1 min-h-0">
          <LiveCaptionPanel
            captions={relay.captions}
            translationState={relay.translationState}
          />
        </div>
      </div>

      {isActive && (
        <>
          {/* Info message + end call */}
          <div className="px-4 py-2 border-t border-[#E2E8F0] bg-[#F8FAFC]">
            <p className="text-xs text-center text-[#94A3B8]">
              {t('aiNoIntervention')}
            </p>
          </div>
          <div className="px-4 py-3 border-t border-[#E2E8F0]">
            <button
              onClick={handleEndCall}
              className="w-full flex items-center justify-center gap-2 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-red-600"
            >
              <PhoneOff className="size-4" />
              {t('endCall')}
            </button>
          </div>
        </>
      )}
    </>
  );

  // Select the right renderer based on communication mode
  const renderContent = () => {
    switch (communicationMode) {
      case 'voice_to_voice':
        return renderVoiceToVoice();
      case 'text_to_voice':
        return renderTextToVoice();
      case 'full_agent':
        return renderFullAgent();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white rounded-xl border border-[#E2E8F0] overflow-hidden">
      {/* Status Bar */}
      <CallStatusBar
        callStatus={relay.callStatus}
        callDuration={relay.callDuration}
        targetName={targetName}
        callMode={callMode}
      />

      {/* Mode Badge */}
      <div className="flex items-center gap-1.5 px-4 py-1.5 border-b border-[#E2E8F0] bg-[#F8FAFC]">
        <BadgeIcon className="size-3 text-[#64748B]" />
        <span className="text-[10px] font-medium text-[#64748B]">{badgeLabel}</span>
      </div>

      {/* Error display */}
      {relay.error && (
        <div className="px-4 py-2 bg-red-50 border-t border-red-100">
          <p className="text-xs text-red-600">{relay.error}</p>
        </div>
      )}

      {/* Mode-specific content */}
      {renderContent()}
    </div>
  );
}
