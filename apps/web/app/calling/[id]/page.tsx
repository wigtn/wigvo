'use client';

import { useParams, useRouter } from 'next/navigation';
import { useState, useEffect, useRef } from 'react';
import { useCallPolling } from '@/hooks/useCallPolling';
import CallingStatus from '@/components/call/CallingStatus';
import { Loader2, AlertTriangle, RefreshCw, Home } from 'lucide-react';

export default function CallingPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { call, loading, error } = useCallPolling(id);
  const [elapsed, setElapsed] = useState(0);
  const hasNavigatedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isTerminalRef = useRef(false);

  // 경과 시간 카운터
  useEffect(() => {
    timerRef.current = setInterval(() => {
      if (!isTerminalRef.current) {
        setElapsed((prev) => prev + 1);
      }
    }, 1000);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, []);

  // 종료 상태시 자동 이동
  useEffect(() => {
    if (!call) return;
    if (hasNavigatedRef.current) return;

    const isTerminal = call.status === 'COMPLETED' || call.status === 'FAILED';
    if (isTerminal) {
      isTerminalRef.current = true;
      hasNavigatedRef.current = true;

      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }

      setTimeout(() => {
        router.push(`/result/${id}`);
      }, 1000);
    }
  }, [call, id, router]);

  if (error) {
    return (
      <div className="page-center">
        <div className="page-card mx-auto flex w-full max-w-md flex-col items-center gap-5 px-5 py-10 text-center">
          <div className="w-14 h-14 rounded-2xl bg-red-50 flex items-center justify-center">
            <AlertTriangle className="size-6 text-red-500" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-[#0F172A]">연결 오류</h2>
            <p className="mt-1.5 text-sm text-[#94A3B8]">{error}</p>
          </div>
          <div className="flex w-full flex-col gap-2">
            <button
              onClick={() => window.location.reload()}
              className="w-full h-11 rounded-xl flex items-center justify-center gap-2 text-sm font-medium bg-[#0F172A] text-white hover:bg-[#1E293B] transition-all shadow-sm"
            >
              <RefreshCw className="size-4" />
              다시 시도
            </button>
            <button
              onClick={() => router.push('/')}
              className="w-full h-11 rounded-xl flex items-center justify-center gap-2 text-sm font-medium text-[#94A3B8] hover:text-[#64748B] hover:bg-[#F1F5F9] transition-all"
            >
              <Home className="size-4" />
              홈으로 돌아가기
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-center">
      <div className="mx-auto w-full max-w-md">
        {loading && !call ? (
          <div className="page-card flex flex-col items-center gap-4 py-16">
            <Loader2 className="size-8 text-[#0F172A] animate-spin" />
            <p className="text-sm text-[#94A3B8]">통화 정보를 불러오는 중...</p>
          </div>
        ) : (
          <CallingStatus call={call} elapsed={elapsed} />
        )}
      </div>
    </div>
  );
}
