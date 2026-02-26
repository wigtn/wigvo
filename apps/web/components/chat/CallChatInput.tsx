'use client';

import { useState, useCallback, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { Send } from 'lucide-react';

interface CallChatInputProps {
  onSend: (text: string) => void;
  onTypingStart?: () => void;
  disabled?: boolean;
}

export default function CallChatInput({ onSend, onTypingStart, disabled }: CallChatInputProps) {
  const t = useTranslations('call');
  const [textInput, setTextInput] = useState('');
  const typingSentRef = useRef(false);

  const quickReplies = [
    { label: t('quickReplyYes'), value: t('quickReplyYesValue') },
    { label: t('quickReplyNo'), value: t('quickReplyNoValue') },
    { label: t('quickReplyWait'), value: t('quickReplyWaitValue') },
    { label: t('quickReplyRepeat'), value: t('quickReplyRepeatValue') },
  ];

  const handleChange = useCallback(
    (value: string) => {
      setTextInput(value);
      if (value.length > 0 && !typingSentRef.current && onTypingStart) {
        typingSentRef.current = true;
        onTypingStart();
      }
    },
    [onTypingStart],
  );

  const handleSend = useCallback(
    (text?: string) => {
      const msg = text ?? textInput.trim();
      if (!msg) return;
      onSend(msg);
      setTextInput('');
      typingSentRef.current = false;
    },
    [textInput, onSend],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="shrink-0 border-t border-[#E2E8F0]">
      {/* Quick reply chips */}
      <div className="flex items-center gap-2 px-4 py-2 overflow-x-auto">
        {quickReplies.map((reply) => (
          <button
            key={reply.label}
            onClick={() => handleSend(reply.value)}
            disabled={disabled}
            className="shrink-0 rounded-full border border-[#E2E8F0] bg-[#F8FAFC] px-3 py-1.5 text-xs font-medium text-[#334155] transition-colors hover:bg-[#E2E8F0] disabled:opacity-40"
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
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={t('sendMessage')}
          className="flex-1 rounded-xl border border-[#E2E8F0] px-3 py-2 text-base md:text-sm text-[#334155] placeholder:text-[#CBD5E1] focus:outline-none focus:ring-1 focus:ring-[#0F172A] disabled:opacity-40"
        />
        <button
          onClick={() => handleSend()}
          disabled={!textInput.trim() || disabled}
          className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#0F172A] text-white transition-colors hover:bg-[#1E293B] disabled:opacity-40"
        >
          <Send className="size-4" />
        </button>
      </div>
    </div>
  );
}
