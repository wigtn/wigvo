'use client';

import { Phone } from 'lucide-react';

interface CallStatusBarProps {
  callStatus: 'idle' | 'connecting' | 'waiting' | 'connected' | 'ended';
  callDuration: number;
  targetName?: string | null;
  callMode: 'agent' | 'relay';
}

function formatDuration(seconds: number): string {
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  const ss = String(seconds % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

function getStatusLabel(status: CallStatusBarProps['callStatus']): string {
  switch (status) {
    case 'idle':
      return '\uB300\uAE30\uC911';
    case 'connecting':
      return '\uC5F0\uACB0 \uC911';
    case 'waiting':
      return '\uC5F0\uACB0 \uC911';
    case 'connected':
      return '\uC5F0\uACB0\uB428';
    case 'ended':
      return '\uC885\uB8CC';
  }
}

export default function CallStatusBar({
  callStatus,
  callDuration,
  targetName,
  callMode,
}: CallStatusBarProps) {
  const isActive = callStatus === 'connected' || callStatus === 'waiting';

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-[#E2E8F0]/80 bg-white/12">
      <div className="flex items-center gap-3">
        <div
          className={`flex h-8 w-8 items-center justify-center rounded-lg ${
            isActive ? 'bg-teal-50' : 'bg-[#F1F5F9]'
          }`}
        >
          <Phone
            className={`size-4 ${
              isActive ? 'text-teal-600' : 'text-[#94A3B8]'
            }`}
          />
        </div>
        <div>
          <div className="flex items-center gap-2">
            {targetName && (
              <span className="text-sm font-semibold text-[#0F172A]">
                {targetName}
              </span>
            )}
            <span
              className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                callMode === 'agent'
                  ? 'bg-purple-50 text-purple-600'
                  : 'bg-blue-50 text-blue-600'
              }`}
            >
              {callMode === 'agent' ? 'AI' : 'Relay'}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {isActive && (
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-teal-500 animate-pulse" />
            )}
            <span className="text-xs text-[#94A3B8]">
              {getStatusLabel(callStatus)}
            </span>
          </div>
        </div>
      </div>

      <span className="font-mono text-lg font-bold tabular-nums text-[#0F172A]">
        {formatDuration(callDuration)}
      </span>
    </div>
  );
}
