'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import HistoryList from '@/components/call/HistoryList';
import { Loader2, AlertTriangle, RefreshCw, Home, Phone } from 'lucide-react';
import type { Call } from '@/shared/types';

export default function HistoryPage() {
  const router = useRouter();
  const [calls, setCalls] = useState<Call[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchedRef = useRef(false);

  useEffect(() => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;

    async function fetchCalls() {
      try {
        const res = await fetch('/api/calls');

        if (res.status === 401) {
          router.push('/login');
          return;
        }

        if (!res.ok) {
          setError('기록을 불러오는 데 실패했습니다.');
          setLoading(false);
          return;
        }

        const data = await res.json();
        setCalls(data.calls || []);
        setLoading(false);
      } catch {
        setError('네트워크 오류가 발생했습니다.');
        setLoading(false);
      }
    }

    fetchCalls();
  }, [router]);

  return (
    <div className="page-shell">
      <div className="mx-auto w-full max-w-3xl px-5 py-8">
        {/* 헤더 */}
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[#F1F5F9] flex items-center justify-center">
              <Phone className="size-4 text-[#0F172A]" />
            </div>
            <h1 className="text-xl font-bold text-[#0F172A] tracking-tight">통화 기록</h1>
          </div>
          <button
            onClick={() => router.push('/')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[#64748B] hover:text-[#334155] hover:bg-[#F1F5F9] transition-all"
          >
            <Home className="size-3.5" />
            홈
          </button>
        </div>

        {/* 콘텐츠 */}
        {loading ? (
          <div className="page-card flex flex-col items-center gap-4 py-20">
            <Loader2 className="size-6 text-[#0F172A] animate-spin" />
            <p className="text-sm text-[#94A3B8]">기록을 불러오는 중...</p>
          </div>
        ) : error ? (
          <div className="page-card flex flex-col items-center gap-4 py-20">
            <div className="w-14 h-14 rounded-2xl bg-red-50 flex items-center justify-center">
              <AlertTriangle className="size-6 text-red-500" />
            </div>
            <div className="text-center">
              <p className="font-medium text-red-600 text-sm">{error}</p>
              <p className="mt-1 text-xs text-[#94A3B8]">인터넷 연결을 확인해주세요.</p>
            </div>
            <button
              onClick={() => window.location.reload()}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-medium bg-white border border-[#E2E8F0] text-[#64748B] hover:bg-[#F8FAFC] transition-all"
            >
              <RefreshCw className="size-3.5" />
              새로고침
            </button>
          </div>
        ) : (
          <div className="page-card p-4">
            <HistoryList calls={calls} />
          </div>
        )}
      </div>
    </div>
  );
}
