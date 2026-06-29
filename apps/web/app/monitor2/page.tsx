'use client';

// /monitor2 — /monitor 리스트의 변형. ACTIVITY를 고정 카탈로그로 보는 실험 라우트.
// 동작은 /monitor와 동일하나 클릭 → /monitor2/{id} (카탈로그 관전 화면).

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, Radio, ChevronRight } from 'lucide-react';
import type { Call } from '@/shared/types';

const POLL_MS = 4000;
const ACTIVE_STATUSES = new Set(['CALLING', 'IN_PROGRESS']);

const ENDED_STATUSES = new Set(['COMPLETED', 'FAILED']);

const STATUS_LABEL: Record<string, string> = {
  CALLING: 'Connecting',
  IN_PROGRESS: 'In progress',
  COMPLETED: 'Ended',
  FAILED: 'Failed',
};

export default function Monitor2ListPage() {
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
        if (!res.ok) throw new Error('Failed to load calls');
        const data = (await res.json()) as { calls: Call[] };
        if (!stopped) {
          setCalls(data.calls);
          setError(null);
        }
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : 'Something went wrong');
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
            <h1 className="text-2xl font-bold">Observer Monitor</h1>
            <p className="text-sm text-slate-400">Select a call to watch the live interpretation</p>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-7 animate-spin text-slate-500" />
          </div>
        ) : error ? (
          <div className="rounded-2xl border border-red-500/40 bg-red-500/10 px-6 py-5 text-red-200">{error}</div>
        ) : (
          (() => {
            const active = calls.filter((c) => ACTIVE_STATUSES.has(c.status));
            const ended = calls.filter((c) => ENDED_STATUSES.has(c.status));

            const Row = (call: Call, live: boolean) => {
              const src = call.sourceLanguage?.toUpperCase() ?? '--';
              const tgt = call.targetLanguage?.toUpperCase() ?? '--';
              return (
                <li key={call.id}>
                  <button
                    onClick={() => router.push(`/monitor2/${call.id}`)}
                    className={`flex w-full items-center gap-4 rounded-2xl border px-5 py-4 text-left transition-colors ${
                      live
                        ? 'border-[#1E293B] bg-[#0B1220]/70 hover:border-teal-500/50 hover:bg-[#0B1220]'
                        : 'border-[#1E293B]/60 bg-[#0B1220]/40 opacity-80 hover:opacity-100 hover:border-slate-600'
                    }`}
                  >
                    <span className="flex size-2.5 shrink-0 items-center">
                      <span
                        className={`size-2.5 rounded-full ${
                          live ? 'animate-pulse bg-teal-400 shadow-[0_0_10px_rgba(45,212,191,0.7)]' : 'bg-slate-600'
                        }`}
                      />
                    </span>
                    <div className="flex items-center gap-2 text-lg font-bold">
                      <span>{src}</span>
                      <span className={live ? 'text-teal-400' : 'text-slate-500'}>↔</span>
                      <span>{tgt}</span>
                    </div>
                    <div className="flex-1 truncate text-sm text-slate-400">
                      {call.targetName || call.targetPhone || 'No recipient'}
                    </div>
                    <span className="shrink-0 rounded-full border border-slate-600 bg-slate-800/60 px-3 py-1 text-xs font-medium text-slate-300">
                      {STATUS_LABEL[call.status] ?? call.status}
                    </span>
                    <ChevronRight className="size-5 shrink-0 text-slate-500" />
                  </button>
                </li>
              );
            };

            if (active.length === 0 && ended.length === 0) {
              return (
                <div className="rounded-2xl border border-[#1E293B] bg-[#0B1220]/60 px-6 py-16 text-center text-slate-500">
                  No calls
                  <p className="mt-1 text-xs text-slate-600">Calls appear here automatically (refreshes every 4s)</p>
                </div>
              );
            }

            return (
              <div className="flex flex-col gap-8">
                <section>
                  <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-teal-400/80">
                    Live ({active.length})
                  </h2>
                  {active.length > 0 ? (
                    <ul className="flex flex-col gap-3">{active.map((c) => Row(c, true))}</ul>
                  ) : (
                    <p className="rounded-xl border border-[#1E293B] bg-[#0B1220]/40 px-5 py-6 text-center text-sm text-slate-600">
                      No ongoing calls
                    </p>
                  )}
                </section>

                {ended.length > 0 && (
                  <section>
                    <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
                      Recently ended ({ended.length})
                    </h2>
                    <ul className="flex flex-col gap-3">{ended.map((c) => Row(c, false))}</ul>
                  </section>
                )}
              </div>
            );
          })()
        )}
      </div>
    </div>
  );
}
