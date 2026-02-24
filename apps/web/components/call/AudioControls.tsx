'use client';

import { Mic, MicOff } from 'lucide-react';
import { useTranslations } from 'next-intl';

interface AudioControlsProps {
  isMuted: boolean;
  isSpeaking: boolean;
  onToggleMute: () => void;
}

export default function AudioControls({
  isMuted,
  isSpeaking,
  onToggleMute,
}: AudioControlsProps) {
  const t = useTranslations('call');

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-t border-[#E2E8F0]">
      {/* VAD indicator */}
      <div className="flex items-center gap-2 flex-1">
        <div
          className={`w-2.5 h-2.5 rounded-full transition-colors ${
            isMuted
              ? 'bg-[#CBD5E1]'
              : isSpeaking
                ? 'bg-blue-500 animate-pulse'
                : 'bg-teal-500'
          }`}
        />
        <span className="text-xs text-[#64748B]">
          {isMuted
            ? t('muted')
            : isSpeaking
              ? t('speaking')
              : t('listening')}
        </span>
      </div>

      {/* Mute toggle */}
      <button
        onClick={onToggleMute}
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
    </div>
  );
}
