'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowLeft,
  BarChart3,
  Bot,
  Mic,
  PhoneOff,
  Sparkles,
} from 'lucide-react';

function formatDuration(seconds: number): string {
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  const ss = String(Math.floor(seconds % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

export default function CallPreviewPage() {
  const router = useRouter();
  const [seconds, setSeconds] = useState(18);
  const [muted, setMuted] = useState(false);
  const [showMetrics, setShowMetrics] = useState(false);

  useEffect(() => {
    const id = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const orbClass = useMemo(() => {
    if (muted) return 'bg-[#9AA7BC]';
    return 'bg-[#0B1324]';
  }, [muted]);

  return (
    <div className="page-shell page-center">
      <div className="page-card max-w-xl p-4 md:p-5">
        <div className="rounded-2xl glass-surface overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/70">
            <button
              onClick={() => router.push('/')}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-[#5A6D84] hover:text-[#1F3048]"
            >
              <ArrowLeft className="size-3.5" />
              Back
            </button>
            <div className="text-xs font-semibold text-[#0B1324]">Call UI Preview</div>
            <div className="text-xs text-[#6A7C94]">{formatDuration(seconds)}</div>
          </div>

          <div className="px-5 pt-6 pb-5">
            <div className="mb-4 flex items-center justify-center">
              <div className={`w-36 h-36 rounded-full ${orbClass} shadow-[0_12px_28px_rgba(8,23,55,0.28)]`} />
            </div>

            <div className="text-center mb-5">
              <p className="text-sm font-semibold text-[#0B1324]">Connected to Recipient</p>
              <p className="text-xs text-[#7A8AA0] mt-1">
                This is a visual preview without backend connection.
              </p>
            </div>

            {showMetrics && (
              <div className="mb-4 rounded-xl border border-white/75 bg-white/55 px-3 py-2 text-xs text-[#4A5D76]">
                <p>Session A Avg: 740ms</p>
                <p>Session B Avg: 910ms</p>
              </div>
            )}

            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowMetrics((v) => !v)}
                className="h-10 w-10 rounded-xl border border-white/80 bg-white/60 text-[#4A5D76] hover:bg-white/80 flex items-center justify-center"
              >
                <BarChart3 className="size-4" />
              </button>
              <button
                onClick={() => setMuted((v) => !v)}
                className="h-10 px-4 rounded-xl border border-white/80 bg-white/60 text-[#2B3B52] hover:bg-white/80 inline-flex items-center gap-1.5 text-sm"
              >
                <Mic className="size-4" />
                {muted ? 'Unmute' : 'Mute'}
              </button>
              <button
                className="ml-auto h-10 px-4 rounded-xl bg-[#0B1324] text-white hover:bg-[#13203A] inline-flex items-center gap-1.5 text-sm"
              >
                <PhoneOff className="size-4" />
                End
              </button>
            </div>

            <div className="mt-4 rounded-xl border border-white/75 bg-white/52 px-3 py-2">
              <div className="flex items-center gap-1.5 text-[11px] text-[#60738B]">
                <Bot className="size-3.5" />
                Agent active
              </div>
              <p className="mt-1 text-xs text-[#3F5068]">
                I&apos;ll make a direct call for you. Where would you like to call?
              </p>
              <div className="mt-2 inline-flex items-center gap-1 text-[11px] text-[#5B6E86]">
                <Sparkles className="size-3" />
                Realtime translation enabled
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
