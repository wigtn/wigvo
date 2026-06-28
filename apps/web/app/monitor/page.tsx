'use client';

// /monitor — 진행 중 통화 목록 (부스 운영자용).
// GET /api/calls (owner-scoped) 4초 폴링, CALLING/IN_PROGRESS만 필터. 클릭 → /monitor/{id}.
// 탭 비활성 시 폴링 일시정지 (PRD M5: 과거 auth 폭주 회피). 401 → /login.

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, Radio, ChevronRight } from 'lucide-react';
import type { Call } from '@/shared/types';

const POLL_MS = 4000;
const ACTIVE_STATUSES = new Set(['CALLING', 'IN_PROGRESS']);

const STATUS_LABEL: Record<string, string> = {
  CALLING: '연결 중',
  IN_PROGRESS: '통화 중',
};

export default function MonitorListPage() {
  const router = useRouter();
  const [calls, setCalls] = useState<Call[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      // 탭 비활성: fetch 건너뛰고 재예약만 (auth 폭주 방지)
      if (typeof document !== 'undefined' && document.hidden) {
        schedule();
        return;
      }
      try {
        const res = await fetch('/api/calls');
        if (res.status === 401) {
          router.push('/login');
          return;
        }
        if (!res.ok) throw new Error('통화 목록을 불러오지 못했습니다');
        const data = (await res.json()) as { calls: Call[] };
        if (!stopped) {
          setCalls(data.calls.filter((c) => ACTIVE_STATUSES.has(c.status)));
          setError(null);
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : '오류가 발생했습니다');
      } finally {
        if (!stopped) {
          setLoading(false);
          schedule();
        }
      }
    }

    function schedule() {
      if (stopped) return;
      timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [router]);

  return (
    <div className="min-h-screen bg-[#070B14] px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-3xl">
        <div className="mb-8 flex items-center gap-3">
          <Radio className="size-7 text-teal-400" />
          <div>
            <h1 className="text-2xl font-bold">관전 모니터</h1>
            <p className="text-sm text-slate-400">진행 중인 통역 통화를 선택해 실시간으로 관전합니다</p>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-7 animate-spin text-slate-500" />
          </div>
        ) : error ? (
          <div className="rounded-2xl border border-red-500/40 bg-red-500/10 px-6 py-5 text-red-200">{error}</div>
        ) : calls.length === 0 ? (
          <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/60 px-6 py-16 text-center text-slate-500">
            진행 중인 통화가 없습니다
            <p className="mt-1 text-xs text-slate-600">통화가 시작되면 자동으로 나타납니다 (4초마다 갱신)</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {calls.map((call) => {
              const src = call.sourceLanguage?.toUpperCase() ?? '--';
              const tgt = call.targetLanguage?.toUpperCase() ?? '--';
              return (
                <li key={call.id}>
                  <button
                    onClick={() => router.push(`/monitor/${call.id}`)}
                    className="flex w-full items-center gap-4 rounded-2xl border border-[#1E293B] bg-[#0B1220]/70 px-5 py-4 text-left transition-colors hover:border-teal-500/50 hover:bg-[#0B1220]"
                  >
                    <span className="flex size-2.5 shrink-0 items-center">
                      <span className="size-2.5 animate-pulse rounded-full bg-teal-400 shadow-[0_0_10px_rgba(45,212,191,0.7)]" />
                    </span>
                    <div className="flex items-center gap-2 text-lg font-bold">
                      <span>{src}</span>
                      <span className="text-teal-400">↔</span>
                      <span>{tgt}</span>
                    </div>
                    <div className="flex-1 truncate text-sm text-slate-400">
                      {call.targetName || call.targetPhone || '상대 미지정'}
                    </div>
                    <span className="shrink-0 rounded-full border border-slate-600 bg-slate-800/60 px-3 py-1 text-xs font-medium text-slate-300">
                      {STATUS_LABEL[call.status] ?? call.status}
                    </span>
                    <ChevronRight className="size-5 shrink-0 text-slate-500" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
