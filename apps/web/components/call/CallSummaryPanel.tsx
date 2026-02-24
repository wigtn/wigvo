'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type Call } from '@/shared/types';
import {
  PhoneOff,
  MapPin,
  Calendar,
  Clock,
  Scissors,
  FileText,
  Timer,
  Zap,
  Mic,
  MessageSquare,
  Bot,
  MessageCircle,
  List,
} from 'lucide-react';
import type { CommunicationMode } from '@/shared/call-types';
import { useDashboard } from '@/hooks/useDashboard';

interface CallSummaryPanelProps {
  call: Call;
  onNewChat: () => void;
}

const MODE_KEYS: Record<CommunicationMode, string> = {
  voice_to_voice: 'voiceToVoice',
  text_to_voice: 'textToVoice',
  full_agent: 'fullAgent',
};

const MODE_ICONS: Record<CommunicationMode, typeof Mic> = {
  voice_to_voice: Mic,
  text_to_voice: MessageSquare,
  full_agent: Bot,
};

function formatDuration(seconds: number): string {
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  const ss = String(Math.floor(seconds % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-';
  try {
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parseInt(parts[0], 10);
      const month = parseInt(parts[1], 10);
      const day = parseInt(parts[2], 10);
      const date = new Date(year, month - 1, day);
      const days = ['일', '월', '화', '수', '목', '금', '토'];
      return `${year}년 ${month}월 ${day}일 (${days[date.getDay()]})`;
    }
    return dateStr;
  } catch {
    return dateStr;
  }
}

function formatTime(timeStr: string | null): string {
  if (!timeStr) return '-';
  try {
    const parts = timeStr.split(':');
    if (parts.length < 2) return timeStr;
    const hours = parseInt(parts[0], 10);
    const minutes = parseInt(parts[1], 10);
    if (isNaN(hours) || isNaN(minutes)) return timeStr;
    const period = hours < 12 ? '오전' : '오후';
    const displayHours = hours % 12 || 12;
    return minutes > 0 ? `${period} ${displayHours}시 ${minutes}분` : `${period} ${displayHours}시`;
  } catch {
    return timeStr;
  }
}

export default function CallSummaryPanel({ call, onNewChat }: CallSummaryPanelProps) {
  const router = useRouter();
  const t = useTranslations('summary');

  const commMode = (call.communicationMode ?? 'voice_to_voice') as CommunicationMode;
  const ModeIcon = MODE_ICONS[commMode];

  return (
    <div className="h-full overflow-y-auto styled-scrollbar px-4 py-6">
      <div className="flex flex-col gap-5">
        {/* Status Header */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#F1F5F9] flex items-center justify-center shrink-0">
            <PhoneOff className="size-5 text-[#64748B]" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-[#0F172A]">
              {t('callCompleted')}
            </h2>
            {call.targetName && (
              <p className="text-xs text-[#94A3B8]">{call.targetName}</p>
            )}
          </div>
        </div>

        {/* Stats Badges */}
        <div className="flex flex-wrap gap-2">
          {call.durationS != null && call.durationS > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#F1F5F9] px-3 py-1.5 text-xs font-medium text-[#334155]">
              <Timer className="size-3" />
              {formatDuration(call.durationS)}
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#F1F5F9] px-3 py-1.5 text-xs font-medium text-[#334155]">
            <ModeIcon className="size-3" />
            {t(`mode.${MODE_KEYS[commMode]}`)}
          </span>
          {call.totalTokens != null && call.totalTokens > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#F1F5F9] px-3 py-1.5 text-xs font-medium text-[#334155]">
              <Zap className="size-3" />
              {call.totalTokens.toLocaleString()} {t('tokens')}
            </span>
          )}
        </div>

        {/* Call Info Card */}
        {(call.targetName || call.parsedDate || call.parsedTime || call.parsedService) && (
          <div className="rounded-2xl bg-white border border-[#E2E8F0] shadow-[0_1px_3px_rgba(0,0,0,0.04)] overflow-hidden">
            <div className="px-4 py-3 border-b border-[#E2E8F0]">
              <h3 className="text-xs font-semibold text-[#0F172A] uppercase tracking-wider">{t('callInfo')}</h3>
            </div>
            <div className="px-4 py-3.5 space-y-3.5">
              {call.targetName && (
                <SummaryRow icon={<MapPin className="size-3.5" />} label={t('place')} value={call.targetName} />
              )}
              {call.parsedDate && (
                <SummaryRow icon={<Calendar className="size-3.5" />} label={t('date')} value={formatDate(call.parsedDate)} />
              )}
              {call.parsedTime && (
                <SummaryRow icon={<Clock className="size-3.5" />} label={t('time')} value={formatTime(call.parsedTime)} />
              )}
              {call.parsedService && (
                <SummaryRow icon={<Scissors className="size-3.5" />} label={t('service')} value={call.parsedService} />
              )}
            </div>
          </div>
        )}

        {/* AI Summary */}
        {call.summary && (
          <div className="rounded-2xl bg-white border border-[#E2E8F0] shadow-[0_1px_3px_rgba(0,0,0,0.04)] overflow-hidden">
            <div className="px-4 py-3 border-b border-[#E2E8F0] flex items-center gap-2">
              <FileText className="size-3.5 text-[#94A3B8]" />
              <h3 className="text-xs font-semibold text-[#0F172A] uppercase tracking-wider">{t('aiSummary')}</h3>
            </div>
            <div className="px-4 py-3.5">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#334155]">
                {call.summary}
              </p>
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex flex-col gap-2 pt-1">
          <button
            onClick={onNewChat}
            className="w-full h-11 rounded-xl flex items-center justify-center gap-2 text-sm font-medium bg-[#0F172A] text-white hover:bg-[#1E293B] transition-all"
          >
            <MessageCircle className="size-4" />
            {t('newChat')}
          </button>
          <button
            onClick={() => {
              const { resetCalling, setActiveMenu } = useDashboard.getState();
              resetCalling();
              setActiveMenu('conversations');
              router.push('/');
            }}
            className="w-full h-11 rounded-xl flex items-center justify-center gap-2 text-sm font-medium bg-white border border-[#E2E8F0] text-[#334155] hover:bg-[#F8FAFC] transition-all"
          >
            <List className="size-4" />
            {t('viewHistory')}
          </button>
        </div>
      </div>
    </div>
  );
}

/* Sub-component */
function SummaryRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-7 h-7 rounded-lg bg-[#F1F5F9] flex items-center justify-center shrink-0 text-[#64748B]">
        {icon}
      </div>
      <div>
        <p className="text-[10px] text-[#94A3B8] uppercase tracking-wider">{label}</p>
        <p className="text-sm font-medium text-[#0F172A]">{value}</p>
      </div>
    </div>
  );
}
