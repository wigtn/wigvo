'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  Loader2,
  AlertTriangle,
  RefreshCw,
  MessageSquare,
  Clock,
  Phone,
  PhoneCall,
  PhoneOff,
  ArrowLeft,
  User,
  Bot,
  MapPin,
  Calendar,
  Scissors,
  Info,
  Users,
  FileText,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ConversationSummary } from '@/hooks/useDashboard';
import type { Conversation, CollectedData, Message, Call, TranscriptEntry } from '@/shared/types';

// ---------------------------------------------------------------------------
// 날짜 포맷 헬퍼
// ---------------------------------------------------------------------------
function formatDate(dateStr: string) {
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) {
    return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
  } else if (days === 1) {
    return '어제';
  } else if (days < 7) {
    return `${days}일 전`;
  }
  return date.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' });
}

function formatFullDate(dateStr: string) {
  const date = new Date(dateStr);
  return date.toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ---------------------------------------------------------------------------
// 상태 관련 헬퍼
// ---------------------------------------------------------------------------
function getStatusIcon(status: string) {
  switch (status) {
    case 'COMPLETED':
      return <PhoneOff className="size-3.5 text-[#64748B]" />;
    case 'CALLING':
    case 'IN_PROGRESS':
      return <Phone className="size-3.5 text-[#0F172A] animate-pulse" />;
    case 'READY':
      return <Phone className="size-3.5 text-blue-500" />;
    default:
      return <Clock className="size-3.5 text-[#CBD5E1]" />;
  }
}

function getStatusLabel(status: string) {
  switch (status) {
    case 'COLLECTING':
      return '정보 수집 중';
    case 'READY':
      return '통화 준비 완료';
    case 'CALLING':
      return '통화 중';
    case 'IN_PROGRESS':
      return '통화 진행 중';
    case 'COMPLETED':
      return '완료';
    case 'CANCELLED':
      return '취소됨';
    default:
      return status;
  }
}

function getStatusBadgeClass(status: string) {
  switch (status) {
    case 'COMPLETED':
      return 'bg-teal-50 text-teal-600';
    case 'READY':
      return 'bg-blue-50 text-blue-600';
    case 'CALLING':
    case 'IN_PROGRESS':
      return 'bg-amber-50 text-amber-600';
    case 'CANCELLED':
      return 'bg-red-50 text-red-500';
    default:
      return 'bg-[#F1F5F9] text-[#64748B]';
  }
}

// ---------------------------------------------------------------------------
// 시나리오 타입 라벨
// ---------------------------------------------------------------------------
function getScenarioLabel(type: string | null) {
  switch (type) {
    case 'RESERVATION':
      return '예약';
    case 'INQUIRY':
      return '문의';
    case 'AS_REQUEST':
      return 'AS 요청';
    default:
      return null;
  }
}

// ===========================================================================
// ConversationHistoryPanel
// ===========================================================================
export default function ConversationHistoryPanel() {
  const router = useRouter();

  // ── 대화 목록 상태 ──
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const listFetchedRef = useRef(false);

  // ── 선택된 대화 상세 ──
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Conversation | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [callResult, setCallResult] = useState<Call | null>(null);

  // ── 모바일 뷰 전환 ──
  const [mobileView, setMobileView] = useState<'list' | 'detail'>('list');

  // 실효 상태: CALLING이지만 통화 완료/실패된 경우 COMPLETED로 표시
  const effectiveDetailStatus =
    detail?.status === 'CALLING' &&
    callResult &&
    (callResult.status === 'COMPLETED' || callResult.status === 'FAILED')
      ? 'COMPLETED'
      : detail?.status;

  // ────────────────────────────────────────────────────────────────
  // 대화 목록 fetch
  // ────────────────────────────────────────────────────────────────
  const fetchConversations = useCallback(async () => {
    setListLoading(true);
    setListError(null);

    try {
      const res = await fetch('/api/conversations');

      if (res.status === 401) {
        router.push('/login');
        return;
      }

      if (!res.ok) {
        setListError('대화 기록을 불러오는 데 실패했습니다.');
        setListLoading(false);
        return;
      }

      const data = await res.json();
      setConversations(data.conversations || []);
      setListLoading(false);
    } catch {
      setListError('네트워크 오류가 발생했습니다.');
      setListLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (listFetchedRef.current) return;
    listFetchedRef.current = true;
    fetchConversations();
  }, [fetchConversations]);

  // ────────────────────────────────────────────────────────────────
  // 대화 상세 fetch
  // ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setCallResult(null);
      return;
    }

    let cancelled = false;

    async function fetchDetail() {
      setDetailLoading(true);
      setDetailError(null);
      setCallResult(null);

      try {
        const res = await fetch(`/api/conversations/${selectedId}`);

        if (!cancelled) {
          if (res.status === 401) {
            router.push('/login');
            return;
          }

          if (!res.ok) {
            setDetailError('대화 상세를 불러오는 데 실패했습니다.');
            setDetailLoading(false);
            return;
          }

          const data: Conversation = await res.json();
          setDetail(data);
          setDetailLoading(false);

          // 연관된 통화 결과 fetch
          try {
            const callsRes = await fetch('/api/calls');
            if (!cancelled && callsRes.ok) {
              const callsData = await callsRes.json();
              const relatedCall = (callsData.calls || []).find(
                (c: Call) => c.conversationId === selectedId,
              );
              setCallResult(relatedCall || null);
            }
          } catch {
            // 통화 결과 로드 실패는 무시 (핵심 기능 아님)
          }
        }
      } catch {
        if (!cancelled) {
          setDetailError('네트워크 오류가 발생했습니다.');
          setDetailLoading(false);
        }
      }
    }

    fetchDetail();

    return () => {
      cancelled = true;
    };
  }, [selectedId, router]);

  // ────────────────────────────────────────────────────────────────
  // 항목 선택
  // ────────────────────────────────────────────────────────────────
  const handleSelect = (id: string) => {
    setSelectedId(id);
    setMobileView('detail');
  };

  const handleBack = () => {
    setMobileView('list');
  };

  const handleRefresh = () => {
    listFetchedRef.current = false;
    fetchConversations();
  };

  // ────────────────────────────────────────────────────────────────
  // Render
  // ────────────────────────────────────────────────────────────────
  return (
    <div className="h-full flex flex-col lg:flex-row bg-[#EEF3F9]">
      {/* ───── 좌측: 대화 리스트 ───── */}
      <div
        className={cn(
          'lg:w-[330px] lg:border-r lg:border-white/70 flex flex-col bg-white/60 backdrop-blur-xl',
          // 모바일: list일 때만 보임
          mobileView === 'list' ? 'flex-1 lg:flex-none' : 'hidden lg:flex',
        )}
      >
        {/* 리스트 헤더 */}
        <div className="shrink-0 px-5 py-4 border-b border-white/70">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-[#F1F5F9] flex items-center justify-center">
                <MessageSquare className="size-4 text-[#0F172A]" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-[#0F172A] tracking-tight">
                  통화 기록
                </h2>
                {!listLoading && !listError && (
                  <p className="text-[10px] text-[#94A3B8] mt-0.5">
                    총 {conversations.length}건
                  </p>
                )}
              </div>
            </div>
            <button
              onClick={handleRefresh}
              disabled={listLoading}
              className="p-1.5 rounded-lg text-[#64748B] hover:text-[#334155] hover:bg-[#F1F5F9] transition-all disabled:opacity-50"
            >
              <RefreshCw className={cn('size-3.5', listLoading && 'animate-spin')} />
            </button>
          </div>
        </div>

        {/* 리스트 본문 */}
        <div className="flex-1 overflow-y-auto styled-scrollbar">
          {listLoading ? (
            <div className="px-4 py-6 space-y-2">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="animate-pulse">
                  <div className="h-16 bg-[#F1F5F9] rounded-xl" />
                </div>
              ))}
            </div>
          ) : listError ? (
            <div className="flex flex-col items-center gap-3 py-16 px-4">
              <div className="w-12 h-12 rounded-2xl bg-red-50 flex items-center justify-center">
                <AlertTriangle className="size-5 text-red-500" />
              </div>
              <p className="text-sm text-red-600 font-medium text-center">{listError}</p>
              <button
                onClick={handleRefresh}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white border border-[#E2E8F0] text-[#64748B] hover:bg-[#F8FAFC] transition-all"
              >
                <RefreshCw className="size-3" />
                다시 시도
              </button>
            </div>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 px-4">
              <div className="w-12 h-12 rounded-2xl bg-[#F1F5F9] flex items-center justify-center">
                <MessageSquare className="size-5 text-[#CBD5E1]" />
              </div>
              <div className="text-center">
                <p className="text-sm font-medium text-[#94A3B8]">대화 기록이 없습니다</p>
                <p className="text-xs text-[#CBD5E1] mt-1">새 대화를 시작해보세요</p>
              </div>
            </div>
          ) : (
            <div className="px-1.5 py-2 space-y-0.5">
              {conversations.map((conv) => (
                <button
                  key={conv.id}
                  onClick={() => handleSelect(conv.id)}
                  className={cn(
                    'w-full text-left px-2.5 py-3 rounded-xl transition-all',
                    'hover:bg-[#F8FAFC]',
                    selectedId === conv.id && 'bg-[#F1F5F9]',
                  )}
                >
                  <div className="flex items-start gap-2.5">
                    {getStatusIcon(conv.status)}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium text-[#0F172A] truncate">
                          {conv.targetName || '새 대화'}
                        </p>
                        <span className="text-[10px] text-[#CBD5E1] shrink-0">
                          {formatDate(conv.createdAt)}
                        </span>
                      </div>
                      <p className="text-xs text-[#94A3B8] truncate mt-0.5">
                        {conv.lastMessage}
                      </p>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ───── 우측: 대화 상세 ───── */}
      <div
        className={cn(
          'flex-1 flex flex-col bg-white/28 backdrop-blur-sm',
          // 모바일: detail일 때만 보임
          mobileView === 'detail' ? 'flex' : 'hidden lg:flex',
        )}
      >
        {selectedId === null ? (
          /* 선택 안 됨 - 빈 상태 */
          <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4">
            <div className="w-14 h-14 rounded-2xl bg-[#F1F5F9] flex items-center justify-center">
              <MessageSquare className="size-6 text-[#CBD5E1]" />
            </div>
            <p className="text-sm font-medium text-[#94A3B8]">대화를 선택해주세요</p>
            <p className="text-xs text-[#CBD5E1]">좌측 목록에서 대화를 선택하면 상세 내용을 확인할 수 있습니다</p>
          </div>
        ) : detailLoading ? (
          /* 로딩 */
          <div className="flex-1 flex flex-col items-center justify-center gap-3">
            <Loader2 className="size-6 text-[#0F172A] animate-spin" />
            <p className="text-sm text-[#94A3B8]">대화를 불러오는 중...</p>
          </div>
        ) : detailError ? (
          /* 에러 */
          <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4">
            <div className="w-12 h-12 rounded-2xl bg-red-50 flex items-center justify-center">
              <AlertTriangle className="size-5 text-red-500" />
            </div>
            <p className="text-sm font-medium text-red-600">{detailError}</p>
            <button
              onClick={() => setSelectedId(selectedId)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white border border-[#E2E8F0] text-[#64748B] hover:bg-[#F8FAFC] transition-all"
            >
              <RefreshCw className="size-3" />
              다시 시도
            </button>
          </div>
        ) : detail ? (
          /* 대화 상세 */
          <>
            {/* 상세 헤더 */}
            <div className="shrink-0 px-5 py-4 bg-white/58 backdrop-blur-xl border-b border-white/70">
              <div className="flex items-center gap-3">
                {/* 모바일 뒤로가기 */}
                <button
                  onClick={handleBack}
                  className="lg:hidden p-1.5 -ml-1.5 rounded-lg hover:bg-[#F1F5F9] transition-colors"
                >
                  <ArrowLeft className="size-4 text-[#64748B]" />
                </button>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-[#0F172A] truncate">
                      {detail.collectedData?.target_name || '대화 상세'}
                    </h2>
                    <span
                      className={cn(
                        'shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold',
                        getStatusBadgeClass(effectiveDetailStatus!),
                      )}
                    >
                      {getStatusLabel(effectiveDetailStatus!)}
                    </span>
                  </div>
                  <p className="text-[11px] text-[#94A3B8] mt-0.5">
                    {formatFullDate(detail.createdAt)}
                  </p>
                </div>
              </div>
            </div>

            {/* 콘텐츠 영역 — 3섹션: Summary → AI 대화 → 통화 내용 */}
            <div className="flex-1 overflow-y-auto styled-scrollbar">
              <div className="w-full px-3 md:px-4 lg:px-5 py-5 space-y-6">

                {/* ── Section 1: Summary ── */}
                {((detail.collectedData && hasCollectedData(detail.collectedData)) || callResult) && (
                  <section>
                    <SectionHeader icon={Info} title="Summary" />
                    <div className="space-y-3 mt-3">
                      {detail.collectedData && hasCollectedData(detail.collectedData) && (
                        <CollectedDataCard data={detail.collectedData} />
                      )}
                      {callResult && (
                        <CallInfoCard call={callResult} />
                      )}
                    </div>
                  </section>
                )}

                {/* ── Section 2: AI 대화 ── */}
                {detail.messages && detail.messages.length > 0 ? (
                  <section>
                    <SectionHeader icon={MessageSquare} title="AI 대화" />
                    <div className="space-y-4 mt-3">
                      {detail.messages.map((msg) => (
                        <MessageBubble key={msg.id} message={msg} />
                      ))}
                    </div>
                  </section>
                ) : !callResult ? (
                  <div className="text-center py-10">
                    <p className="text-sm text-[#94A3B8]">메시지가 없습니다</p>
                  </div>
                ) : null}

                {/* ── Section 3: 통화 내용 ── */}
                {callResult?.transcriptBilingual && callResult.transcriptBilingual.length > 0 && (
                  <section>
                    <SectionHeader icon={Phone} title="통화 내용" />
                    <div className="space-y-1.5 mt-3">
                      {callResult.transcriptBilingual.map((entry, idx) => (
                        <TranscriptBubble key={idx} entry={entry} />
                      ))}
                    </div>
                  </section>
                )}

              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

// ===========================================================================
// MessageBubble (인라인 - ChatMessage 스타일 참고)
// ===========================================================================
function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';

  return (
    <div className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      {/* 어시스턴트 아바타 */}
      {!isUser && (
        <div className="shrink-0 w-7 h-7 rounded-lg bg-[#F1F5F9] flex items-center justify-center mr-2 mt-1">
          <Bot className="size-3.5 text-[#64748B]" />
        </div>
      )}

      <div className="max-w-[75%]">
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap',
            isUser
              ? 'bg-[#0F172A] text-white rounded-br-md'
              : 'bg-white border border-[#E2E8F0] shadow-sm text-[#334155] rounded-bl-md',
          )}
        >
          {!isUser && (
            <div className="text-[10px] text-[#64748B] font-medium mb-1.5 uppercase tracking-wider">
              Agent
            </div>
          )}
          {message.content}
        </div>
        <p className={cn('text-[10px] text-[#CBD5E1] mt-1', isUser ? 'text-right' : 'text-left')}>
          {formatDate(message.createdAt)}
        </p>
      </div>

      {/* 사용자 아바타 */}
      {isUser && (
        <div className="shrink-0 w-7 h-7 rounded-lg bg-[#0F172A] flex items-center justify-center ml-2 mt-1">
          <User className="size-3.5 text-white" />
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// SectionHeader (섹션 구분 헤더)
// ===========================================================================
function SectionHeader({ icon: Icon, title }: { icon: React.ComponentType<{ className?: string }>; title: string }) {
  return (
    <div className="flex items-center gap-2 pb-2 border-b border-[#E2E8F0]">
      <Icon className="size-3.5 text-[#64748B]" />
      <span className="text-xs font-semibold text-[#0F172A] tracking-tight">{title}</span>
    </div>
  );
}

// ===========================================================================
// CollectedDataCard (수집 정보 요약 카드)
// ===========================================================================
function CollectedDataCard({ data }: { data: CollectedData }) {
  const scenarioLabel = getScenarioLabel(data.scenario_type);

  const fields = [
    { icon: MapPin, label: '대상', value: data.target_name },
    { icon: Phone, label: '전화번호', value: data.target_phone },
    { icon: Calendar, label: '일시', value: data.primary_datetime },
    { icon: Scissors, label: '서비스', value: data.service },
    { icon: User, label: '예약자', value: data.customer_name },
    { icon: Users, label: '인원', value: data.party_size ? `${data.party_size}명` : null },
    { icon: FileText, label: '특이사항', value: data.special_request },
  ].filter((f) => f.value != null);

  if (fields.length === 0) return null;

  return (
    <div className="bg-white border border-[#E2E8F0] rounded-2xl shadow-[0_1px_3px_rgba(0,0,0,0.04)] overflow-hidden">
      {/* 카드 헤더 */}
      <div className="px-4 py-3 border-b border-[#E2E8F0] flex items-center gap-2">
        <Info className="size-3.5 text-[#64748B]" />
        <span className="text-xs font-semibold text-[#0F172A]">수집 정보</span>
        {scenarioLabel && (
          <span className="ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold bg-[#F1F5F9] text-[#64748B]">
            {scenarioLabel}
          </span>
        )}
      </div>

      {/* 필드 목록 */}
      <div className="px-4 py-3 space-y-2.5">
        {fields.map((field) => {
          const Icon = field.icon;
          return (
            <div key={field.label} className="flex items-start gap-2.5">
              <Icon className="size-3.5 text-[#94A3B8] mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <span className="text-[10px] text-[#94A3B8] font-medium uppercase tracking-wider">
                  {field.label}
                </span>
                <p className="text-sm text-[#334155] mt-0.5">{field.value}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ===========================================================================
// hasCollectedData 헬퍼 - 수집된 데이터가 있는지 확인
// ===========================================================================
function hasCollectedData(data: CollectedData): boolean {
  return !!(
    data.target_name ||
    data.target_phone ||
    data.primary_datetime ||
    data.service ||
    data.customer_name ||
    data.party_size ||
    data.special_request
  );
}

// ===========================================================================
// Call Result 헬퍼 함수들
// ===========================================================================
function getCallStatusLabel(status: string): string {
  switch (status) {
    case 'PENDING':
      return '대기 중';
    case 'CALLING':
      return '전화 거는 중...';
    case 'IN_PROGRESS':
      return '통화 중...';
    case 'COMPLETED':
    case 'FAILED':
      return '통화 종료';
    default:
      return status;
  }
}

// ===========================================================================
// CallInfoCard (통화 정보 카드 — Summary 섹션용, transcript 제외)
// ===========================================================================
function CallInfoCard({ call }: { call: Call }) {
  const formatDuration = (seconds: number) => {
    const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
    const ss = String(Math.floor(seconds % 60)).padStart(2, '0');
    return `${mm}:${ss}`;
  };

  const modeLabel: Record<string, string> = {
    voice_to_voice: 'Voice to Voice',
    text_to_voice: 'Text to Voice',
    voice_to_text: 'Voice to Text',
    full_agent: 'Full Agent',
  };

  const fields = [
    { icon: Phone, label: '상태', value: getCallStatusLabel(call.status) },
    {
      icon: Clock,
      label: call.completedAt ? '완료' : '시작',
      value: formatFullDate(call.completedAt || call.createdAt),
    },
    call.durationS != null
      ? { icon: Clock, label: '통화 시간', value: formatDuration(call.durationS) }
      : null,
    call.communicationMode
      ? { icon: PhoneCall, label: '모드', value: modeLabel[call.communicationMode] || call.communicationMode }
      : null,
  ].filter(Boolean) as Array<{ icon: typeof Phone; label: string; value: string }>;

  return (
    <div className="bg-white border border-[#E2E8F0] rounded-2xl shadow-[0_1px_3px_rgba(0,0,0,0.04)] overflow-hidden">
      <div className="px-4 py-3 space-y-2.5">
        {fields.map((field) => {
          const Icon = field.icon;
          return (
            <div key={field.label} className="flex items-start gap-2.5">
              <Icon className="size-3.5 text-[#94A3B8] mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <span className="text-[10px] text-[#94A3B8] font-medium uppercase tracking-wider">
                  {field.label}
                </span>
                <p className="text-sm text-[#334155] mt-0.5">{field.value}</p>
              </div>
            </div>
          );
        })}
        {call.summary && (
          <div className="mt-1 pt-2 border-t border-[#F1F5F9]">
            <p className="text-sm text-[#334155] leading-relaxed pl-3 border-l-2 border-[#E2E8F0] italic">
              {call.summary}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ===========================================================================
// TranscriptBubble (통화 자막 버블)
// ===========================================================================
function TranscriptBubble({ entry }: { entry: TranscriptEntry }) {
  const isUser = entry.role === 'user';

  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[80%] rounded-xl px-3 py-1.5 text-xs leading-relaxed',
          isUser
            ? 'bg-[#0F172A] text-white rounded-br-sm'
            : 'bg-[#F1F5F9] text-[#334155] rounded-bl-sm',
        )}
      >
        {entry.translated_text}
      </div>
    </div>
  );
}
